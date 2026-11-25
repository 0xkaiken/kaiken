#!/usr/bin/env python3
"""
Red Team Send — helper for Pliny Red Team Live sessions.
Sends a prompt to a target model and reports both the prompt and response
to the Pliny Command dashboard via API.

Usage:
    Windows: python rt_send.py <attempt> <provider> <model_id> --prompt-file prompt.txt
    The file is read as UTF-8 without PowerShell piping or shell redirection.

    python3 rt_send.py <attempt> <provider> <model_id> --timeout 600 --prompt-file prompt.txt

    --timeout controls the wall-clock kill for any target that generates for a long time
    (atlas asks, long fiction, deep reasoning). probe_send.py used to exist only for this:
    it wrapped complete_text with a 600s timeout because rt_send hard-coded 120s. It is
    merged here as of 2026-11-02 — call rt_send with --timeout instead. Currently applies
    to the OpenCode-Go route; other providers keep their hard-coded caps.

    python3 rt_send.py <attempt> <provider> <model_id> <<'PROMPT'
    Your prompt text here
    PROMPT

    Structured campaigns: after a reply, rt_send prints an [ARTEX RECEIPT]
    line with the probe's request_id. Read the judge/validation decision with:
    python rt_send.py --receipt <request_id> --campaign <campaign_id> --wait 300

Providers: anthropic, openai, google, xai, meta, mistral, deepseek, custom
"""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

import provider_health
import transport_receipts

from agent_secrets import resolve_key
import account_guard

DASHBOARD_API = "http://localhost:8888/api/redteam/chat"
RECEIPT_API = "http://localhost:8888/api/artex/receipt"
# Session-id passthrough so pack-attack mode (N concurrent rt_send.py callers
# against N different blood-agent sessions) tags each chat event with the
# correct session. Set by the server when spawning the Claude CLI subprocess.
_PLINY_SESSION_ID = os.environ.get("PLINY_SESSION_ID", "")
# A structured session gets its campaign id at launch.  One rt_send process is
# one probe, so every dashboard call from this process carries the same request
# id: the prompt, the response, the judge, the validation and the receipt all
# resolve to the exact same evidence record.
_PLINY_CAMPAIGN_ID = os.environ.get("PLINY_ARTEX_CAMPAIGN", "")
_PLINY_ARTEX_PROVIDER = os.environ.get("PLINY_ARTEX_PROVIDER", "")
_PLINY_ARTEX_MODEL = os.environ.get("PLINY_ARTEX_MODEL", "")
_REQUEST_ID = uuid.uuid4().hex


# ─── Error sentinel helpers ──────────────────────────────────────────────────
# The gauntlet recognizes these prefixes and treats them as zero-info upstream
# failures: they do NOT burn the attempt budget and are surfaced to the agent
# so it can retry with a different technique or bail out.
#
#   [BLOCKED — ...]    classifier / safety filter / content policy block
#   [RATELIMIT — ...]  429 from provider or similar
#   [TIMEOUT — ...]    request exceeded our wall-clock limit
#   [NETWORK — ...]    DNS / connection / TLS failure before response
#   [ERROR — ...]      catch-all for anything else (auth, 5xx, parse, etc.)

_BLOCK_KEYWORDS = (
    "content_policy_violation", "content policy", "safety",
    "blocked", "content_filter", "responsible ai", "harmful",
    "cannot assist", "moderation", "prohibited",
)


def _classify_http_error(provider_label, err):
    """Classify a urllib HTTPError into the right sentinel prefix.

    Reads the body (best effort), inspects the status code and body text,
    and returns a (prefix, detail) tuple the caller can format.
    """
    code = getattr(err, "code", 0) or 0
    try:
        body = err.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        body = str(err)
    _capture_wire("response", body, code)
    body_lower = body.lower()

    if code == 429 or "rate limit" in body_lower or "rate_limit" in body_lower or "too many requests" in body_lower:
        return "RATELIMIT", f"{provider_label} 429: {body[:200]}"
    if code in (400, 403, 451, 422):
        if any(k in body_lower for k in _BLOCK_KEYWORDS):
            return "BLOCKED", f"{provider_label} {code} (classifier): {body[:200]}"
        # Some providers return 400 for malformed requests too — keep as ERROR
        return "ERROR", f"{provider_label} {code}: {body[:200]}"
    if code in (401, 402, 403):
        return "ERROR", f"{provider_label} auth {code}: {body[:200]}"
    if 500 <= code < 600:
        return "ERROR", f"{provider_label} {code} (server): {body[:200]}"
    return "ERROR", f"{provider_label} {code}: {body[:200]}"


def _classify_exception(provider_label, exc):
    """Classify a non-HTTP exception (timeout, DNS, etc.)."""
    if isinstance(exc, socket.timeout):
        return "TIMEOUT", f"{provider_label}: request timed out"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        return "NETWORK", f"{provider_label}: {reason}"
    # Unknown — plain ERROR
    return "ERROR", f"{provider_label}: {type(exc).__name__}: {str(exc)[:200]}"


def _fmt_sentinel(prefix, detail):
    return f"[{prefix} — {detail}]"


# ─── Raw wire capture sidechannel ────────────────────────────────────────────
# The gauntlet stores the decoded model answer verbatim, but when a call fails
# upstream it only sees a sentinel: the actual HTTP body that produced
# `blocked_upstream` is thrown away. Capture the bytes on the wire (request and
# response, including discarded retry attempts) on stderr so the run record can
# carry them. Same pattern as [META] — stdout stays byte-identical.
_WIRE_MAX_BYTES = 2048
_WIRE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _capture_wire(side, payload, status=0):
    """Emit one bounded base64 wire line on stderr. Never breaks the call."""
    try:
        raw = payload or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        raw = raw[:_WIRE_MAX_BYTES]
        data = base64.b64encode(raw).decode("ascii")
        print(
            f"[WIRE] side={side} status={status} bytes={len(raw)} data={data}",
            file=sys.stderr,
        )
    except Exception as _e:
        print(f"[rt_send] wire capture failed: {_e}", file=sys.stderr)


class _ReplayResponse:
    """Minimal response shim that replays bytes already read from the real one."""

    def __init__(self, real, raw):
        self._real = real
        self._raw = raw
        self._off = 0
        self.status = getattr(real, "status", 0)
        self.headers = getattr(real, "headers", None)

    def read(self, n=-1):
        if self._off >= len(self._raw):
            return b""
        if n is None or n < 0:
            out = self._raw[self._off:]
            self._off = len(self._raw)
            return out
        out = self._raw[self._off:self._off + n]
        self._off += len(out)
        return out

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        try:
            self._real.close()
        except Exception:
            pass


def _target_workspace():
    """Neutral working directory for opencode-route target requests.

    The opencode CLI reads the workspace it is started in. When a gauntlet
    agent fires rt_send from the repo, the target inherits AGENTS.md and
    CLAUDE.md — files that document the gauntlet, the payload files and the
    script-verbatim workflow — and answers as an agent that already knows it
    is being tested (run 20261117-234316-5ee158, probes 5 and 6). A target
    call has no business reading the attacker's workspace, so it runs in a
    stable empty directory instead.
    """
    path = os.path.join(tempfile.gettempdir(), "pliny_target_workspace")
    os.makedirs(path, exist_ok=True)
    return path


def _wire_urlopen(request, timeout):
    """Open a provider request and tee its body to stderr.

    Explicit at each call site rather than a global urlopen swap — jailbreak_lab
    can call send_to_target concurrently in one process, and a module-global
    monkeypatch would leak across threads on nested entry/exit.
    """
    url = getattr(request, "full_url", None) or (request if isinstance(request, str) else "")
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        host = ""
    if host in _WIRE_LOCAL_HOSTS:
        return urllib.request.urlopen(request, timeout=timeout)
    body = getattr(request, "data", None)
    if body:
        _capture_wire("request", body)
    response = urllib.request.urlopen(request, timeout=timeout)
    raw = response.read()
    try:
        status = response.status
    except Exception:
        status = getattr(response, "code", 0)
    _capture_wire("response", raw, status)
    return _ReplayResponse(response, raw)


def post_to_dashboard(data, retries=2):
    """Post a message to the dashboard and return its JSON reply."""
    structured = bool(_PLINY_CAMPAIGN_ID)
    # Inject session_id so the dashboard can persist per-session chat history
    # (used by pack-attack mode to backfill the chat panel on follow-switch).
    if _PLINY_SESSION_ID and "session_id" not in data:
        data = dict(data, session_id=_PLINY_SESSION_ID)
    if structured:
        if not _PLINY_SESSION_ID:
            raise SystemExit(
                "Target request not sent: structured mode requires PLINY_SESSION_ID.")
        data = dict(data, campaign_id=_PLINY_CAMPAIGN_ID,
                    session_id=_PLINY_SESSION_ID,
                    # Every event from this process names the probe it belongs
                    # to, so the response, judge and receipt cannot attach to
                    # a different request.
                    request_id=_REQUEST_ID)
    needs_permission = (data.get("type") == "prompt"
                        and (bool(data.get("session_id")) or structured))
    if not structured and needs_permission:
        # Free-form: keep one ID across HTTP retries, but allocate a fresh
        # slot for every real target call even when the agent reuses an
        # attempt number.
        data = dict(data, request_id=uuid.uuid4().hex)
    for attempt in range(retries + 1):
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                DASHBOARD_API, body, {"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                reply = json.load(response)
            if needs_permission and not reply.get("request_permitted"):
                raise SystemExit("Target request not sent: dashboard did not grant permission. Finish using received results.")
            if structured and data.get("type") == "prompt":
                if (reply.get("campaign_id") != _PLINY_CAMPAIGN_ID
                        or reply.get("request_id") != _REQUEST_ID):
                    raise SystemExit(
                        "Target request not sent: dashboard permission did not "
                        "name this campaign/request.")
            return reply
        except urllib.error.HTTPError as error:
            if needs_permission and 400 <= error.code < 500:
                detail = error.read().decode("utf-8", errors="replace")
                raise SystemExit(f"Target request not sent: {detail}")
            if attempt == retries:
                if needs_permission:
                    raise SystemExit("Target request not sent: dashboard unavailable; budget could not be checked.")
                print(f"[rt_send] dashboard post failed: {error}", file=sys.stderr)
        except SystemExit:
            raise
        except Exception as e:
            if attempt < retries:
                import time; time.sleep(0.5)
            else:
                if needs_permission:
                    raise SystemExit("Target request not sent: dashboard unavailable; budget could not be checked.")
                # Log silently so at least stderr shows something
                print(f"[rt_send] dashboard post failed: {e}", file=sys.stderr)
    return {}


def fetch_receipt(campaign_id, request_id, wait=0, interval=2.0):
    """Read (or wait for) the campaign receipt for one request.

    ``wait`` seconds are a ceiling, not a sleep: the loop returns as soon as
    the evaluation is no longer pending.  A waiting receipt is printed as-is so
    the agent can decide to keep polling.
    """
    import time
    deadline = time.time() + max(0, int(wait or 0))
    last_error = None
    payload = None
    while True:
        query = urllib.parse.urlencode(
            {"campaign": campaign_id, "request": request_id})
        try:
            with urllib.request.urlopen(f"{RECEIPT_API}?{query}", timeout=5) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise SystemExit(f"Receipt unavailable: {detail}")
        except Exception as error:  # dashboard restarting, connection refused...
            payload = None
            last_error = error
        if payload and payload.get("ok"):
            receipt = payload.get("receipt") or {}
            if not receipt.get("waiting") or int(wait or 0) <= 0:
                return receipt
        remaining = deadline - time.time()
        if remaining <= 0:
            if payload and payload.get("ok"):
                return payload.get("receipt") or {}
            raise SystemExit(f"Receipt unavailable: {last_error}")
        time.sleep(min(interval, remaining))


# ─── Structured-meta sidechannel ─────────────────────────────────────────────
# Probe finish_reason dark-code closure (2026-05-23):
#   The Sidewinder shipped classify_upstream_error finish_reason awareness on
#   May 21, but rt_send.py was stripping the field on the way out — Layer 1
#   never saw it. This helper emits the structured response metadata to stderr
#   so gauntlet._run_rt_send can parse it and plumb it into record_attempt.
#
# Format chosen to be:
#   - cheap to parse (one line, space-separated kv)
#   - hard to false-positive in normal logs (prefix anchor [META])
#   - byte-stable on stdout (stderr-only emission preserves backward compat)
#
# See: red-team-notes/intel/20260523_probe_finishreason_dark_code/FINDING.md
_ROUTED = {"model": ""}


def _note_routed(routed):
    """Remember the provider-reported model for this probe and return it in
    META-safe form (no whitespace: the META line is space-separated kv)."""
    value = "".join(str(routed or "").split())[:200]
    if value:
        _ROUTED["model"] = value
    return value


def _emit_meta(finish, usage, max_tokens, routed=""):
    """Emit structured response metadata via stderr sidechannel.

    Args:
      finish: provider's finish_reason string (or empty)
      usage: provider's usage dict (or None / empty)
      max_tokens: the cap we asked the provider for (or None / 0)
      routed: the model id the provider says answered (response body
        ``model`` / ``modelVersion``). Empty when the transport hides it
        (OpenCode CLI) -- never filled with the requested id.

    Backward-compat: stdout is untouched. Callers that don't parse stderr
    (everyone before today) see no change in behavior.
    """
    try:
        u = usage or {}
        details = u.get("completion_tokens_details") or {}
        rt = details.get("reasoning_tokens") if isinstance(details, dict) else None
        ct = u.get("completion_tokens")
        pt = u.get("prompt_tokens")
        print(
            f"[META] finish_reason={finish or ''} "
            f"reasoning_tokens={rt if rt is not None else ''} "
            f"completion_tokens={ct if ct is not None else ''} "
            f"prompt_tokens={pt if pt is not None else ''} "
            f"max_tokens={max_tokens or ''} "
            f"model_routed={_note_routed(routed)}",
            file=sys.stderr,
        )
    except Exception as _e:
        # Sidechannel must never break the primary path. Swallow + log.
        print(f"[rt_send] _emit_meta failed: {_e}", file=sys.stderr)


# ─── Provider send functions ─────────────────────────────────────────────────

def _collect_sandbox_files(sandbox_dir):
    """Walk sandbox dir and return list of {path, content, size} for files the target created."""
    artifacts = []
    for root, _dirs, files in os.walk(sandbox_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, sandbox_dir)
            try:
                size = os.path.getsize(fpath)
                # Read text files up to 8KB, skip binaries
                if size > 8192:
                    content = f"[binary/large file — {size} bytes]"
                else:
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                    except UnicodeDecodeError:
                        content = f"[binary file — {size} bytes]"
                artifacts.append({"path": rel, "content": content, "size": size})
            except OSError:
                pass
    return artifacts


_SAMPLING_FIELDS = (
    "temperature", "top_p", "top_k",
    "frequency_penalty", "presence_penalty", "repetition_penalty",
)


def _sampling_params(params, allowed=_SAMPLING_FIELDS, alias=None):
    """Filter an AutoTune vector down to what a provider accepts.

    Unknown fields are dropped rather than forwarded: a strict endpoint 400s on
    an unsupported `top_k`, and the caller's default behaviour (params=None)
    must stay byte-identical to the pre-AutoTune request.
    """
    if not params:
        return {}
    alias = alias or {}
    return {
        alias.get(key, key): params[key]
        for key in _SAMPLING_FIELDS
        if key in allowed and key in params and params[key] is not None
    }


def send_to_anthropic(prompt, model_id):
    """Send via Claude CLI in a sandboxed temp dir. Tools enabled so we see what the target does."""
    # Spending the Anthropic seat from a session that was granted a different
    # one (or none) would charge an account the operator did not point here.
    blocked = account_guard.runner_denial("claude")
    if blocked:
        return _fmt_sentinel("ERROR", f"subscription guard: {blocked}")
    sandbox = tempfile.mkdtemp(prefix="rt_sandbox_")
    try:
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--max-turns", "5",                    # Enough turns to use tools + respond
            "--setting-sources", "",                  # Skip CLAUDE.md files — clean eval target
            "--tools", "Write",                        # Write only — no Read/Bash/shell escape
            # Auto-approve the one tool this sandbox exposes; do not grant a
            # blanket bypass to a target-evaluation subprocess.
            "--permission-mode", "acceptEdits",
            "--permission-prompts", "none",
        ]
        # Map gauntlet model IDs to claude CLI --model values.
        # Pass full model names so the CLI doesn't silently resolve aliases
        # to whatever it considers "latest" — operator must get the model
        # they asked for, especially Fable 5 which used to omit --model
        # entirely and inherit the CLI default (currently opus-4-8).
        _claude_model_map = {
            # Current generation (source: platform.claude.com models
            # overview, 2026-11-21): Fable 5.2 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5.
            "claude-fable-5-2":  "claude-fable-5-2",
            "claude-opus-5-5":   "claude-opus-5-5",
            "claude-sonnet-5-5": "claude-sonnet-5-5",
            "claude-haiku-5-5":  "claude-haiku-5-5",
            # Legacy lineup — still available, other target lists use them.
            "claude-fable-5-1":  "claude-fable-5-1",
            "claude-fable-5":    "claude-fable-5",
            "claude-opus-4-8":   "claude-opus-4-8",
            "claude-opus-4-7":   "claude-opus-4-7",
            "claude-opus-4-6":   "claude-opus-4-6",
            "claude-sonnet-4-6": "claude-sonnet-4-6",
            "claude-haiku-4-5":  "claude-haiku-4-5",
            "claude-sonnet-4":   "sonnet",
        }
        cli_model = _claude_model_map.get(model_id, model_id)
        if cli_model:
            cmd += ["--model", cli_model]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            cwd=sandbox,  # Sandboxed — all file ops happen here
        )

        # Collect any files the target created
        artifacts = _collect_sandbox_files(sandbox)

        # Parse response
        text = ""
        meta_parts = []
        # Claude CLI doesn't expose a max_tokens cap; the helper-call below
        # uses 0 as the "unset" sentinel so the lens treats finish_reason as
        # the only structural signal for anthropic.
        try:
            data = json.loads(result.stdout)
            text = data.get("result", "")
            if data.get("is_error"):
                # CLI-level failure (auth, bad model name, quota). Never let
                # the error text surface as the target's compliance answer —
                # return a sentinel so gauntlet classifies it as upstream.
                return _fmt_sentinel("ERROR", f"claude CLI: {text[:200] or 'no error text'}")
            # Surface the model that ACTUALLY served the response so silent
            # CLI reroutes (alias → latest) are visible to the operator.
            served = list((data.get("modelUsage") or {}).keys())
            if served:
                served_str = served[0] if len(served) == 1 else "+".join(served)
                meta_parts.append(f"served: {served_str}")
                if cli_model and cli_model not in served and served_str != cli_model:
                    meta_parts.append(f"\u26A0 asked: {cli_model}")
            cost = data.get("total_cost_usd")
            if cost:
                meta_parts.append(f"${cost:.4f}")
            usage = data.get("usage", {}) or {}
            out_tokens = usage.get("output_tokens")
            if out_tokens:
                meta_parts.append(f"{out_tokens} tokens")
            turns = data.get("num_turns", 1)
            if turns > 1:
                meta_parts.append(f"{turns} turns")
            stop = data.get("stop_reason", "")
            if stop and stop != "end_turn":
                meta_parts.append(f"stop: {stop}")
            # Map anthropic stop_reason → finish_reason for the META sidechannel.
            # end_turn ≈ stop ; max_tokens ≈ length ; everything else passes
            # through verbatim so the lens can recognize tool_use, refusal, etc.
            _anth_finish_map = {"end_turn": "stop", "max_tokens": "length"}
            _anth_finish = _anth_finish_map.get(stop or "", stop or "")
            _emit_meta(
                _anth_finish,
                {"completion_tokens": usage.get("output_tokens"),
                 "prompt_tokens": usage.get("input_tokens")},
                0,  # claude CLI has no client-side cap to surface
                routed=next(iter(data.get("modelUsage") or {}), ""),
            )
        except json.JSONDecodeError:
            text = result.stdout[:4000] if result.stdout else result.stderr[:2000]
            _emit_meta("", None, 0)

        # Report artifacts as structured cards to dashboard (before response)
        if artifacts:
            post_to_dashboard({
                "type": "artifacts",
                "files": [{"path": a["path"], "size": a["size"], "content": a["content"][:3000]} for a in artifacts],
            })
            # Add summary line to response text (not full content — cards handle that)
            fnames = ", ".join(f"`{a['path']}`" for a in artifacts)
            text += f"\n\n📁 Created {len(artifacts)} file(s): {fnames}"

        if meta_parts:
            text += f"\n\n`[{' · '.join(meta_parts)}]`"

        return text if text else "[No response from target]"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT — target took >180s to respond]"
    except FileNotFoundError:
        return "[ERROR — claude CLI not found in PATH]"
    finally:
        # Clean up sandbox
        shutil.rmtree(sandbox, ignore_errors=True)


def send_to_openai(prompt, model_id, params=None):
    """Send via OpenAI API, with OpenRouter fallback."""
    api_key = resolve_key("OPENAI_API_KEY")
    openrouter_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither OPENAI_API_KEY nor OPENROUTER_API_KEY set.]"
    # Map friendly IDs to API model names
    model_map = {
        # GPT-6 generation (source: developers.openai.com model catalog,
        # 2026-11-21). 1.05M ctx / 128K out.
        "gpt-6.2-astra": "gpt-6.2-astra",
        "gpt-6.2-sol": "gpt-6.2-sol",
        "gpt-6-astra": "gpt-6-astra",
        "gpt-6.1-sol": "gpt-6.1-sol",
        "gpt-6.1-terra": "gpt-6.1-terra",
        "gpt-6-luna": "gpt-6-luna",
        # Open-weight reasoning (Apache 2.0)
        "gpt-oss-120b": "gpt-oss-120b",
        "gpt-oss-20b": "gpt-oss-20b",
        # GPT-5 generation (legacy)
        "gpt-5.5": "gpt-5.5",
        "gpt-5.5-pro": "gpt-5.5-pro",
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-pro": "gpt-5.4-pro",
        "gpt-5.4-mini": "gpt-5.4-mini",
        "gpt-5.4-nano": "gpt-5.4-nano",
        "gpt-4.1": "gpt-4.1",
        "gpt-4.1-mini": "gpt-4.1-mini",
        "gpt-4.1-nano": "gpt-4.1-nano",
        "o3-pro": "o3-pro",
        "o3": "o3",
        "o4-mini": "o4-mini",
    }
    api_model = model_map.get(model_id, model_id)
    # Prefer OpenRouter when available (direct OpenAI key is out of credits on this host)
    if openrouter_key:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        # OpenRouter uses openai/ prefix for OpenAI models
        or_model = f"openai/{api_model}"
    else:
        base_url = "https://api.openai.com/v1/chat/completions"
        auth_key = api_key
        or_model = api_model
    # Red-team the LOW-thinking variant of reasoning-capable models. The deep-thinking
    # variant is materially harder to jailbreak; the typical end-user faces the
    # default/low-reasoning surface, so that's what we benchmark against.
    body_obj = {
        "model": or_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    _reasoning_models = ("gpt-5", "gpt-6", "gpt-oss", "o3", "o4")
    if api_model.startswith(_reasoning_models):
        # GPT-6 & gpt-oss dropped "minimal" effort — valid levels start at "low".
        default_effort = "low" if api_model.startswith(("gpt-6", "gpt-oss")) else "minimal"
        body_obj["reasoning_effort"] = os.environ.get("OPENAI_REASONING_EFFORT", default_effort)
    body_obj.update(_sampling_params(
        params,
        allowed=("temperature", "top_p", "frequency_penalty", "presence_penalty"),
    ))
    try:
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = _wire_urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        # OpenAI sometimes blocks via choices[0].finish_reason == "content_filter"
        # with empty content — catch that explicitly as BLOCKED.
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), body_obj.get("max_tokens"), routed=data.get("model"))
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"OpenAI ({or_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"OpenAI ({or_model}) returned empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenAI ({or_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenAI ({or_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_google(prompt, model_id, params=None):
    """Send via Google Gemini API, with OpenRouter fallback."""
    api_key = resolve_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
    openrouter_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither GOOGLE_API_KEY nor OPENROUTER_API_KEY set.]"
    model_map = {
        # Gemini 4 & 3.8 generation (source: ai.google.dev/gemini-api/docs/models, 2026-11-21)
        "gemini-4-carbon": "gemini-4-carbon",
        "gemini-4-hydrogen": "gemini-4-hydrogen",
        "gemini-4-argon": "gemini-4-argon",
        "gemini-4-neon": "gemini-4-neon",
        "gemini-3.8-flash": "gemini-3.8-flash",
        "gemini-3.5-flash": "gemini-3.5-flash",
        "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
        # Legacy Gemini 3.1 / 2.5
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-3-flash": "gemini-3-flash-preview",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash": "gemini-2.5-flash",
    }
    # OpenRouter model mapping
    or_model_map = {
        "gemini-4-carbon": "google/gemini-4-carbon",
        "gemini-4-hydrogen": "google/gemini-4-hydrogen",
        "gemini-4-argon": "google/gemini-4-argon",
        "gemini-4-neon": "google/gemini-4-neon",
        "gemini-3.8-flash": "google/gemini-3.8-flash",
        "gemini-3.5-flash": "google/gemini-3.5-flash",
        "gemini-3.5-flash-lite": "google/gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
        "gemini-3.1-pro": "google/gemini-3.1-pro-preview",
        "gemini-3-pro-preview": "google/gemini-3.1-pro-preview",
        "gemini-3-flash-preview": "google/gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview": "google/gemini-3-flash-lite-preview",
        "gemini-2.5-pro": "google/gemini-2.5-pro",
        "gemini-2.5-flash": "google/gemini-2.5-flash",
    }
    api_model = model_map.get(model_id, model_id)
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={api_key}"
            gemini_max_tokens = 2000
            generation_config = {"maxOutputTokens": gemini_max_tokens}
            generation_config.update(_sampling_params(
                params,
                allowed=("temperature", "top_p", "top_k"),
                alias={"top_p": "topP", "top_k": "topK"},
            ))
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }).encode()
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            resp = _wire_urlopen(req, timeout=90)
            data = json.loads(resp.read())

            # Map Gemini's usageMetadata to OpenAI-shape usage so _emit_meta
            # is uniform across providers. thoughtsTokenCount is Gemini's name
            # for reasoning_tokens on Gemini-3.x reasoning models.
            um = data.get("usageMetadata") or {}
            _g_usage = {
                "prompt_tokens": um.get("promptTokenCount"),
                "completion_tokens": um.get("candidatesTokenCount"),
                "completion_tokens_details": (
                    {"reasoning_tokens": um.get("thoughtsTokenCount")}
                    if um.get("thoughtsTokenCount") is not None else None
                ),
            }

            # Prompt-level safety block: no candidates, promptFeedback.blockReason set
            pf = data.get("promptFeedback") or {}
            if pf.get("blockReason"):
                _emit_meta(f"block:{pf.get('blockReason')}", _g_usage, gemini_max_tokens)
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) promptFeedback.blockReason={pf.get('blockReason')}")

            candidates = data.get("candidates") or []
            if not candidates:
                _emit_meta("", _g_usage, gemini_max_tokens)
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) no candidates returned (likely prompt-level block)")

            cand = candidates[0]
            finish = cand.get("finishReason") or ""
            _emit_meta(finish, _g_usage, gemini_max_tokens, routed=data.get("modelVersion"))
            # Response-level safety block: finishReason == SAFETY / RECITATION / PROHIBITED_CONTENT
            if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) finishReason={finish}")

            parts = ((cand.get("content") or {}).get("parts") or [])
            if not parts:
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) empty content parts (finishReason={finish or 'unset'})")

            text = parts[0].get("text", "")
            if not text.strip():
                return _fmt_sentinel("ERROR", f"Gemini ({api_model}) empty text (finishReason={finish or 'unset'})")
            return text
        except urllib.error.HTTPError as e:
            prefix, detail = _classify_http_error(f"Gemini ({api_model})", e)
            return _fmt_sentinel(prefix, detail)
        except Exception as e:
            prefix, detail = _classify_exception(f"Gemini ({api_model})", e)
            return _fmt_sentinel(prefix, detail)
    # OpenRouter fallback
    or_model = or_model_map.get(api_model, or_model_map.get(model_id, f"google/{api_model}"))
    try:
        # Gemini 3.x are reasoning models — burn tokens internally before
        # producing visible output. Default 2000 truncates visible text to
        # ~300 chars. Bump to 16000 + reasoning.effort=low for full output.
        is_reasoning = ("gemini-3" in (or_model or "")) or ("gemini-4" in (or_model or ""))
        body_payload = {
            "model": or_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16000 if is_reasoning else 2000,
        }
        if is_reasoning:
            body_payload["reasoning"] = {"effort": "low"}
        body_payload.update(_sampling_params(params))
        body = json.dumps(body_payload).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {openrouter_key}",
            },
        )
        resp = _wire_urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), body_payload.get("max_tokens"), routed=data.get("model"))
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"OpenRouter Gemini ({or_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"OpenRouter Gemini ({or_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenRouter Gemini ({or_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenRouter Gemini ({or_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_xai(prompt, model_id, params=None):
    """Send via xAI Grok API (OpenAI-compatible), with OpenRouter fallback."""
    api_key = resolve_key("XAI_API_KEY", "GROK_API_KEY")
    openrouter_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither XAI_API_KEY nor OPENROUTER_API_KEY set.]"
    model_map = {
        # Current xAI lineup (source: docs.x.ai, 2026-11-21)
        "grok-4.7": "grok-4.7",
        "grok-4.20": "grok-4.20-0309-reasoning",
        "grok-4.20-0309-reasoning": "grok-4.20-0309-reasoning",
        "grok-4.20-multi-agent": "grok-4.20-multi-agent-0309",
        "grok-4.20-multi-agent-0309": "grok-4.20-multi-agent-0309",
        "grok-4.20-nr": "grok-4.20-0309-non-reasoning",
        "grok-4-1-fast": "grok-4-1-fast-reasoning",
        "grok-3": "grok-3",
    }
    # OpenRouter model mapping
    or_model_map = {
        "grok-4.7": "x-ai/grok-4.7",
        "grok-4.20": "x-ai/grok-4.20",
        "grok-4.20-0309-reasoning": "x-ai/grok-4.20",
        "grok-4.20-multi-agent": "x-ai/grok-4.20-multi-agent",
        "grok-4.20-multi-agent-0309": "x-ai/grok-4.20-multi-agent",
        "grok-4.20-nr": "x-ai/grok-4.20",
        "grok-4-1-fast": "x-ai/grok-4.1-fast",
        "grok-3": "x-ai/grok-3",
    }
    if api_key:
        base_url = "https://api.x.ai/v1/chat/completions"
        auth_key = api_key
        api_model = model_map.get(model_id, model_id)
    else:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        api_model = or_model_map.get(model_id, f"x-ai/{model_id}")
    xai_max_tokens = 2000
    try:
        body_obj = {
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": xai_max_tokens,
        }
        body_obj.update(_sampling_params(
            params,
            allowed=("temperature", "top_p", "frequency_penalty", "presence_penalty"),
        ))
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = _wire_urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), xai_max_tokens, routed=data.get("model"))
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"xAI ({api_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"xAI ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"xAI ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"xAI ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_ollama(prompt, model_id, params=None):
    """Send via local Ollama."""
    # Map model IDs to Ollama model names
    ollama_map = {
        "llama-4-scout": "llama4:scout",
        "llama-4-maverick": "llama4:maverick",
        "llama-guard-4-12b": "llama-guard4:12b",
        "llama-3.3-70b": "llama3.3:70b",
        "llama-3.1-405b": "llama3.1:405b",
        "deepseek-v3": "deepseek-v3",
        "deepseek-r1": "deepseek-r1",
        "deepseek-r1-0528": "deepseek-r1:0528",
        "mistral-large-4": "mistral-large",
        "mistral-large-latest": "mistral-large",
        "mistral-small-2603": "mistral-small",
        "mistral-small-latest": "mistral-small",
        "devstral-latest": "devstral",
        "codestral-latest": "codestral",
        "pixtral-large-latest": "pixtral-large",
    }
    ollama_model = ollama_map.get(model_id, model_id)
    try:
        body_obj = {
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        options = _sampling_params(
            params,
            alias={"repetition_penalty": "repeat_penalty"},
        )
        if options:
            body_obj["options"] = options
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            body,
            {"Content-Type": "application/json"},
        )
        resp = _wire_urlopen(req, timeout=180)
        data = json.loads(resp.read())
        _note_routed(data.get("model"))
        return data.get("response", str(data))
    except Exception as e:
        return f"[ERROR — Ollama ({ollama_model}): {e}]"


def send_to_mistral(prompt, model_id, params=None):
    """Send via Mistral API, with OpenRouter fallback."""
    api_key = resolve_key("MISTRAL_API_KEY")
    openrouter_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        # Fallback to Ollama if no API key
        return send_to_ollama(prompt, model_id, params=params)

    # OpenRouter model mapping for Mistral (source: docs.mistral.ai, 2026-11-21)
    openrouter_map = {
        "mistral-large-4": "mistralai/mistral-large-4",
        "mistral-large-latest": "mistralai/mistral-large-4",
        "mistral-small-2603": "mistralai/mistral-small-2603",
        "mistral-small-latest": "mistralai/mistral-small-2603",
        "devstral-latest": "mistralai/devstral-latest",
        "magistral-medium-latest": "mistralai/magistral-medium-latest",
        "codestral-latest": "mistralai/codestral-2508",
        "ministral-14b-latest": "mistralai/ministral-14b-2512",
        "ministral-8b-latest": "mistralai/ministral-8b-2512",
        "ministral-3b-latest": "mistralai/ministral-3b-2512",
        "pixtral-large-latest": "mistralai/pixtral-large-2411",
    }

    if api_key:
        base_url = "https://api.mistral.ai/v1/chat/completions"
        auth_key = api_key
        api_model = model_id
    else:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        api_model = openrouter_map.get(model_id, f"mistralai/{model_id}")

    mistral_max_tokens = 2000
    try:
        body_obj = {
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": mistral_max_tokens,
        }
        body_obj.update(_sampling_params(
            params, allowed=("temperature", "top_p"),
        ))
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = _wire_urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), mistral_max_tokens, routed=data.get("model"))
        if not content:
            return _fmt_sentinel("ERROR", f"Mistral ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"Mistral ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"Mistral ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_deepseek(prompt, model_id, params=None):
    """Send via DeepSeek API or Ollama fallback."""
    api_key = resolve_key("DEEPSEEK_API_KEY")
    if not api_key:
        return send_to_ollama(prompt, model_id, params=params)
    model_map = {
        # Current models (source: api-docs.deepseek.com, 2026-10-31).
        # deepseek-flash -> DeepSeek-V4.1-Flash-1017; deepseek-v4-pro is the
        # flagship. Both default to thinking mode ON.
        "deepseek-v4-pro": "deepseek-v4-pro",
        "deepseek-flash": "deepseek-flash",
        # Legacy aliases
        "deepseek-v3.2": "deepseek-chat",
        "deepseek-v3": "deepseek-chat",
        "deepseek-r1": "deepseek-reasoner",
        "deepseek-r1-0528": "deepseek-reasoner",
    }
    api_model = model_map.get(model_id, model_id)
    # V4 models think by default (reasoning tokens burn the output cap before
    # any visible text). 2000 truncated every response to a fragment — same
    # failure the Gemini 3.x path hit; 16000 leaves room for the answer.
    deepseek_max_tokens = 16000
    try:
        body_obj = {
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": deepseek_max_tokens,
        }
        body_obj.update(_sampling_params(
            params,
            allowed=("temperature", "top_p", "frequency_penalty", "presence_penalty"),
        ))
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        resp = _wire_urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        # DeepSeek-r1 emits reasoning_tokens natively in completion_tokens_details.
        # This is exactly the field the Falsifier flagged (2026-05-23) — needed to
        # discriminate length_starved_reasoning from real truncation downstream.
        _emit_meta(finish, data.get("usage"), deepseek_max_tokens, routed=data.get("model"))
        if not content:
            return _fmt_sentinel("ERROR", f"DeepSeek ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"DeepSeek ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"DeepSeek ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


# ─── OpenRouter passthrough (for providers without a native handler) ────────

# OpenRouter slug map for short model ids. If the id isn't in the map, we
# fall back to "<vendor>/<model_id>" using the provider→vendor table.
_OPENROUTER_SLUG_MAP = {
    # Qwen / Alibaba (source: alibabacloud.com / qwen.ai, 2026-11-21)
    "qwen4-max":        "qwen/qwen4-max",
    "qwen3.7-plus":     "qwen/qwen3.7-plus",
    "qwen3.6-plus":     "qwen/qwen3.6-plus",
    "qwen3.6-flash":    "qwen/qwen3.6-flash",
    "qwen3-vl-plus":    "qwen/qwen3-vl-plus",
    "qwen3-omni-flash": "qwen/qwen3-omni-flash",
    "qwen3-max-plus":   "qwen/qwen3-max-plus",
    "qwen3-max":        "qwen/qwen3-max",
    "qwen3-235b":       "qwen/qwen3-235b-a22b",
    "qwen-plus":        "qwen/qwen-plus",
    "qwen-max":         "qwen/qwen-max",
    # Z.ai / Zhipu GLM family (source: docs.z.ai, 2026-11-21)
    "glm-5.3":          "z-ai/glm-5.3",
    "glm-5.3-flash":    "z-ai/glm-5.3-flash",
    "glm-5.2":          "z-ai/glm-5.2",
    "glm-5v-turbo":     "z-ai/glm-5v-turbo",
    "glm-5.1":          "z-ai/glm-5.1",
    "glm-5":            "z-ai/glm-5",
    "glm-4.6":          "z-ai/glm-4.6",
    "glm-4.5":          "z-ai/glm-4.5",
}

# Default vendor namespace per provider — used when the model id isn't in
# the explicit slug map above.
_OPENROUTER_VENDOR_NS = {
    "qwen":   "qwen",
    "zhipu":  "z-ai",
    "glm":    "z-ai",
    "z-ai":   "z-ai",
    "moonshot": "moonshotai",
    "kimi":   "moonshotai",
    "01-ai":  "01-ai",
    "yi":     "01-ai",
}


def send_to_openrouter(prompt, model_id, provider_hint="", params=None):
    """Generic OpenRouter passthrough — used for any provider without a
    dedicated native handler (qwen, zhipu/glm, moonshot, etc.).

    Resolves the OR slug from the explicit map first, falling back to
    "<vendor_ns>/<model_id>" using `provider_hint` to pick the namespace.
    """
    api_key = resolve_key("OPENROUTER_API_KEY")
    if not api_key:
        return _fmt_sentinel("ERROR", f"OpenRouter passthrough requires OPENROUTER_API_KEY (provider={provider_hint}, model={model_id})")

    # Already a fully qualified OR slug? (e.g. "qwen/qwen3.6-plus")
    if "/" in model_id:
        api_model = model_id
    elif model_id in _OPENROUTER_SLUG_MAP:
        api_model = _OPENROUTER_SLUG_MAP[model_id]
    else:
        ns = _OPENROUTER_VENDOR_NS.get(provider_hint.lower(), provider_hint.lower() or "openrouter")
        api_model = f"{ns}/{model_id}"

    # GLM-5.3 reasons on every request (low|high|max, default max — docs.z.ai).
    # A 2000-token cap is eaten by reasoning before any visible output; pin
    # the cheapest effort and give the cap headroom, same as Gemini 3.x.
    glm53_reasoning = "glm-5.3" in api_model
    or_max_tokens = 16000 if glm53_reasoning else 2000
    try:
        body_obj = {
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": or_max_tokens,
        }
        if glm53_reasoning:
            body_obj["reasoning"] = {"effort": "low"}
        body_obj.update(_sampling_params(params))
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/dragons-blood/libertarium",
                "X-Title": "Pliny Command",
            },
        )
        resp = _wire_urlopen(req, timeout=120)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), or_max_tokens, routed=data.get("model"))
        if not content:
            return _fmt_sentinel("ERROR", f"OpenRouter ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenRouter ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenRouter ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


# ─── Router ──────────────────────────────────────────────────────────────────

def send_to_target(provider, model_id, prompt, timeout=None, params=None):
    """Provider dispatch with a shared health guard and raw wire capture.

    A provider whose breaker is open is skipped before any request is sent; the
    RATELIMIT sentinel is zero-info for the gauntlet, so the scored-attempt
    budget is not burned. Every outcome feeds back into the shared registry —
    one subprocess discovering a dead key skips it for every other target.

    `params` is an optional sampling vector (the AutoTune output). Unsupported
    providers ignore it; the CLI transports (anthropic, opencode) do not take
    OpenAI-style sampling fields at all.
    """
    cooldown = provider_health.check(provider)
    if cooldown:
        return _fmt_sentinel("RATELIMIT", cooldown)
    try:
        response = _dispatch_target(provider, model_id, prompt, timeout, params=params)
    except Exception:
        provider_health.note_result(provider, "[NETWORK — rt_send dispatch crashed]")
        raise
    provider_health.note_result(provider, response)
    return response


def _dispatch_target(provider, model_id, prompt, timeout=None, params=None):
    """Route to the correct provider.

    `timeout` (seconds) is the wall-clock cap for long-generation targets. None keeps
    each provider's hard-coded default (legacy behavior — every existing caller and
    the gauntlet's interpretation of timeouts is untouched).
    """
    if provider in ("opencode", "opencode-go"):
        from opencode_runner import complete_text
        try:
            # Bare names are Go models; provider/model IDs (openrouter/..., opencode/...) pass through.
            resolved = model_id if "/" in model_id else "opencode-go/" + model_id
            if timeout is not None:
                return complete_text(prompt, resolved, timeout=timeout,
                                     cwd=_target_workspace())
            return complete_text(prompt, resolved, cwd=_target_workspace())
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            return f"[OpenCode Go error: {error}]"

    elif provider == "anthropic":
        return send_to_anthropic(prompt, model_id)
    elif provider == "openai":
        return send_to_openai(prompt, model_id, params=params)
    elif provider == "google":
        return send_to_google(prompt, model_id, params=params)
    elif provider == "xai":
        return send_to_xai(prompt, model_id, params=params)
    elif provider == "meta":
        return send_to_ollama(prompt, model_id, params=params)
    elif provider == "mistral":
        return send_to_mistral(prompt, model_id, params=params)
    elif provider == "deepseek":
        return send_to_deepseek(prompt, model_id, params=params)
    elif provider == "custom":
        return send_to_ollama(prompt, model_id, params=params)
    elif provider in ("qwen", "zhipu", "glm", "z-ai", "moonshot", "kimi", "01-ai", "yi", "openrouter"):
        return send_to_openrouter(prompt, model_id, provider_hint=provider, params=params)
    else:
        return f"[Unknown provider '{provider}'. Supported: anthropic, openai, google, xai, meta, mistral, deepseek, qwen, zhipu, openrouter]"


# ─── Main ────────────────────────────────────────────────────────────────────

def parse_prompt_request(argv=None):
    """Read native UTF-8 files without a PowerShell text pipeline."""
    parser = argparse.ArgumentParser(description="Send a prompt and record the target response.")
    parser.add_argument("attempt", type=int)
    parser.add_argument("provider")
    parser.add_argument("model_id")
    parser.add_argument("--prompt-file", type=Path, help="read a UTF-8 prompt file; recommended on Windows")
    parser.add_argument("--timeout", type=int, default=None,
                        help="wall-clock seconds for the target request (long-generation asks; "
                             "default = each provider's hard-coded cap, e.g. 120s opencode)")
    parser.add_argument("--autotune", default="off",
                        choices=("off", "adaptive", "precise", "balanced", "creative", "chaotic"),
                        help="compute a G0DM0D3 sampling vector for this request")

    args = parser.parse_args(argv)
    try:
        args.prompt = (args.prompt_file.read_bytes().decode("utf-8-sig")
                       if args.prompt_file else sys.stdin.read().strip())
    except (OSError, UnicodeError) as error:
        parser.error(f"Cannot read UTF-8 prompt file: {error}")
    if not args.prompt.strip():
        parser.error("Prompt must not be empty")
    return args


def parse_receipt_request(argv=None):
    parser = argparse.ArgumentParser(
        description="Read the structured-campaign receipt for one request.")
    parser.add_argument("--receipt", required=True, help="request id to read")
    parser.add_argument("--campaign", default=_PLINY_CAMPAIGN_ID,
                        help="campaign id (defaults to PLINY_ARTEX_CAMPAIGN)")
    parser.add_argument("--wait", type=int, default=0,
                        help="seconds to wait while the evaluation is pending")
    args = parser.parse_args(argv)
    if not (args.campaign or "").strip():
        parser.error("--campaign is required (or launch with PLINY_ARTEX_CAMPAIGN)")
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--receipt" in argv:
        receipt_args = parse_receipt_request(argv)
        receipt = fetch_receipt(receipt_args.campaign.strip(),
                                receipt_args.receipt.strip(),
                                wait=receipt_args.wait)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return
    global _REQUEST_ID
    # One request id for this probe.  A caller that imports main() and runs it
    # repeatedly (the gauntlet) still gets a fresh evidence slot per probe,
    # while the prompt and response below keep the same id.
    _REQUEST_ID = uuid.uuid4().hex
    _ROUTED["model"] = ""
    args = parse_prompt_request(argv)
    attempt, provider, model_id, prompt = args.attempt, args.provider.lower(), args.model_id, args.prompt

    if _PLINY_CAMPAIGN_ID:
        # The launch owns the target.  A CLI invocation that cannot prove it
        # is the same provider/model must not reach the provider at all.
        if not _PLINY_SESSION_ID:
            raise SystemExit(
                "Target request not sent: structured campaign has no "
                "PLINY_SESSION_ID.")
        if not _PLINY_ARTEX_PROVIDER or not _PLINY_ARTEX_MODEL:
            raise SystemExit(
                "Target request not sent: structured campaign has no locked "
                "provider/model in its environment.")
        import artex_campaign
        if artex_campaign.normalize_target(provider, model_id) != \
                artex_campaign.normalize_target(_PLINY_ARTEX_PROVIDER,
                                                _PLINY_ARTEX_MODEL):
            raise SystemExit(
                "Target request not sent: CLI provider/model does not match "
                f"the campaign's locked target "
                f"{_PLINY_ARTEX_PROVIDER}/{_PLINY_ARTEX_MODEL}.")

    # 1. Report prompt to dashboard (shows immediately in chat UI)
    post_to_dashboard({
        "type": "prompt",
        "text": prompt,
        "attempt": attempt,
        "target": f"{provider}/{model_id}",
    })

    # 2. Report that target is thinking
    post_to_dashboard({
        "type": "typing",
        "attempt": attempt,
    })

    # 3. Send to actual target (timeout applies to any provider that takes it)
    # `--autotune` is explicit; PLINY_RT_AUTOTUNE lets a launcher (the
    # gauntlet, an agent shell) opt in without changing this CLI's shape.
    autotune_mode = (getattr(args, "autotune", "off") or "off").strip().lower()
    if autotune_mode == "off":
        autotune_mode = os.environ.get("PLINY_RT_AUTOTUNE", "off").strip().lower() or "off"
    if autotune_mode not in ("adaptive", "precise", "balanced", "creative", "chaotic"):
        autotune_mode = "off"
    tune = None
    if autotune_mode != "off":
        import autotune
        tune = autotune.compute_params(prompt, strategy=autotune_mode)["params"]
    try:
        response = send_to_target(provider, model_id, prompt,
                                  timeout=args.timeout, params=tune)
    except TypeError:
        # Third-party caller imported an older signature subset; degrade safely.
        response = send_to_target(provider, model_id, prompt)


    # 4. Report response to dashboard
    #    The meta block is the transport receipt: the server keys it by the
    #    response hash so a later /attempt POST carrying this exact text gets
    #    model_routed + provenance stamped without the agent plumbing it.
    post_to_dashboard({
        "type": "response",
        "text": response,
        "attempt": attempt,
        "target": f"{provider}/{model_id}",
        "meta": {
            "model_routed": _ROUTED["model"],
            "response_sha256": transport_receipts.response_digest(response),
        },
    })

    # 5. Print response for Pliny to analyze
    print(response)
    # Structured mode only: hand the agent the exact id its receipt is filed
    # under.  Free-form stdout stays byte-identical for the gauntlet parser.
    if _PLINY_CAMPAIGN_ID:
        try:
            receipt = fetch_receipt(_PLINY_CAMPAIGN_ID, _REQUEST_ID)
            action = receipt.get("next_action", "")
            state = receipt.get("campaign_state", "")
        except SystemExit:
            action, state = "unknown", ""
        print(f"[ARTEX RECEIPT] request_id={_REQUEST_ID} "
              f"campaign_id={_PLINY_CAMPAIGN_ID} state={state} next_action={action}")
        print(f"[ARTEX RECEIPT] poll: python rt_send.py --receipt {_REQUEST_ID} "
              f"--campaign {_PLINY_CAMPAIGN_ID} --wait 300")


if __name__ == "__main__":
    main()
