"""Credential boundaries for agent subprocesses.

The server process owns provider keys. They arrive from three places:

1. the process environment (``.env`` is loaded into it by ``server.py``),
2. ``state/secrets.json`` written by the dashboard secrets card,
3. the host environment the operator launched the server with.

Agent subprocesses must not inherit that environment wholesale. This module
builds a scrubbed copy for them and, for missions that genuinely need to call a
provider directly (red-team transports), writes a per-session JSON key file
with mode 0600. Helpers such as ``rt_send.py`` resolve a key from the process
environment first and from ``PLINY_AGENT_KEY_FILE`` second.

The contract is deliberately narrow:

* no key value is ever returned by a status/list helper,
* no key value is placed in an agent command line or log,
* a key file exists only for the lifetime of the session that needs it.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Mapping, Optional

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
KEY_DIR = STATE_DIR / "agent_keys"
KEY_FILE_ENV = "PLINY_AGENT_KEY_FILE"

# Environment entries that are credentials, not configuration. The regex is
# intentionally specific: it catches API keys, bearer tokens, client secrets
# and cloud access keys without stripping ordinary variables such as
# ``SSH_AUTH_SOCK`` or ``GPG_AGENT_INFO``.
_SECRET_ENV = re.compile(
    r"(?:^|_)("
    r"API_?KEY|APIKEY|"
    r"ACCESS_?KEY|PRIVATE_?KEY|CLIENT_?SECRET|"
    r"SECRET|SECRETS|TOKEN|PASSWORD|PASSWD|CREDENTIALS?"
    r")(?:_|$)",
    re.IGNORECASE,
)

# Exact names that do not match the pattern but are still credentials.
_SECRET_EXACT = {
    "GITHUB_PAT", "GH_PAT", "GH_TOKEN", "GITHUB_TOKEN",
    "CODEX_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_ORG",
    "DATABASE_URL", "REDIS_URL", "MONGODB_URI", "MONGO_URI",
}

# Per-session capabilities the server mints, signs and validates itself. They
# name exactly one session and are useless outside it, so they are not provider
# credentials and must survive the scrub: nested runners (opencode_runner.py,
# codex_runner.py) build their child environment from this function, and
# dropping PLINY_CU_TOKEN there left the agent's tool shell unable to present
# the Computer Use grant its own launch had just issued.
_SCOPED_ENV = ("PLINY_AGENT_TOKEN", "PLINY_CU_TOKEN")

# Provider variables that red-team transports understand. Only these are
# copied into a session key file; a general mission gets none.
PROVIDER_KEY_ENV = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
)

# provider -> the environment names its transport actually reads. Mirrors
# rt_send.send_to_target(): OpenRouter appears only where that transport has a
# fallback, local transports need no credential at all.
PROVIDER_KEY_ENVS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY", "OPENROUTER_API_KEY"),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"),
    "xai": ("XAI_API_KEY", "GROK_API_KEY", "OPENROUTER_API_KEY"),
    "grok": ("XAI_API_KEY", "GROK_API_KEY", "OPENROUTER_API_KEY"),
    "mistral": ("MISTRAL_API_KEY", "OPENROUTER_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "qwen": ("OPENROUTER_API_KEY", "DASHSCOPE_API_KEY"),
    "dashscope": ("DASHSCOPE_API_KEY", "OPENROUTER_API_KEY"),
    "zhipu": ("OPENROUTER_API_KEY",),
    "glm": ("OPENROUTER_API_KEY",),
    "z-ai": ("OPENROUTER_API_KEY",),
    "moonshot": ("OPENROUTER_API_KEY",),
    "kimi": ("OPENROUTER_API_KEY",),
    "01-ai": ("OPENROUTER_API_KEY",),
    "yi": ("OPENROUTER_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    # Local or plan-backed transports: no provider credential is required.
    "meta": (),
    "ollama": (),
    "custom": (),
    "opencode": (),
    "opencode-go": (),
}


def is_secret_env(name: str) -> bool:
    """True when an environment variable name denotes a credential."""
    if not name:
        return False
    if name.upper() in _SECRET_EXACT:
        return True
    return bool(_SECRET_ENV.search(name))


# ─── Operator proof for local CLI helpers ───────────────────────────────────

OPERATOR_TOKEN_FILE = STATE_DIR / "operator.token"


def operator_token() -> str:
    """The operator bearer for local CLI calls.

    ``PLINY_AUTH_TOKEN`` when configured, otherwise the rotating token the
    server writes to ``state/operator.token`` at startup. Empty when neither
    exists — callers then send no credential and privileged routes fail
    closed rather than running unauthenticated.
    """
    configured = (os.environ.get("PLINY_AUTH_TOKEN") or "").strip()
    if configured:
        return configured
    try:
        return OPERATOR_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def operator_headers() -> dict:
    """Authorization header for a local CLI call, or ``{}`` when unavailable."""
    token = operator_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def filtered_env(
    base: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> dict:
    """Return an environment safe for an agent process.

    Non-secret configuration (PATH, TEMP, APPDATA, locale, proxy settings…) is
    preserved so Claude/Codex/OpenCode keep behaving like they do in a normal
    shell. Credential-shaped variables are removed, then ``extra`` is applied
    so callers can add scoped values such as ``PLINY_AGENT_TOKEN``.

    ``_SCOPED_ENV`` survives the scrub unchanged — see the comment there.
    """
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items()
           if key in _SCOPED_ENV or not is_secret_env(key)}
    if extra:
        env.update({key: value for key, value in extra.items() if value is not None})
    return env


def session_key_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id or "session")[:160]
    return KEY_DIR / f"{safe}.json"


def provider_key_names(provider: str) -> tuple[str, ...]:
    """The environment names a target provider actually needs.

    Returns an empty tuple when the provider is unknown or needs no
    credential. Empty means "write no key file" — fail closed rather than
    handing the session every key the server happens to hold.
    """
    key = (provider or "").strip().lower()
    if not key:
        return ()
    return tuple(PROVIDER_KEY_ENVS.get(key, ()))


def write_session_key_file(
    session_id: str,
    source: Optional[Mapping[str, str]] = None,
    names: Iterable[str] = (),
) -> Optional[Path]:
    """Write a 0600 key file for one session and return its path (or None).

    ``names`` is required to be explicit: an empty default keeps a caller from
    accidentally writing every configured provider key. Pass
    ``provider_key_names(provider)`` for a red-team target.

    The file contains only provider variables that are actually set. It is
    written atomically so a helper can never read a half-written JSON object.
    """
    source = os.environ if source is None else source
    values = {name: source[name] for name in names if source.get(name)}
    if not values:
        return None
    path = session_key_path(session_id)
    temp_name = ""
    try:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=KEY_DIR, delete=False
        ) as handle:
            json.dump(values, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path
    except OSError:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        return None


def remove_session_key_file(session_id: str) -> None:
    """Delete a session key file; safe to call repeatedly."""
    try:
        session_key_path(session_id).unlink()
    except OSError:
        pass


def read_key_file(path: Optional[str] = None) -> dict:
    """Read a session key file. Returns an empty dict on any problem."""
    candidate = path or os.environ.get(KEY_FILE_ENV, "")
    if not candidate:
        return {}
    try:
        data = json.loads(Path(candidate).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_key(*names: str, key_file: Optional[str] = None) -> str:
    """Resolve a credential without exposing it.

    Environment wins (server-side and manual CLI use), then the current
    session's key file (agent-side use).
    """
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    stored = read_key_file(key_file)
    for name in names:
        value = stored.get(name, "")
        if isinstance(value, str) and value:
            return value
    return ""
