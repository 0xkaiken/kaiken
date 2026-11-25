"""Session-scoped Computer Use capabilities.

A capability is the only credential an agent process holds for Computer Use.
It names one session, one mode (``desktop`` or ``browser``), a set of scopes
and an expiry, and it is signed with a key that never enters an agent
environment. The command server mints one when a session acquires the
Computer Use slot and revokes it when the slot is released, so a capability
copied out of one mission is useless in the next.

Two verifiers share this format:

* ``CapabilityRegistry.check`` in the command server, which also enforces
  revocation (the grant's nonce must still be current);
* ``verify`` in the browser sidecar, which checks signature, expiry and mode
  and then compares session id + nonce with the owner the server pushed.

Same-OS-user processes can still read each other's memory; this is a
boundary against confused deputies and stale credentials, not an OS sandbox
(see CLAUDE.md, "Isolation limit").

Stdlib only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Dict, Iterable, Optional

PREFIX = "cu1"
DEFAULT_TTL_SECONDS = int(os.environ.get("PLINY_CU_CAPABILITY_TTL", "") or 6 * 3600)
MAX_TTL_SECONDS = 24 * 3600
SCOPES = ("observe", "act", "pause")
ENV_NAME = "PLINY_CU_TOKEN"
HEADER_NAME = "X-Pliny-CU-Token"

# Structured error codes with operator/agent-facing recovery guidance. The
# desktop adapter, the command server and the browser sidecar all report
# failures with one of these codes so an agent can branch on them.
RECOVERY = {
    "capability_missing": "This request needs the session's Computer Use capability (PLINY_CU_TOKEN). "
                          "Send it as the X-Pliny-CU-Token header (server) or Authorization: Bearer (browser).",
    "capability_invalid": "The capability is malformed or was not issued by this server. Do not retry; report it.",
    "capability_expired": "The capability expired. Stop and ask the operator to continue the mission.",
    "capability_revoked": "This mission no longer owns Computer Use. Stop; do not retry.",
    "capability_wrong_session": "The capability belongs to a different session. Use only your own session id.",
    "capability_wrong_mode": "The capability was issued for a different Computer Use mode.",
    "capability_scope": "The capability does not grant this operation.",
    "operator_required": "Only the operator can do this (Release / resume). Wait for the operator.",
    "desktop_paused": "The operator has control. Stop and wait for Release; do not retry in a loop.",
    "desktop_ownership_changed": "Another mission owns the desktop or this mission ended. Stop.",
    "desktop_unavailable": "The desktop is locked, on a secure/UAC screen, or unavailable. Report the blocker.",
    "focus_changed": "The target window lost focus. Re-observe, activate the target once, retry once.",
    "target_mismatch": "The point is not on the active target window. Re-observe and activate the intended "
                       "window, or use clear_target for a deliberate click outside it.",
    "stale_frame": "The screenshot these coordinates came from is outdated. Take a fresh screenshot and "
                   "recompute coordinates before acting.",
    "cancelled": "Input was cancelled (operator pause, ESC or mission stop). Re-observe before continuing.",
    "deadline_exceeded": "The action batch hit its time limit. Re-observe; split the work into smaller batches.",
    "invalid_action": "The step was malformed. Fix the step; do not repeat it unchanged.",
}


class CapabilityError(PermissionError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code

    def payload(self) -> dict:
        return error_payload(self.code, str(self))


def error_payload(code: str, message: str, **extra) -> dict:
    return {"ok": False, "error": message, "code": code,
            "recovery": RECOVERY.get(code, ""), **extra}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> bytes:
    return secrets.token_bytes(32)


def key_from_text(text: str) -> bytes:
    """Signing key from an environment value (hex preferred, raw otherwise)."""
    text = (text or "").strip()
    if not text:
        return b""
    try:
        return bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")


def mint(key: bytes, session_id: str, mode: str, *, ttl: Optional[float] = None,
         scopes: Iterable[str] = SCOPES, nonce: Optional[str] = None,
         now: Optional[float] = None) -> str:
    if not key:
        raise ValueError("capability signing key is not configured")
    if mode not in ("desktop", "browser"):
        raise ValueError("mode must be desktop or browser")
    issued = time.time() if now is None else now
    ttl = DEFAULT_TTL_SECONDS if ttl is None else ttl
    claims = {"sid": str(session_id), "mode": mode, "scp": sorted(set(scopes)),
              "iat": int(issued), "exp": int(issued + max(1, min(float(ttl), MAX_TTL_SECONDS))),
              "n": nonce or secrets.token_hex(8)}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(key, (PREFIX + "." + body).encode("ascii"), hashlib.sha256).digest())
    return f"{PREFIX}.{body}.{signature}"


def verify(key: bytes, token: str, *, session_id: Optional[str] = None, mode: Optional[str] = None,
           scope: Optional[str] = None, now: Optional[float] = None) -> dict:
    """Signature, expiry, session, mode and scope. Raises CapabilityError."""
    if not token:
        raise CapabilityError("capability_missing", "Computer Use capability required")
    if not key:
        raise CapabilityError("capability_invalid", "capability verification is not configured")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise CapabilityError("capability_invalid", "malformed capability")
    expected = _b64(hmac.new(key, (PREFIX + "." + parts[1]).encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise CapabilityError("capability_invalid", "capability signature mismatch")
    try:
        claims = json.loads(_unb64(parts[1]))
    except (ValueError, TypeError):
        raise CapabilityError("capability_invalid", "malformed capability claims")
    if not isinstance(claims, dict):
        raise CapabilityError("capability_invalid", "malformed capability claims")
    current = time.time() if now is None else now
    if float(claims.get("exp", 0)) <= current:
        raise CapabilityError("capability_expired", "Computer Use capability expired")
    if session_id is not None and claims.get("sid") != session_id:
        raise CapabilityError("capability_wrong_session", "capability belongs to another session")
    if mode is not None and claims.get("mode") != mode:
        raise CapabilityError("capability_wrong_mode", f"capability is not valid for {mode} mode")
    if scope is not None and scope not in (claims.get("scp") or ()):
        raise CapabilityError("capability_scope", f"capability does not grant {scope}")
    return claims


class CapabilityRegistry:
    """Server-side grants: one live capability per session, revocable."""

    def __init__(self, key: Optional[bytes] = None):
        self.key = key or new_key()
        self._lock = threading.Lock()
        self._grants: Dict[str, dict] = {}

    def grant(self, session_id: str, mode: str, ttl: Optional[float] = None) -> str:
        nonce = secrets.token_hex(8)
        token = mint(self.key, session_id, mode, ttl=ttl, nonce=nonce)
        claims = verify(self.key, token)
        with self._lock:
            self._grants[session_id] = {"mode": mode, "nonce": nonce, "exp": claims["exp"], "token": token}
        return token

    def current(self, session_id: str) -> Optional[dict]:
        with self._lock:
            grant = self._grants.get(session_id)
            return dict(grant) if grant else None

    def token_for(self, session_id: str) -> str:
        grant = self.current(session_id)
        if not grant or grant["exp"] <= time.time():
            return ""
        return grant["token"]

    def revoke(self, session_id: str) -> bool:
        with self._lock:
            return self._grants.pop(session_id, None) is not None

    def check(self, token: str, session_id: str, mode: str, scope: str) -> dict:
        claims = verify(self.key, token, session_id=session_id, mode=mode, scope=scope)
        grant = self.current(session_id)
        if not grant or not hmac.compare_digest(grant["nonce"], str(claims.get("n", ""))):
            raise CapabilityError("capability_revoked", "Computer Use capability was revoked")
        return claims
