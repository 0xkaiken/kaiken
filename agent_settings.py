"""Safe CLI account status and the default models used by missions."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import tempfile
from process_control import replace_file

from opencode_runner import GO_DEFAULT_MODEL, opencode_models, valid_model as valid_opencode_model

CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"
_claude_cache = None
_claude_cache_until = 0.0
_claude_lock = threading.Lock()
PREFERENCES_FILE = Path(__file__).resolve().parent / "state" / "agent_preferences.json"
RUNTIMES = ("claude", "codex", "opencode")
_preferences_lock = threading.Lock()


def agent_preferences():
    try:
        data = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def default_runtime(fallback="codex"):
    runtime = agent_preferences().get("runtime", fallback)
    return runtime if runtime in RUNTIMES else fallback


def configured_model(runtime):
    return agent_preferences().get("models", {}).get(runtime) or None


def validate_model(runtime, model):
    if runtime not in RUNTIMES:
        raise ValueError("Choose Claude Code, GPT / Codex or OpenCode Go")
    if model is None or model == "":
        return None
    if not isinstance(model, str) or len(model) > 200 or model.startswith('-') or any(c.isspace() for c in model):
        raise ValueError("Enter a valid model ID without spaces")
    if runtime == "opencode" and not valid_opencode_model(model):
        raise ValueError("OpenCode models use provider/model, e.g. opencode-go/glm-5.3-flash or openrouter/<model>")
    return model


def save_agent_preferences(runtime, model):
    model = validate_model(runtime, model)
    with _preferences_lock:
        data = agent_preferences()
        data["runtime"] = runtime
        data.setdefault("models", {})[runtime] = model
        PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=PREFERENCES_FILE.parent, delete=False) as temp:
            json.dump(data, temp, indent=2)
            name = temp.name
        try:
            replace_file(name, PREFERENCES_FILE)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return data


def clear_claude_auth_cache():
    global _claude_cache_until
    with _claude_lock:
        _claude_cache_until = 0.0


def claude_auth_status():
    global _claude_cache, _claude_cache_until
    with _claude_lock:
        if _claude_cache is not None and time.monotonic() < _claude_cache_until:
            return dict(_claude_cache)
        cli = shutil.which("claude")
        info = {"installed": bool(cli), "logged_in": False,
                "email": None, "subscription_type": None, "auth_method": None}
        if cli:
            try:
                result = subprocess.run(
                    [cli, "auth", "status", "--json"], capture_output=True,
                    text=True, encoding="utf-8", timeout=5,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
                status = json.loads(result.stdout)
                # Whitelist display fields. Never return tokens, credentials,
                # environment variables, or the full CLI response.
                info.update(logged_in=bool(status.get("loggedIn")),
                            email=status.get("email"),
                            subscription_type=status.get("subscriptionType"),
                            auth_method=status.get("authMethod"))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                info["error"] = "Claude sign-in status is temporarily unavailable"
        _claude_cache, _claude_cache_until = info, time.monotonic() + 10
        return dict(info)


def default_mission_models():
    model = os.environ.get("PLINY_CODEX_MODEL")
    source = "PLINY_CODEX_MODEL" if model else "Codex CLI default"
    if not model:
        config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            config = tomllib.loads((config_home / "config.toml").read_text(encoding="utf-8"))
            model = config.get("model")
            if model:
                source = "Codex config.toml"
        except (ImportError, OSError, ValueError):
            pass
    defaults = {
        "claude": {"model": CLAUDE_DEFAULT_MODEL, "source": "Libertarium default", "fallback": "sonnet"},
        "codex": {"model": model, "source": source},
        "opencode": {"model": GO_DEFAULT_MODEL, "source": "OpenCode Go"},
    }
    for runtime, selected in agent_preferences().get("models", {}).items():
        if runtime in defaults and selected:
            defaults[runtime] = {"model": selected, "source": "Application default"}
    return defaults


CLAUDE_ALIASES = {"fable": "latest Fable", "opus": "latest Opus", "sonnet": "latest Sonnet",
                  "haiku": "latest Haiku", "opusplan": "Opus plans, Sonnet builds"}


def claude_model_label(model_id):
    """claude-opus-5-5 -> Opus 5.5; aliases get a short description."""
    if model_id in CLAUDE_ALIASES:
        return CLAUDE_ALIASES[model_id]
    parts = model_id.removeprefix("claude-").split("-")
    if parts and parts[0] in {"opus", "sonnet", "haiku", "fable"}:
        return parts[0].capitalize() + " " + ".".join(parts[1:])
    return model_id


def mission_model_catalogs():
    defaults = default_mission_models()
    claude = [{"id": name, "name": claude_model_label(name)} for name in dict.fromkeys(
              (CLAUDE_DEFAULT_MODEL, "claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5",
               "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-4-8", "claude-sonnet-5",
               "claude-opus-4-7", "claude-opus-4-6", "fable", "opus", "sonnet", "haiku", "opusplan"))]
    codex = []
    try:
        root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        cache = json.loads((root / "models_cache.json").read_text(encoding="utf-8"))
        codex = [{"id": model["slug"], "name": model.get("display_name") or model["slug"]}
                 for model in cache.get("models", []) if model.get("slug")]
    except (OSError, ValueError, KeyError):
        pass
    seen_codex = {model["id"] for model in codex}
    for slug, label in (
        ("gpt-6.2-astra", "GPT-6.2 Astra"),
        ("gpt-6.2-sol", "GPT-6.2 Sol"),
        ("gpt-6.1-terra", "GPT-6.1 Terra"),
        ("gpt-6-luna", "GPT-6 Luna"),
    ):
        if slug not in seen_codex:
            codex.append({"id": slug, "name": label})
            seen_codex.add(slug)
    if defaults["codex"]["model"] and defaults["codex"]["model"] not in seen_codex:
        codex.insert(0, {"id": defaults["codex"]["model"], "name": defaults["codex"]["model"]})
    return {"claude": claude, "codex": codex,
            "opencode": [{"id": model, "name": model} for model in opencode_models()]}
