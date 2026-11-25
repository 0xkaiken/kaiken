"""Subscription-account usage guard.

Enforces the rule in ``AGENTS.md`` and ``CLAUDE.md``: an agent or a test may
not, on its own initiative, use a user's specific Anthropic or GPT
subscription accounts. Only an explicit operator request authorizes a seat.

Origins
-------
``operator``    a human action — dashboard launch, CLI, follow-up reply.
``automation``  a standing request the operator configured — a schedule, a
                mission, the gauntlet, a lair pack, the fixer.
``agent``       a launch requested by an agent (scoped ``PLINY_AGENT_TOKEN``).
``test``        a test harness.

Seats
-----
Only ``claude`` (Anthropic) and ``codex`` (OpenAI / ChatGPT) spend a
subscription seat. ``opencode`` bills its own Go plan and is out of scope.

The default origin for a thread that has not been stamped is ``operator``:
this is a single-operator localhost application, and the loops the server
runs — scheduler, gauntlet, missions — are the operator's standing requests.
The scoped agent token demotes a request to ``agent`` the moment it
authenticates, which is the boundary that matters; see
``server._require_api_access``.

What this cannot do
-------------------
An agent already running inside a session inherits that session's
environment. Nothing in-process can distinguish "the sanctioned runner" from
"the agent shell that invoked the same runner" — the OS gives them the same
env and the same user. So the controls here are:
  * fail-closed at launch (``Session._start_inner``),
  * a seat check in every runner script that is *not* the runtime itself
    (``codex_runner``, ``rt_send``),
  * a domain block in the agent browser (``pw_browser``), and
  * a Bash deny rule on the CLI in ``agent_permissions``.
An agent that resolves the real binary and defeats its own deny rule can
still burn the seat. That residual gap is inherent to running an agent on
the operator's account, and it is documented rather than papered over.
"""

from __future__ import annotations

import os
import threading
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

ORIGIN_OPERATOR = "operator"
ORIGIN_AUTOMATION = "automation"
ORIGIN_AGENT = "agent"
ORIGIN_TEST = "test"

ORIGINS = (ORIGIN_OPERATOR, ORIGIN_AUTOMATION, ORIGIN_AGENT, ORIGIN_TEST)
TRUSTED_ORIGINS = frozenset({ORIGIN_OPERATOR, ORIGIN_AUTOMATION})
DEFAULT_ORIGIN = ORIGIN_OPERATOR

# runtime -> provider whose subscription seat it spends
SUBSCRIPTION_RUNTIMES = {"claude": "anthropic", "codex": "openai"}
PROVIDER_LABELS = {"anthropic": "Anthropic", "openai": "OpenAI"}

SEAT_ENV = "PLINY_SUBSCRIPTION_SEAT"
AUTHORIZED_ENV = "PLINY_SUBSCRIPTION_AUTHORIZED"
ORIGIN_ENV = "PLINY_SUBSCRIPTION_ORIGIN"
BROWSER_ALLOW_ENV = "PLINY_ALLOW_SUBSCRIPTION_BROWSER"
CLI_DENY_ENV = "PLINY_AGENT_SUBSCRIPTION_CLI_DENY"

# Consumer web surfaces the agent browser must not drive with the operator's
# logged-in session.
BROWSER_DOMAINS = ("claude.ai", "chatgpt.com", "chat.openai.com")

_state = threading.local()


class SubscriptionDenied(RuntimeError):
    """A launch or nested call that would spend a subscription seat."""

    def __init__(self, message: str, *, runtime: str = "", origin: str = "") -> None:
        self.runtime = runtime
        self.origin = origin
        super().__init__(message)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    runtime: str
    origin: str
    reason: str


# ── normalization ───────────────────────────────────────────────────────────

def normalize_origin(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in ORIGINS else DEFAULT_ORIGIN


def normalize_runtime(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def provider_for(runtime: Optional[str]) -> str:
    return SUBSCRIPTION_RUNTIMES.get(normalize_runtime(runtime), "")


def provider_label(runtime: Optional[str]) -> str:
    return PROVIDER_LABELS.get(provider_for(runtime), "provider")


# ── thread / request origin ─────────────────────────────────────────────────

def current_origin() -> str:
    return getattr(_state, "origin", DEFAULT_ORIGIN)


def current_authorization() -> bool:
    value = getattr(_state, "authorized", None)
    if value is None:
        return current_origin() in TRUSTED_ORIGINS
    return bool(value)


def set_origin(origin: Optional[str], authorized: Optional[bool] = None) -> None:
    """Stamp the calling thread. Called by the HTTP layer when a token matches."""
    normalized = normalize_origin(origin)
    _state.origin = normalized
    _state.authorized = (
        normalized in TRUSTED_ORIGINS if authorized is None else bool(authorized)
    )


def clear_origin() -> None:
    _state.origin = DEFAULT_ORIGIN
    _state.authorized = DEFAULT_ORIGIN in TRUSTED_ORIGINS


@contextmanager
def origin_context(origin: str, authorized: Optional[bool] = None) -> Iterator[None]:
    previous_origin = current_origin()
    previous_authorized = current_authorization()
    set_origin(origin, authorized)
    try:
        yield
    finally:
        set_origin(previous_origin, previous_authorized)


def bind_current_origin(callback):
    """Carry the caller's authorization into a background thread or pool task."""
    origin = current_origin()
    authorized = current_authorization()

    def run(*args, **kwargs):
        with origin_context(origin, authorized):
            return callback(*args, **kwargs)

    return run


def automation_request() -> "object":
    """Operator-configured standing request (schedule, mission, gauntlet)."""
    return origin_context(ORIGIN_AUTOMATION, True)


def agent_request() -> "object":
    return origin_context(ORIGIN_AGENT, False)


def test_request() -> "object":
    return origin_context(ORIGIN_TEST, False)


# ── decisions ───────────────────────────────────────────────────────────────

def decide(
    runtime: Optional[str],
    *,
    origin: Optional[str] = None,
    authorized: Optional[bool] = None,
) -> Decision:
    """Resolve whether ``runtime`` may start in the current context."""
    normalized_runtime = normalize_runtime(runtime)
    if normalized_runtime not in SUBSCRIPTION_RUNTIMES:
        return Decision(
            True,
            normalized_runtime,
            normalize_origin(origin) if origin is not None else current_origin(),
            "not a subscription runtime",
        )

    resolved_origin = normalize_origin(origin) if origin is not None else current_origin()
    resolved_authorized = (
        current_authorization() if authorized is None else bool(authorized)
    )
    provider = provider_label(normalized_runtime)

    if not resolved_authorized:
        return Decision(
            False,
            normalized_runtime,
            resolved_origin,
            f"no explicit operator authorization to spend an {provider} "
            f"subscription seat (launch originated from {resolved_origin!r})",
        )
    if resolved_origin not in TRUSTED_ORIGINS:
        return Decision(
            False,
            normalized_runtime,
            resolved_origin,
            f"launch originated from {resolved_origin!r}; only an operator request "
            f"may spend an {provider} subscription seat",
        )
    return Decision(True, normalized_runtime, resolved_origin, f"authorized by {resolved_origin}")


def require(
    runtime: Optional[str],
    *,
    origin: Optional[str] = None,
    authorized: Optional[bool] = None,
) -> Decision:
    """Raise ``SubscriptionDenied`` unless ``runtime`` may spend a seat."""
    decision = decide(runtime, origin=origin, authorized=authorized)
    if decision.allowed:
        return decision
    provider = provider_label(decision.runtime)
    raise SubscriptionDenied(
        f"{decision.runtime} runs on an {provider} subscription account and "
        f"{decision.reason}. Refusing to start it. An operator has to launch "
        "this explicitly — from the dashboard, or with an intentional scripted "
        "run.",
        runtime=decision.runtime,
        origin=decision.origin,
    )


# ── environment contract for agent processes ────────────────────────────────

def session_flags(
    runtime: Optional[str],
    authorized: bool = False,
    origin: Optional[str] = None,
) -> dict:
    """Env markers describing the seat a session was granted.

    ``PLINY_SUBSCRIPTION_SEAT`` is empty for runtimes that do not spend an
    Anthropic/GPT seat, so a runner script can tell "no seat at all" apart
    from "a different seat".
    """
    normalized_runtime = normalize_runtime(runtime)
    seat = normalized_runtime if normalized_runtime in SUBSCRIPTION_RUNTIMES else ""
    granted = bool(authorized) and seat != ""
    return {
        SEAT_ENV: seat,
        AUTHORIZED_ENV: "1" if granted else "0",
        ORIGIN_ENV: normalize_origin(origin),
    }


def runner_denial(runtime: Optional[str]) -> Optional[str]:
    """Refusal reason for a script that is about to start a subscription CLI.

    Returns ``None`` when the call may proceed. Reading only the environment
    keeps this usable from any runner process.
    """
    seat = (os.environ.get(SEAT_ENV) or "").strip().lower()
    if not seat:
        # No session context: a standalone operator invocation.
        return None
    if (os.environ.get(AUTHORIZED_ENV) or "").strip() != "1":
        return (
            "this session is not authorized to spend a subscription seat"
        )
    normalized_runtime = normalize_runtime(runtime)
    if seat != normalized_runtime:
        return (
            f"this session's seat is {seat!r}; starting {normalized_runtime} would "
            "spend a different account"
        )
    return None


def require_runner(runtime: Optional[str]) -> None:
    """Raise when a runner script is about to spend the wrong seat."""
    reason = runner_denial(runtime)
    if reason is None:
        return
    provider = provider_label(runtime)
    raise SubscriptionDenied(
        f"{runtime} runs on an {provider} subscription account and {reason}. "
        "Refusing to start a nested subscription CLI. Ask the operator to run "
        "it, or use the provider-key path instead.",
        runtime=normalize_runtime(runtime),
    )


# ── agent browser ───────────────────────────────────────────────────────────

def _host(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        return (urllib.parse.urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def subscription_host(url: str) -> str:
    """Return the subscription domain ``url`` points at, or ``""``."""
    host = _host(url)
    if not host:
        return ""
    for domain in BROWSER_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return ""


def browser_denial(url: str, allowed: Optional[bool] = None) -> Optional[str]:
    """Refusal reason for navigating the agent browser to ``url``."""
    if allowed is None:
        allowed = (os.environ.get(BROWSER_ALLOW_ENV) or "").strip() == "1"
    if allowed:
        return None
    domain = subscription_host(url)
    if not domain:
        return None
    return (
        f"{domain} is a subscription-account site. This browser is agent-driven, "
        "so it may not use the operator's logged-in seat there. "
        f"Set {BROWSER_ALLOW_ENV}=1 to allow it deliberately."
    )
