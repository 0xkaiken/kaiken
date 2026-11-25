#!/usr/bin/env python3
"""pliny_secrets_client — environment-backed replacement for the Keychain sidecar.

History: the original implementation talked to ``pliny_secrets_sidecar.py``, a
macOS-only process that held API keys in RAM behind a Unix socket. That trio was
removed from the public tree. Everything that imported this module kept working
*until* it was called, which is why the tweet drafter and the auth overview broke
silently.

This module keeps the same five-function surface, so no caller changes:

    sidecar_ping()            -> {"ok": bool, "mode": str}
    sidecar_providers()       -> {"ok": bool, "providers": [names]}
    sidecar_oauth_status()    -> {"ok": bool, "oauth": {provider: {...}}}
    sidecar_draft_tweets()    -> {"ok", "candidates", "provider", "model", ...}
    sidecar_research_posts()  -> {"ok", "posts", ...}

Where the keys come from, in order:
  1. process environment (populated from .env by server.py at startup)
  2. ``state/secrets.json`` written by the dashboard's secrets card

No key value is ever returned to the caller — only provider *names* and booleans.
That contract is the whole point of the old sidecar and it is preserved here.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
WORKSHOP_DIR = Path(os.environ.get(
    "PLINY_WORKSHOP",
    str(Path(__file__).resolve().parent / "state" / "workshop"),
)).expanduser()

DEFAULT_TIMEOUT = 120.0

# provider name -> candidate env vars (first hit wins)
PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "xai":        ("XAI_API_KEY", "GROK_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "anthropic":  ("ANTHROPIC_API_KEY",),
    "openai":     ("OPENAI_API_KEY",),
    "google":     ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "mistral":    ("MISTRAL_API_KEY",),
    "deepseek":   ("DEEPSEEK_API_KEY",),
    "cohere":     ("COHERE_API_KEY",),
    "github_pat": ("GITHUB_PAT", "PLINY_GITHUB_PAT"),
}

DRAFT_MODEL = os.environ.get("PLINY_TWEET_MODEL", "x-ai/grok-4.20")
XAI_MODEL = os.environ.get("PLINY_XAI_MODEL", "grok-4")

PLACEHOLDER = re.compile(r"^(?:|sk-or-v1-?\.{2,}|your[_-].*|xxx+|changeme|"
                         r"placeholder|<.*>)$", re.IGNORECASE)


def _load_dotenv() -> None:
    """Populate os.environ from the repo .env when running standalone.

    server.py already does this at startup; the CLI path does not. We never
    overwrite an existing variable, so an explicit environment always wins.
    """
    path = BASE_DIR / ".env"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


# ---------------------------------------------------------------------------
#  key resolution (never returns a value to a caller)
# ---------------------------------------------------------------------------

def _secrets_file() -> dict:
    path = STATE_DIR / "secrets.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _key(provider: str) -> str:
    for var in PROVIDER_ENV.get(provider, ()):
        value = os.environ.get(var, "")
        if value and not PLACEHOLDER.match(value.strip()):
            return value.strip()
    stored = _secrets_file().get(provider)
    if isinstance(stored, str) and stored and not PLACEHOLDER.match(stored.strip()):
        return stored.strip()
    return ""


# ---------------------------------------------------------------------------
#  status surface
# ---------------------------------------------------------------------------

def sidecar_ping() -> dict:
    return {"ok": True, "mode": "env", "socket": None,
            "note": "environment-backed shim (Keychain sidecar removed)"}


def sidecar_providers() -> dict:
    found = [name for name in PROVIDER_ENV if _key(name)]
    return {"ok": True, "providers": found,
            "supported": list(PROVIDER_ENV)}


def sidecar_oauth_status() -> dict:
    """Report the xAI/SuperGrok seat without reading any credential file.

    The old sidecar asked the local ``hermes`` CLI. The shim can only report
    what the environment proves: a key is present or it is not. It says so
    instead of pretending to know the plan.
    """
    has_xai = bool(_key("xai"))
    return {"ok": True, "oauth": {
        "xai": {
            "signed_in": has_xai,
            "source": "XAI_API_KEY" if has_xai else None,
            "plan": None,
            "note": ("key present in environment" if has_xai
                     else "no xAI credential; SuperGrok sign-in is unavailable "
                          "without the removed sidecar"),
        }
    }}


# ---------------------------------------------------------------------------
#  HTTP helpers
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, key: str, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "http://localhost:8888",
            "X-Title": "Pliny Command",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"error": body[:400]}
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _envelope_from(payload: dict) -> dict:
    """Pull a JSON array of tweets out of a chat completion, leniently."""
    try:
        text = payload["choices"][0]["message"]["content"]
    except Exception:
        return {"ok": False, "error": "no completion content",
                "raw": json.dumps(payload)[:400]}

    candidates: list[dict] = []
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
            for item in parsed:
                if isinstance(item, str):
                    candidates.append({"text": item})
                elif isinstance(item, dict):
                    txt = item.get("text") or item.get("tweet") or ""
                    if txt:
                        candidates.append({**item, "text": txt})
        except Exception:
            pass

    if not candidates:
        for line in text.splitlines():
            line = line.strip().strip('",')
            if len(line) > 24 and not line.endswith(":"):
                candidates.append({"text": line})

    return {"ok": bool(candidates), "candidates": candidates, "raw": text}


# ---------------------------------------------------------------------------
#  capabilities
# ---------------------------------------------------------------------------

def _system_prompt() -> str:
    """Prefer the operator's hot-swappable prompt, fall back to a built-in."""
    override = WORKSHOP_DIR / "TWEET_BANGER_PROMPT.md"
    try:
        text = override.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception:
        pass
    return (
        "You write posts for an AI red-team researcher's account. Voice: lowercase, "
        "specific, punchy. Every post carries a receipt — a number, a model name, a "
        "verbatim artifact. End on a bait line: a question or an open loop that "
        "invites a reply. No hashtags in the body, no link in the body, at most two "
        "emoji. Never use: delve, tapestry, intricate, realm, unlock, harness, "
        "\"game-changer\", \"in an era\", \"it's worth noting\".\n\n"
        "Return ONLY a JSON array of objects: [{\"text\": \"...\"}, ...]. "
        "Each text must be <= 280 characters."
    )


def sidecar_draft_tweets(context: str, *, n: int = 5,
                         use_xai: bool = False) -> dict:
    started = time.time()
    n = max(1, min(int(n or 5), 10))

    if use_xai:
        provider, key = "xai", _key("xai")
        url = "https://api.x.ai/v1/chat/completions"
        model = XAI_MODEL
    else:
        provider, key = "openrouter", _key("openrouter")
        url = "https://openrouter.ai/api/v1/chat/completions"
        model = DRAFT_MODEL

    if not key:
        return {"ok": False,
                "error": f"no {provider} key configured; set "
                         f"{PROVIDER_ENV[provider][0]} in .env",
                "candidates": [], "provider": provider, "model": model}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content":
                f"Write {n} candidate posts about:\n\n{context}\n\n"
                f"Return the JSON array only."},
        ],
        "temperature": 0.9,
        "max_tokens": 1200,
    }

    status, response = _post_json(url, payload, key, DEFAULT_TIMEOUT)
    if status != 200:
        return {"ok": False, "error": response.get("error", f"HTTP {status}"),
                "candidates": [], "provider": provider, "model": model,
                "elapsed_s": round(time.time() - started, 2)}

    result = _envelope_from(response)
    result.update({
        "provider": provider, "model": model,
        "elapsed_s": round(time.time() - started, 2),
        "n_requested": n,
    })
    return result


def sidecar_research_posts(topic: str, *, hours: int = 24,
                           max_results: int = 10) -> dict:
    """Recent high-engagement posts on a topic.

    xAI's ``x_search`` tool is only reachable with an xAI key, which is why the
    dashboard checks ``_xai_key_present()`` before calling. Without the key this
    returns a structured failure rather than an empty success.
    """
    started = time.time()
    key = _key("xai")
    if not key:
        return {"ok": False, "posts": [], "elapsed_s": 0.0,
                "error": "xAI key not configured; x_search unavailable"}

    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content":
                "You have live access to X. Search it and return real posts only."},
            {"role": "user", "content":
                f"Find the {max_results} highest-engagement posts from the last "
                f"{hours} hours about: {topic}\n\n"
                f"Return ONLY a JSON array: "
                f"[{{\"author\": \"@handle\", \"text\": \"...\", \"url\": \"...\"}}]"},
        ],
        "temperature": 0.3,
        "max_tokens": 1600,
    }

    status, response = _post_json("https://api.x.ai/v1/chat/completions",
                                  payload, key, DEFAULT_TIMEOUT)
    if status != 200:
        return {"ok": False, "posts": [],
                "error": response.get("error", f"HTTP {status}"),
                "elapsed_s": round(time.time() - started, 2)}

    try:
        text = response["choices"][0]["message"]["content"]
    except Exception:
        return {"ok": False, "posts": [], "error": "no completion content",
                "elapsed_s": round(time.time() - started, 2)}

    posts: list[dict] = []
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start:end + 1])
            posts = [p for p in parsed if isinstance(p, dict)]
        except Exception:
            posts = []

    return {"ok": bool(posts), "posts": posts,
            "elapsed_s": round(time.time() - started, 2),
            "model": XAI_MODEL}
