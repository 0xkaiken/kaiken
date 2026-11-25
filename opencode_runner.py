"""OpenCode Go execution using credentials owned by the installed OpenCode CLI."""

import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import agent_permissions
import agent_secrets
from process_control import PLINY_TITLE_PREFIX, run_captured_process

GO_DEFAULT_MODEL = "opencode-go/glm-5.3-flash"

# Wall-clock cap for a single text request through the OpenCode CLI. Reasoning
# models routinely exceed two minutes on a long prompt; the old 120s default
# killed those calls and surfaced them as transport errors. Override with
# PLINY_OPENCODE_TIMEOUT, or per call with rt_send.py --timeout.
DEFAULT_TEXT_TIMEOUT = max(30, int(os.environ.get("PLINY_OPENCODE_TIMEOUT", "300") or 300))
_catalog_cache = None
_catalog_until = 0.0
_catalog_lock = threading.Lock()
_status_cache = None
_status_until = 0.0
_status_lock = threading.Lock()


def opencode_cli():
    cli = shutil.which("opencode2") or shutil.which("opencode")
    if not cli:
        raise FileNotFoundError("OpenCode CLI is not installed")
    if os.name == "nt" and Path(cli).suffix.lower() in {".cmd", ".bat"}:
        candidates = [Path(cli).parent / "node_modules/@opencode/cli/bin/opencode.exe"]
        if os.environ.get("APPDATA"):
            candidates.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@opencode/cli/bin/opencode.exe")
        cli = next((str(path) for path in candidates if path.is_file()), None)
        if not cli:
            raise FileNotFoundError("Cannot find the installed native OpenCode executable")
    return cli


def opencode_status():
    """Use the public CLI listing; never open its credential store."""
    global _status_cache, _status_until
    # The dashboard polls auth often; two CLI launches per poll were slow.
    with _status_lock:
        if _status_cache is not None and time.monotonic() < _status_until:
            return dict(_status_cache)
        _status_cache = _opencode_status_uncached()
        # Re-check quickly while not connected, so Refresh right after sign-in works.
        _status_until = time.monotonic() + (15 if _status_cache["configured"] else 2)
        return dict(_status_cache)


def _opencode_status_uncached():
    info = {"installed": False, "configured": False}
    try:
        cli = opencode_cli()
        info["installed"] = True
        version = subprocess.run([cli, "--version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=5,
                                 **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        info["version"] = version.stdout.strip() if version.returncode == 0 else None
        # Like the catalog and runs, must not depend on the background service: a
        # stale one holding its port made this fail and report "not connected".
        result = subprocess.run([cli, "--standalone", "auth", "list"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=8,
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        info["configured"] = result.returncode == 0 and any(
            "OpenCode Go" in line and "stored" in line for line in result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def opencode_models():
    """Every model OpenCode can run: Go, Zen and any provider the operator connected."""
    global _catalog_cache, _catalog_until
    with _catalog_lock:
        if _catalog_cache is not None and time.monotonic() < _catalog_until:
            return list(_catalog_cache)
        try:
            # Catalog reads must not depend on OpenCode's background service.
            # A stale/unreachable service used to time out and hide every model.
            result = subprocess.run([opencode_cli(), "--standalone", "models"], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", check=True, timeout=15,
                                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            models = sorted({line.strip() for line in result.stdout.splitlines()
                             if re.fullmatch(r"[A-Za-z0-9._-]+/\S+", line.strip())})
        except (OSError, subprocess.SubprocessError):
            models = []
        if models:
            _catalog_cache, _catalog_until = models, time.monotonic() + 60
        else:
            # Preserve the last successful catalog and retry transient failures
            # soon instead of caching a blank picker for a full minute.
            _catalog_until = time.monotonic() + 5
        return list(_catalog_cache or [])


def config_env(config, base=None, filter_env=True):
    """Per-run OpenCode settings via OPENCODE_CONFIG_CONTENT (merged over the user's config).

    The workshop permission profile is folded in here so every agent run gets
    the same external-directory and credential-file boundaries. Callers that
    pass an explicit ``permission`` value (the judge uses ``"deny"``) override
    the profile instead of being weakened by it.

    ``filter_env`` is true for agent runs. Server-side callers such as the
    Gauntlet judge set it false: they are not agent subprocesses, and the
    OpenCode provider resolver may legitimately need the server's environment
    keys. An agent-side caller already runs with a filtered environment, so
    the distinction cannot leak a provider key back into an agent.
    """
    profile = agent_permissions.opencode_permission_config()
    merged = agent_permissions.merge_opencode_config(profile, config)
    required_policies = profile.get("policies", [])
    if required_policies:
        policies = merged.get("policies", [])
        if not isinstance(policies, list):
            raise ValueError("OpenCode policies must be a list")
        merged["policies"] = policies + [rule for rule in required_policies if rule not in policies]
    if os.environ.get("PLINY_CU_TOKEN"):
        # Computer Use sessions accumulate screenshots; keep the window below
        # 80% so latency does not climb to 45s per turn, and preserve 15k of
        # recent conversation beside the summary (OpenCode's default).
        compaction = merged.get("compaction")
        compaction = compaction if isinstance(compaction, dict) else {}
        compaction.setdefault("auto", True)
        compaction.setdefault("buffer", "20%")
        keep = compaction.get("keep")
        keep = keep if isinstance(keep, dict) else {}
        keep.setdefault("tokens", 15000)
        compaction["keep"] = keep
        merged["compaction"] = compaction
        # A visible-workflow mission must not have app-automation MCPs in its
        # tool list: the SolidWorks run used the solidworks MCP and COM scripts
        # to build the model after launching the app through CU. The operator
        # names the servers to withhold in PLINY_CU_BLOCK_MCP.
        blocked = [name.strip() for name in (os.environ.get("PLINY_CU_BLOCK_MCP") or "").split(",")
                   if name.strip()]
        if blocked:
            mcp = merged.get("mcp")
            mcp = mcp if isinstance(mcp, dict) else {}
            for name in blocked:
                server = mcp.get(name)
                server = server if isinstance(server, dict) else {}
                server["enabled"] = False
                mcp[name] = server
            merged["mcp"] = mcp
    if base is not None:
        env = dict(base)
    elif filter_env:
        env = agent_secrets.filtered_env()
    else:
        env = dict(os.environ)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(merged)
    return env


def mcp_config(servers):
    # codemode=false exposes the tools directly instead of behind Code Mode's
    # execute(), saving a model round trip per action.
    return {"mcp": {name: {"type": "local", "command": [spec["command"], *spec["args"]],
                           "enabled": True, "codemode": False, "timeout": 180000}
                    for name, spec in servers.items()}}


def valid_model(model):
    return (isinstance(model, str) and "/" in model.strip("/") and not model.startswith("-")
            and not any(c.isspace() for c in model) and len(model) <= 200)


# Adapter prefixes that name OTHER billing pools inside OpenCode's catalog.
# Session evidence (2026-11-01): `openai/<name>` rides the stored ChatGPT
# credential and 401s when it expires; `opencode/<name>` rides the console
# funds adapter and 402s "Insufficient account funds"; `openrouter/openai/<name>`
# rides the OpenRouter key and 401s when that's dead. Only `opencode-go/`
# is the operator's Go subscription — so when it serves the same model
# name, prefer it and a run can't strand on the wrong pool.
_GO_REPAIR_PROVIDERS = {"openai", "opencode", "openrouter"}


def repair_go_route(model):
    """Rewrite connected-adapter model ids to the Go subscription twin.

    `openai/gpt-6-luna` and `opencode/gpt-6-luna` name OTHER credential
    pools the operator doesn't control well — when `opencode-go/gpt-6-luna`
    exists in the catalog, that's the pool the operator actually pays for.
    Fails open: catalog unavailable or no twin → the id passes through.
    """
    if not valid_model(model):
        return model
    parts = model.strip("/").split("/")
    if len(parts) < 2 or parts[0] == "opencode-go":
        return model
    if parts[0] not in _GO_REPAIR_PROVIDERS:
        return model
    candidate = "opencode-go/" + parts[-1]
    try:
        catalog = set(opencode_models())
    except Exception:
        return model
    return candidate if candidate in catalog else model


def run_command(model, resume=None, text_only=False):
    model = repair_go_route(model)
    if not valid_model(model):
        raise ValueError("Select an OpenCode model as provider/model (see `opencode models`)")
    # A private server keeps STOP scoped to this mission's process tree, and
    # the title is an ownership marker the orphan sweep can recognize without
    # matching unrelated OpenCode sessions.
    owner = os.environ.get("PLINY_SESSION_ID", "runtime")
    command = [opencode_cli(), "--standalone", "run", "--format", "json", "--model", model,
               "--agent", "plan" if text_only else "build",
               "--title", f"{PLINY_TITLE_PREFIX}{owner}"]
    if not text_only and agent_permissions.permission_profile() == "unrestricted":
        command.append("--auto")
    if resume:
        command += ["--session", resume]
    return command


class DashboardEvents:
    def __init__(self):
        self.session_id = None
        self.text_parts = {}
        self.failed = False
        self.tools = set()
        self.error_message = ""
        self.usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cost": 0.0}

    @property
    def answer(self):
        return "\n".join(self.text_parts.values()).strip()

    def convert(self, event):
        output = []
        sid = event.get("sessionID")
        if sid and not self.session_id:
            self.session_id = sid
            output.append({"type": "system", "subtype": "opencode_go", "session_id": sid})
        part = event.get("part") or {}
        kind = event.get("type")
        if kind == "text":
            text = part.get("text", "")
            part_id = part.get("id", "answer")
            old = self.text_parts.get(part_id, "")
            self.text_parts[part_id] = text
            delta = text[len(old):] if text.startswith(old) else text
            if delta:
                # part_id lets the agent log stitch deltas of one part back
                # into a single message before rendering it as Markdown.
                output.append({"type": "assistant", "part_id": part_id,
                               "message": {"content": [{"type": "text", "text": delta}]}})
        elif kind == "step_finish":
            # Per-step accounting for the dashboard: the CLI reports the
            # uncached input, the cache read, output/reasoning and cost.
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            self.usage["input"] += int(tokens.get("input") or 0)
            self.usage["output"] += int(tokens.get("output") or 0)
            self.usage["reasoning"] += int(tokens.get("reasoning") or 0)
            self.usage["cache_read"] += int(cache.get("read") or 0)
            self.usage["cost"] += float(part.get("cost") or 0)
        elif kind in {"tool_use", "tool_start", "tool_result"} or part.get("type") == "tool":
            state = part.get("state") or {}
            tool_id = part.get("id", "tool")
            if tool_id not in self.tools:
                self.tools.add(tool_id)
                output.append({"type": "assistant", "message": {"content": [{
                    "type": "tool_use", "id": tool_id, "name": part.get("tool", "OpenCode"),
                    "input": state.get("input", {})}]}})
            if state.get("status") in {"completed", "error"}:
                output.append({"type": "tool_result", "tool_use_id": tool_id,
                               "content": str(state.get("output") or state.get("error") or "")})
        elif kind == "error":
            self.failed = True
            error = event.get("error") or {}
            message = (error.get("data") or {}).get("message") or error.get("message") or "OpenCode request failed"
            etype = error.get("type") or ""
            self.error_message = f"{etype}: {message}" if etype else message
            output.append({"type": "result", "subtype": "error", "is_error": True, "result": message})
        return output


def complete_text(prompt, model=GO_DEFAULT_MODEL, timeout=None, pin_body=None, cwd=None):
    """Single text request for evaluators and explicit Go targets.

    ``timeout=None`` means "use DEFAULT_TEXT_TIMEOUT" — 300s by default, which
    is what a reasoning target actually needs. rt_send.py passes an explicit
    value when the caller gave --timeout.

    ``cwd`` is the working directory the CLI runs in, i.e. the workspace the
    model can read. Target calls pass a neutral directory (rt_send's
    _target_workspace); None keeps the inherited cwd for evaluators that have
    no workspace semantics attached to the answer.

    ``pin_body`` routes the request through go_temp_proxy.py and pins params
    into the request body (e.g. {"temperature": 0.0} for deterministic judge
    calls). The CLI has no temperature flag and config-based parameter
    placements are silently dropped by the transport (probes/temp_pin_probe.py,
    probes/maxtoken_probe.py, probes/variant_body_probe.py,
    probes/model_body_probe.py); the one honored control point is provider
    options.baseURL, which the proxy rides. Pinned calls are otherwise
    transparent: auth headers created by the CLI pass through and are never
    stored or logged.
    """
    if timeout is None:
        timeout = DEFAULT_TEXT_TIMEOUT
    if pin_body:
        provider = model.split("/", 1)[0]
        if provider != "opencode-go":
            raise ValueError(
                "pin_body is only implemented for the opencode-go provider; "
                f"got {provider!r}")
        from go_temp_proxy import GoTempProxy
        with GoTempProxy(pinned=pin_body) as proxy:
            env = config_env({"permission": "deny"}, filter_env=False)
            merged = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            prov = merged.setdefault("provider", {}).setdefault(provider, {})
            prov.setdefault("options", {})["baseURL"] = proxy.url
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(merged)
            result = run_captured_process(run_command(model, text_only=True),
                                          input_text=prompt, timeout=timeout, env=env,
                                          cwd=cwd)
    else:
        # OpenCode 2 ignores the old OPENCODE_PERMISSION variable; deny tools in config.
        env = config_env({"permission": "deny"}, filter_env=False)
        result = run_captured_process(run_command(model, text_only=True), input_text=prompt,
                                      timeout=timeout, env=env, cwd=cwd)
    events = DashboardEvents()
    for line in result.stdout.splitlines():
        try:
            events.convert(json.loads(line))
        except ValueError:
            continue
    if result.returncode or events.failed or not events.answer:
        # Surface the real cause (auth 401, dead key, unavailable model…)
        # instead of a generic message — the operator sees "provider.auth:
        # Request failed: 401 on openai/gpt-6-luna" and knows to switch
        # routes (e.g. opencode-go/<model>) rather than debug blind.
        if events.error_message:
            detail = events.error_message
        elif result.returncode:
            detail = f"exit code {result.returncode}"
        else:
            detail = "no answer text"
        # The CLI often prints the actionable reason on stderr (unknown
        # provider, revoked key, credit balance); keep the tail so callers
        # can classify the failure instead of re-running it.
        if result.stderr and result.stderr.strip():
            tail = " | ".join(s.strip() for s in result.stderr.strip().splitlines()[-3:])
            detail = f"{detail}; stderr: {tail[:300]}"
        raise RuntimeError(
            f"OpenCode Go request for {model} did not complete ({detail}); "
            "check its connection and model access"
        )
    return events.answer


def emit(event):
    print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--resume")
    parser.add_argument("--mcp-json", help="JSON object of extra stdio MCP servers for this run")
    args = parser.parse_args()
    prompt = sys.stdin.read()
    events = DashboardEvents()
    try:
        # Native OpenCode reads the prompt from stdin; keys stay in its own
        # credential storage rather than in arguments, app state or logs.
        import tempfile
        with tempfile.TemporaryFile() as prompt_input:
            prompt_input.write(prompt.encode("utf-8"))
            prompt_input.seek(0)
            # Refresh safeguards even when resuming from a server that loaded
            # an older module, while preserving per-session provider settings.
            config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
            if not isinstance(config, dict):
                raise ValueError("OpenCode configuration must be an object")
            if args.mcp_json:
                config = agent_permissions.merge_opencode_config(config, mcp_config(json.loads(args.mcp_json)))
            env = config_env(config)
            env["PLINY_AGENT_RUNNER_PID"] = str(os.getpid())
            process = subprocess.Popen(run_command(args.model, args.resume), stdin=prompt_input,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", env=env)
        for line in process.stdout:
            try:
                for event in events.convert(json.loads(line)):
                    emit(event)
            except ValueError:
                emit({"type": "status", "text": line.rstrip()})
        code = process.wait()
        failed = bool(code or events.failed or not events.answer)
        if events.usage["input"] or events.usage["output"]:
            emit({"type": "status", "text": "[USAGE] " + ", ".join((
                f"input={events.usage['input']}",
                f"cache_read={events.usage['cache_read']}",
                f"output={events.usage['output']}",
                f"reasoning={events.usage['reasoning']}",
                f"cost=${events.usage['cost']:.4f}",
            ))})
        emit({"type": "result", "subtype": "error" if failed else "success", "is_error": failed,
              "result": events.answer or "OpenCode Go returned no answer",
              "usage": events.usage})
        return code or int(failed)
    except (OSError, ValueError) as error:
        emit({"type": "result", "subtype": "error", "is_error": True, "result": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
