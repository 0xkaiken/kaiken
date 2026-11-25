"""Least-privilege defaults for the three agent runtimes.

Profiles are selected with ``PLINY_AGENT_PERMISSIONS``:

* ``workshop`` (default) — the agent may read and edit its working directory,
  run commands and use the network, but does not get blanket permission
  bypasses. Claude Code runs in auto mode with prompts routed nowhere (a
  prompt that would need a human is denied instead of hanging); OpenCode gets
  an explicit permission document that denies edits outside the workshop and
  temp directories, keeps the install tree readable so red-team transports
  still run, and refuses reads of ``.env`` / ``secrets.json``. Desktop
  Computer Use missions additionally receive the operator's Desktop, Downloads
  and Documents as write roots (``PLINY_WRITE_ROOTS``), because their Save /
  New Project dialogs land there; the command server withholds them with
  ``PLINY_CU_WRITE_ROOTS=0``.
* ``unrestricted`` — the historical behaviour (Claude's
  ``--dangerously-skip-permissions``, OpenCode ``--auto``). Opt in only for a
  trusted machine or a mission that genuinely needs it.

Codex already runs with ``approval_policy=never`` inside a
``workspace-write`` sandbox; that remains the default because it is the
mechanism that lets headless missions work.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_WORKSHOP = BASE_DIR / "state" / "workshop"
PROFILE_ENV = "PLINY_AGENT_PERMISSIONS"
# Extra directories this agent process may write to. The command server sets
# it per session; desktop Computer Use missions point it at the operator's
# visible folders so Save/New Project does not have to detour through %TEMP%.
WRITE_ROOTS_ENV = "PLINY_WRITE_ROOTS"
DEFAULT_USER_FOLDERS = ("Desktop", "Downloads", "Documents")

_ALIASES = {
    "": "workshop",
    "default": "workshop",
    "workshop": "workshop",
    "safe": "workshop",
    "restricted": "workshop",
    "unrestricted": "unrestricted",
    "trusted": "unrestricted",
    "bypass": "unrestricted",
    "full": "unrestricted",
}


def permission_profile(value: Optional[str] = None) -> str:
    """Return the normalized profile; unknown values fall back to workshop."""
    raw = (value if value is not None else os.environ.get(PROFILE_ENV, "")).strip().lower()
    return _ALIASES.get(raw, "workshop")


def _posix(path) -> str:
    return Path(path).resolve().as_posix()


def user_content_roots(home=None) -> list[Path]:
    """The operator's visible content folders that exist on this machine.

    Desktop Computer Use missions act in real applications, whose Save and
    New Project dialogs default here. Only those missions wire them in.
    """
    base = Path(home or os.environ.get("USERPROFILE") or Path.home())
    return [path.resolve() for name in DEFAULT_USER_FOLDERS
            if (path := base / name).is_dir()]


def write_roots() -> list[Path]:
    """Extra write roots for this agent process, from ``PLINY_WRITE_ROOTS``.

    One path per ``os.pathsep`` (``;`` on Windows). Missing paths are ignored:
    a typo must not silently become an allow rule.
    """
    raw = (os.environ.get(WRITE_ROOTS_ENV) or "").strip()
    if not raw:
        return []
    roots = []
    for part in raw.split(os.pathsep):
        part = part.strip().strip('"')
        if not part:
            continue
        try:
            path = Path(part).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots


def subscription_cli_deny_args() -> list[str]:
    """Claude Code deny rules that keep an agent off the subscription CLIs.

    The seat belongs to the operator. An agent that types ``claude -p ...``
    in its own shell spends that seat without being asked to, so the CLI
    binaries are denied outright. The rules name the binaries, not Python, so
    ``python rt_send.py ...`` — the sanctioned target-evaluation transport —
    still runs, and ``codex_runner`` is launched by the server, not by the
    agent's shell.

    Set ``PLINY_AGENT_SUBSCRIPTION_CLI_DENY=0`` to drop the rules. The rule in
    AGENTS.md says "under no circumstances", so that switch is for a machine
    where the operator has made a different call — not a per-mission toggle.
    """
    if (os.environ.get("PLINY_AGENT_SUBSCRIPTION_CLI_DENY", "1") or "").strip() != "1":
        return []
    rules = {
        "permissions": {
            "deny": [
                "Bash(claude:*)",
                "Bash(claude.exe:*)",
                "Bash(codex:*)",
                "Bash(codex.exe:*)",
                # Operator proof for local CLI calls — never the agent's to read.
                f"Read(//{_posix(BASE_DIR / 'state' / 'operator.token')})",
            ]
        }
    }
    return ["--settings", json.dumps(rules, separators=(",", ":"))]


def claude_permission_args(
    profile: Optional[str] = None,
    computer_use: bool = False,
    write_roots: Iterable[Path] = (),
) -> list[str]:
    """Claude Code flags for the selected profile.

    ``computer_use`` is accepted so a session that controls the real desktop
    can keep its MCP tools; the auto permission mode still decides per action
    rather than bypassing every check. ``write_roots`` extends the workspace
    with the folders a desktop mission was granted (``--add-dir``).

    The subscription-CLI deny rules ride along in both profiles: file-system
    trust and account ownership are different questions.
    """
    if permission_profile(profile) == "unrestricted":
        return ["--dangerously-skip-permissions", *subscription_cli_deny_args()]
    directories = [flag for root in write_roots for flag in ("--add-dir", str(root))]
    return [
        "--permission-mode", "auto",
        "--permission-prompts", "none",
        *subscription_cli_deny_args(),
        *directories,
    ]


def opencode_permission_config(
    profile: Optional[str] = None,
    workshop: Optional[Path] = None,
    home: Optional[Path] = None,
) -> dict:
    """Config fragment for OpenCode's ``permission`` document.

    The working directory is the workshop. Temp directories are allowed
    because screenshots, payload files and browser artifacts legitimately live
    there. The install tree is readable because red-team transports are
    invoked as ``python <PLINY_HOME>/rt_send.py``, but edits outside the
    workshop are denied and credential files are never readable.

    ``PLINY_WRITE_ROOTS`` adds the per-session folders a desktop Computer Use
    mission was granted. The agent already reaches them through the shell, so
    denying the write tool only forced a %TEMP% copy per save; the read denies
    for credentials stay in force.
    """
    if permission_profile(profile) == "unrestricted":
        return {}
    workshop_path = _posix(workshop or os.environ.get("PLINY_WORKSHOP") or DEFAULT_WORKSHOP)
    home_path = _posix(home or os.environ.get("PLINY_HOME") or BASE_DIR)
    temp_path = _posix(tempfile.gettempdir())
    external = {"*": "deny"}
    for root in {workshop_path, home_path, temp_path}:
        external[f"{root}/**"] = "allow"
        external[f"{root}/*"] = "allow"
    edit = {
        "*": "allow",
        f"{home_path}/**": "deny",
        f"{workshop_path}/**": "allow",
        f"{temp_path}/**": "allow",
    }
    for root in write_roots():
        granted = _posix(root)
        for rule in (f"{granted}/**", f"{granted}/*"):
            external[rule] = "allow"
            edit[rule] = "allow"
    return {
        # OpenCode V2 hard policies: process cleanup belongs to the operator's
        # dashboard, not to an agent guessing which Python PID is an orphan.
        "policies": [
            {"action": "tool.use", "resource": resource, "effect": "deny"}
            for resource in (
                "shell:*Stop-Process*", "shell:*taskkill*", "shell:tskill*",
                "shell:spps*", "shell:kill*", "shell:pkill*", "shell:killall*",
                # A gauntlet agent must not start/restart the dashboard or
                # revive a completed run with a temp shim.  The operator owns
                # the server lifecycle; this is deterrence, not an OS boundary.
                "shell:*server.py*", "shell:*install_windows*",
                "shell:*start-windows*", "shell:*server_shim*",
            )
        ],
        "permission": {
            "*": "allow",
            "external_directory": external,
            "read": {
                "*": "allow",
                "*.env": "deny",
                "*.env.*": "deny",
                "**/.env": "deny",
                "**/.env.*": "deny",
                "**/secrets.json": "deny",
                "**/state/secrets.json": "deny",
                # The operator's proof of identity for local CLI calls. Reading
                # it would let an agent impersonate the operator on privileged
                # routes. Best-effort: the OS user is the same either way.
                "**/operator.token": "deny",
                "**/agent_keys/**": "deny",
                "**/agent_keys/*": "deny",
            },
            "edit": edit,
        }
    }


def merge_opencode_config(base: Optional[dict], extra: Optional[dict]) -> dict:
    """Shallow-merge a config fragment without mutating either input."""
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged
