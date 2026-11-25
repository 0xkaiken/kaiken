"""Cross-process provider health / circuit breaker for target calls.

Why file-backed: every gauntlet target request spawns a fresh ``rt_send.py``
subprocess. An in-memory registry would die with that process, so each caller
would rediscover the same dead key on its own. The registry lives in
``state/provider_health.json`` and every read-modify-write is guarded by an OS
lock file, so one task discovering an exhausted key makes every other task
skip it until the cooling-off window ends.

Failure semantics mirror ``gauntlet``'s upstream-error sentinels:

* **hard** — deterministic provider failure (bad key, no credit, missing
  model). Opens the breaker immediately.
* **soft** — transient failure (429, 5xx, timeout, network). Needs
  ``SOFT_TRIP_AFTER`` consecutive failures to open.
* **none** — a classifier block or a normal model response. A success clears
  the failure counter and drops any persisted breaker state.

The cooldown ladder is 1m -> 5m -> 30m (last rung repeats). A half-open probe
is allowed once ``open_until`` has passed; its result decides the next state.

Nothing in here reads a key value. Only error *classes* and short, scrubbed
detail strings are persisted.
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import transport_errors

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(
    os.environ.get("PLINY_PROVIDER_HEALTH_FILE")
    or (BASE_DIR / "state" / "provider_health.json")
)
LOCK_FILE = STATE_FILE.with_suffix(".lock")

SOFT_TRIP_AFTER = 3
BACKOFF = (60.0, 300.0, 1800.0)

# Strip anything that resembles a credential before it reaches the state file.
_SECRET_RE = re.compile(
    r"\b(?:sk|sk-or-v1|sk-ant|ghp|github_pat|gho|ghu|ghs|ghr)-[A-Za-z0-9_\-]{6,}",
    re.IGNORECASE,
)

def normalize_provider(provider: str) -> str:
    """Stable registry key. Credentials are per-provider in this tree."""
    return (provider or "").strip().lower()


def _scrub(text: str) -> str:
    return _SECRET_RE.sub("<redacted>", str(text or ""))[:200]


def _read() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
        try:
            os.chmod(str(STATE_FILE), 0o600)
        except OSError:
            pass
    except OSError:
        pass


@contextmanager
def _locked():
    """Best-effort cross-process lock. Yields True when the lock was taken.

    If the lock cannot be acquired within ~2s we fail open: the caller reads
    state but skips the write rather than blocking a target request on a
    stale lock file.
    """
    got = False
    fd = None
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            for _ in range(100):
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    got = True
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            got = True
        yield got
    finally:
        if fd is not None:
            try:
                if got:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass


def classify_response(response: str) -> Optional[str]:
    """Return ``hard``, ``soft`` or None for an rt_send response string.

    Delegates to ``transport_errors.health_class`` so a sentinel cannot mean
    "hard" here and "not a failure" in the server.  Unknown ``[ERROR — ...]``
    is soft, never a success: an error must not reset the breaker.
    """
    return transport_errors.health_class(response)


def _cooldown_for(trips: int) -> float:
    return BACKOFF[min(max(trips, 1), len(BACKOFF)) - 1]


def check(provider: str) -> Optional[str]:
    """Return a cooldown detail string when the provider is open, else None."""
    key = normalize_provider(provider)
    if not key:
        return None
    with _locked():
        state = _read()
        entry = state.get(key)
        if not isinstance(entry, dict):
            return None
        until = float(entry.get("open_until") or 0)
        if until <= time.time():
            return None
        return f"{key} provider in cooldown for {int(until - time.time())}s (last: {entry.get('last_error', 'unknown')})"


def note_result(provider: str, response: str) -> str:
    """Record the outcome of one target call. Returns the classification."""
    key = normalize_provider(provider)
    kind = classify_response(response)
    if not key or kind is None:
        if key:
            record_success(key)
        return kind or "none"
    with _locked() as got:
        state = _read()
        if not got:
            return kind
        entry = state.get(key)
        if not isinstance(entry, dict):
            entry = {"fails": 0, "trips": 0, "open_until": 0, "last_error": "", "last_at": 0}
        now = time.time()
        entry["fails"] = int(entry.get("fails") or 0) + 1
        entry["last_error"] = _scrub(response)
        entry["last_at"] = now
        if kind == "hard" or entry["fails"] >= SOFT_TRIP_AFTER:
            entry["trips"] = int(entry.get("trips") or 0) + 1
            entry["open_until"] = now + _cooldown_for(entry["trips"])
            entry["fails"] = 0
        state[key] = entry
        _write(state)
    return kind


def record_success(provider: str) -> None:
    """A real target response: reset counters and drop breaker state."""
    key = normalize_provider(provider)
    if not key:
        return
    with _locked() as got:
        if not got:
            return
        state = _read()
        entry = state.get(key)
        if not isinstance(entry, dict) or (
            not entry.get("fails") and not entry.get("trips") and not entry.get("open_until")
        ):
            return
        state.pop(key, None)
        _write(state)


def reset(provider: Optional[str] = None) -> None:
    """Operator/UI escape hatch: clear one provider or the whole registry."""
    with _locked() as got:
        if not got:
            return
        state = _read()
        if provider:
            state.pop(normalize_provider(provider), None)
        else:
            state = {}
        _write(state)


def snapshot() -> dict:
    """Copy of the registry for a status endpoint or diagnostics."""
    return _read()
