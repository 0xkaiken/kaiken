"""Run subscription-authenticated Codex and stream dashboard-compatible events."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import account_guard
import agent_permissions
import agent_secrets


def mcp_overrides(name, spec):
    """Register a stdio MCP server for this run only (no edit to config.toml)."""
    key = "mcp_servers." + name
    # JSON strings/arrays are valid TOML values; Windows paths stay escaped.
    return ["-c", f"{key}.command={json.dumps(spec['command'])}",
            "-c", f"{key}.args={json.dumps(spec['args'])}",
            "-c", f'{key}.env_vars=["PLINY_AGENT_TOKEN","PLINY_AUTH_TOKEN","PLINY_CU_TOKEN"]',
            "-c", f"{key}.startup_timeout_sec=20",
            "-c", f"{key}.tool_timeout_sec=180",
            # The mission already runs with approval_policy=never.
            "-c", f'{key}.default_tools_approval_mode="approve"']


def codex_command(model=None, resume=None, mcp=None):
    cli = shutil.which("codex")
    if not cli:
        raise FileNotFoundError("Codex CLI is not installed; run codex login first")
    command = [cli]
    if os.name == "nt" and Path(cli).suffix.lower() in {".cmd", ".bat"}:
        # Launch the npm entry point directly. No cmd.exe quoting of prompts,
        # model names, paths or configuration values is involved.
        candidates = [Path(cli).parent / "node_modules/@openai/codex/bin/codex.js"]
        if os.environ.get("APPDATA"):
            candidates.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@openai/codex/bin/codex.js")
        entry = next((path for path in candidates if path.is_file()), None)
        node = shutil.which("node")
        if not entry or not node:
            raise FileNotFoundError("Cannot find the installed Codex npm entry point or Node.js")
        command = [node, str(entry)]
    # --auto / --dangerously-skip-permissions have no Codex twin; the sandbox
    # mode is how the operator's profile reaches this runtime.
    sandbox = ('sandbox_mode="danger-full-access"'
               if agent_permissions.permission_profile() == "unrestricted"
               else 'sandbox_mode="workspace-write"')
    command += [
        "-c", 'forced_login_method="chatgpt"',
        "-c", 'model_provider="openai"',
        "-c", 'approval_policy="never"',
        "-c", sandbox,
        "-c", "sandbox_workspace_write.network_access=true",
    ]
    if sandbox.endswith('"workspace-write"'):
        roots = agent_permissions.write_roots()
        if roots:
            command += ["-c", "sandbox_workspace_write.writable_roots="
                        + json.dumps([str(root) for root in roots])]
    for name, spec in (mcp or {}).items():
        command += mcp_overrides(name, spec)
    command += ["exec"]
    if resume:
        command += ["resume", resume]
    command += ["--json", "--skip-git-repo-check"]
    if model:
        command += ["--model", model]
    return command + ["-"]


class DashboardEvents:
    def __init__(self):
        self.last_message = ""
        self.failed = False
        self.finished = False

    def convert(self, event):
        kind = event.get("type")
        if kind == "thread.started":
            return [{"type": "system", "subtype": "codex_chatgpt",
                     "session_id": event["thread_id"]}]
        if kind in {"turn.failed", "error"}:
            self.failed = True
            self.finished = True
            error = event.get("error", {})
            message = error.get("message") if isinstance(error, dict) else str(error)
            message = message or event.get("message") or "Codex turn failed"
            return [{"type": "result", "subtype": "error", "is_error": True, "result": message}]
        if kind == "turn.completed":
            self.finished = True
            return [{"type": "result", "subtype": "success", "is_error": False,
                     "result": self.last_message, "usage": event.get("usage", {})}]
        item = event.get("item", {})
        item_type = item.get("type")
        if kind == "item.completed" and item_type == "agent_message":
            self.last_message = item.get("text", "")
            return [{"type": "assistant", "message": {"content": [
                {"type": "text", "text": self.last_message}]}}]
        tools = {"command_execution": "Bash", "file_change": "Edit",
                 "mcp_tool_call": "MCP", "web_search": "WebSearch"}
        if kind == "item.started" and item_type in tools:
            detail = {key: value for key, value in item.items() if key not in {"id", "type"}}
            return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": item.get("id"),
                 "name": tools[item_type], "input": detail}]}}]
        if kind == "item.completed" and item_type in tools:
            content = item.get("aggregated_output") or item.get("result") or item.get("changes") or item.get("status", "")
            return [{"type": "tool_result", "tool_use_id": item.get("id"),
                     "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}]
        return []


def emit(event):
    # Compact JSON matches the existing session reader's event detection.
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--resume")
    parser.add_argument("--mcp-json", help="JSON object of extra stdio MCP servers for this run")
    args = parser.parse_args()
    # The seat this runner may spend, if any. A claude or opencode session
    # must not reach into the operator's ChatGPT subscription, and an
    # unauthorized session must not start a seat at all.
    try:
        account_guard.require_runner("codex")
    except account_guard.SubscriptionDenied as denied:
        emit({"type": "result", "subtype": "error", "is_error": True, "result": str(denied)})
        return 86
    prompt = sys.stdin.read()
    events = DashboardEvents()
    # Subscription-only: credentials remain owned by Codex CLI. Never read or
    # forward its token file, and never fall back to API-key billing. The
    # scrub also removes every other provider key the server may hold.
    env = agent_secrets.filtered_env()
    try:
        with tempfile.TemporaryFile() as prompt_input:
            prompt_input.write(prompt.encode("utf-8"))
            prompt_input.seek(0)
            process = subprocess.Popen(
                codex_command(args.model, args.resume, json.loads(args.mcp_json) if args.mcp_json else None), stdin=prompt_input,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                emit({"type": "status", "text": line.rstrip()})
                continue
            for translated in events.convert(event):
                emit(translated)
        code = process.wait()
        if not events.finished:
            emit({"type": "result", "subtype": "error", "is_error": True,
                  "result": f"Codex exited without a completed turn (exit {code})"})
            return code or 1
        return code or int(events.failed)
    except (OSError, ValueError) as error:
        emit({"type": "result", "subtype": "error", "is_error": True, "result": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
