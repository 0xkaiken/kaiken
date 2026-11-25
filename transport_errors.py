"""One classifier for every rt_send transport sentinel.

Before this module each caller guessed independently:

* ``server.is_transport_error`` matched a prefix list;
* ``provider_health.classify_response`` matched its own regexes;
* ``gauntlet.classify_upstream_error`` matched a third list;
* ``blood_artex`` treated anything a target returned as a target answer.

The lists had drifted.  In particular an unclassified ``[ERROR — ...]`` was
"not a provider failure" to ``provider_health``, so the very next call reset
the breaker the failed call had just tripped.  Every module now imports the
decision from here.

Sentinel grammar (produced by ``rt_send._fmt_sentinel``)::

    [BLOCKED — detail]      upstream classifier / content filter
    [RATELIMIT — detail]    provider 429
    [TIMEOUT — detail]      wall-clock expiry
    [NETWORK — detail]      DNS / TLS / connection failure
    [ERROR — detail]        catch-all (auth, quota, 5xx, parse, missing CLI)

Classification is deliberately narrow: only text that *starts* with a
sentinel is an error.  A model answer that quotes ``[ERROR — ...]`` in the
middle of a paragraph is still a model answer.
"""
from __future__ import annotations

import re
from typing import Optional

SENTINEL_KINDS = ("blocked", "ratelimit", "timeout", "network", "error")

# Accept both the em dash rt_send emits and a plain hyphen used by older
# records/tests.  ``[OpenCode Go error ...]`` is the one non-bracketed-word
# sentinel that reaches server.py from opencode_runner.
_SENTINEL_RE = re.compile(
    r"^\s*\[\s*(BLOCKED|RATELIMIT|RATE[ _-]?LIMIT|TIMEOUT|NETWORK|ERROR)\b",
    re.IGNORECASE,
)
_OPENCODE_ERROR_RE = re.compile(r"^\s*\[\s*OpenCode\s+Go\s+error\b", re.IGNORECASE)
_TIMEOUT_DETAIL_RE = re.compile(r"\btimed\s+out\s+after\b", re.IGNORECASE)

_AUTH_RE = re.compile(r"\bauth(?:entication|orization)?\s+(?:401|402|403)\b", re.I)
_QUOTA_RE = re.compile(
    r"insufficient[_ ]quota|out of (?:credit|quota)|no credit|quota (?:exceeded|exhausted)",
    re.I,
)
_KEY_RE = re.compile(r"invalid\s*(?:api.?key|token|credential)|missing\s+api.?key", re.I)
_MODEL_RE = re.compile(r"model\b.{0,40}\bnot\s+(?:found|exist|available)", re.I)
_MISSING_ENV_RE = re.compile(r"requires\s+\w+_API_KEY|neither\s+.*\s+set", re.I)
_SEAT_RE = re.compile(r"claude CLI:.{0,80}(?:auth|quota|credit|not found)", re.I)
_SERVER_RE = re.compile(r"\b(?:50[0-9]|5[1-9][0-9])\s*\(server\)", re.I)
# A subscription guard denial is per-session, not a broken provider.  It must
# never open the process-wide breaker for sessions that are authorized.
_SESSION_ONLY_RE = re.compile(
    r"subscription guard|session not granted|not granted the .* seat", re.I)


def sentinel_kind(text: str) -> Optional[str]:
    """Return the sentinel family for ``text`` or ``None``.

    ``ratelimit`` is returned for both ``RATELIMIT`` and ``RATE LIMIT`` so
    callers do not need to know which spelling produced the line.
    """
    value = str(text or "")
    if not value:
        return None
    if _OPENCODE_ERROR_RE.match(value):
        return "error"
    match = _SENTINEL_RE.match(value)
    if not match:
        return None
    return match.group(1).lower().replace(" ", "").replace("_", "").replace("-", "")


def is_transport_error(text: str) -> bool:
    """True when the value is a transport failure, not a target response."""
    if sentinel_kind(text) is not None:
        return True
    # opencode_runner sometimes surfaces the timeout as prose instead of a
    # sentinel.  Keep the historical server.py guard, but bounded so a long
    # model answer that merely says "timed out after" is not misread.
    value = str(text or "").lstrip()
    return len(value) < 600 and bool(_TIMEOUT_DETAIL_RE.search(value))


def health_class(text: str) -> Optional[str]:
    """Map a target-call result to provider health: ``hard``, ``soft`` or None.

    ``None`` means "the provider answered": a normal model response, or an
    upstream classifier block (the provider is alive, it just filtered the
    prompt).  Unknown ``[ERROR — ...]`` sentinels are ``soft``: an error is
    never allowed to reset a breaker.  That was the old bug.
    """
    value = str(text or "")
    kind = sentinel_kind(value)
    if kind is None:
        return None
    if kind == "blocked":
        # Classifier block: provider reachable.  Not a health failure.
        return None
    if kind == "ratelimit":
        return "soft"
    if kind in ("timeout", "network"):
        return "soft"
    if _SESSION_ONLY_RE.search(value):
        return None
    # kind == "error": split deterministic from transient.
    if any(pattern.search(value) for pattern in
           (_AUTH_RE, _QUOTA_RE, _KEY_RE, _MODEL_RE, _MISSING_ENV_RE, _SEAT_RE)):
        return "hard"
    if _SERVER_RE.search(value):
        return "soft"
    return "soft"


def redact_secrets(text: str) -> str:
    """Remove credential-shaped strings before a value is persisted.

    The campaign store keeps prompts/responses for reconstruction.  Those can
    contain a key the operator pasted or a target echoed.  Redaction is for
    disk only; judging still sees the live value.
    """
    value = str(text or "")
    value = re.sub(
        r"\b(?:sk|sk-or-v1|sk-ant|ghp|github_pat|gho|ghu|ghs|ghr)-[A-Za-z0-9_\-]{6,}",
        "<redacted-key>",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}", "Bearer <redacted>", value)
    value = re.sub(
        r"(?i)\b(api[-_]?key|x-api-key|authorization)\s*[:=]\s*\S{8,}",
        r"\1=<redacted>",
        value,
    )
    return value
