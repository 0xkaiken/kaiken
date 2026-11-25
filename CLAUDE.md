# CLAUDE.md — Pliny command system

### Evidence-Driven Verification

Prioritize empirical testing and executable evidence over abstract adversarial
code reviews. Define invariants, run targeted tests, and validate behavior through
reproducible failures. Use formal verification where practical. Avoid speculative
nitpicks and review loops without actionable findings. Every reported issue should
include concrete evidence or a reproducible test case.

## Secrets — architecture

Provider keys come from the server environment only:

1. **`.env` in the repo root** — loaded into the server environment by
   `_load_env_file()` in `server.py` at startup. Variables already set in the
   shell win. Placeholder values ending in `...` are ignored.
2. **`state/secrets.json`** — the dashboard secrets card (`/api/settings/secrets`)
   manages the GitHub PAT/repo/branch records here behind `_secrets_lock`; the
   API only ever returns masked values (last four characters). The same file
   may hold provider entries written by hand. `_sync_env_from_secrets()`
   mirrors every recognised provider entry into the server's own process
   environment for server-side tools (the Gauntlet judge, tweet drafting,
   GitHub operations).
3. **The shell that launched the server** — an explicit environment variable
   is always authoritative.

Agent subprocesses do not inherit any of it; see below.

There is no macOS Keychain sidecar any more. `pliny_secrets_client.py` is an
environment-backed shim with the old five-function surface; it reads the
process environment first and `state/secrets.json` second, and never returns a
key value to a caller. `pliny_secrets_sidecar.py`, `pliny_secrets_setup.py` and
the `pliny/*` Keychain entries do not exist in this tree.

### Agent credential boundary

Agent subprocesses (Claude Code, Codex, OpenCode) do **not** inherit the
server's provider keys. `Session._agent_environment()` uses
`agent_secrets.filtered_env()` to drop credential-shaped variables and adds:

- `PLINY_AGENT_TOKEN` — a per-server scoped bearer for local dashboard calls
  (accepted by `_check_auth` alongside `PLINY_AUTH_TOKEN`),
- `PLINY_AGENT_KEY_FILE` — only for red-team missions that call providers
  directly. It points at a mode-0600 JSON file under `state/agent_keys/` (on
  POSIX; Windows inherits the user-profile ACL) that is created for the
  session and deleted when the session ends.

The key file is **scoped to the target**. `agent_secrets.provider_key_names()`
maps a provider to the exact names its transport reads
(`agent_secrets.PROVIDER_KEY_ENVS`, mirroring `rt_send.send_to_target()`), and
`write_session_key_file()` defaults to an empty name set. A session with no
declared provider gets no file at all rather than every configured key.
Launch paths that know the target pass `provider=` (`launch_session`,
`gauntlet._launch_agent`); the red-team UI already sends it in the `redteam`
block. `PLINY_REDTEAM_WIDE_KEYS=1` restores the old all-keys behaviour and is
logged in the session log when used.

`rt_send.py`, `rt_send_or.py` and `rt_hermes.py` resolve provider keys through
`agent_secrets.resolve_key()`: process environment first, session key file
second. Keep it that way — do not pass keys as CLI arguments or put them back
into a spawned agent's environment.

The carrier lab follows the same rule: `lab_api.start_run()` spawns
`jailbreak_lab.py` with `agent_secrets.filtered_env()`, the target provider's
key file, and `PLINY_LAB_SCOPED_ENV=1`. `jailbreak_lab.load_env_file()` honours
that marker — without it the subprocess would re-read `.env` and undo the
scrub. `/api/lab/start` and `/api/lab/stop` are operator-only (`_require_auth`);
the read endpoints stay open to the dashboard.

**Never:**

- read, print, or copy a value from `state/secrets.json` or `.env` into output;
- add an endpoint that returns a raw secret (the masked view is the contract);
- pass `PLINY_AUTH_TOKEN` to an agent process — nested runners use
  `PLINY_AGENT_TOKEN`;
- weaken `agent_secrets.filtered_env()` to "copy everything" for convenience.

**Allowed:**

- read the source of `agent_secrets.py`, `pliny_secrets_client.py` and the
  secrets helpers in `server.py` — they contain retrieval logic, not values;
- edit `.env` / `state/secrets.json` through the dashboard or by hand;
- call `agent_secrets.resolve_key()` from a new red-team transport.

## Account usage boundary

**Under no circumstances will an agent or a test — on its own initiative — make
use of a user's specific Anthropic or GPT subscription accounts, unless the user
has explicitly requested to conduct operations using those accounts.**

The subscription paths in this tree are operator-owned, not agent-owned:

- the Claude Code CLI sign-in (`~/.claude/`, `claude.ai` OAuth session) and the
  Codex CLI ChatGPT sign-in (`~/.codex/auth.json`);
- any browser profile `pw_browser.py` can drive against `claude.ai` /
  `chatgpt.com` — a stored cookie is a live seat, not a fixture;
- any seat-bound consumer plan (Claude Pro/Max, ChatGPT Plus/Pro/Team) as
  opposed to a metered API key.

An agent that finds one of those reachable has found a *login*, not a
*permission*. So:

- Do not drive a subscription account to answer a question, verify a fix,
  benchmark a model, probe a rate limit, or "just check that it works."
- Do not treat `/api/auth/hermes/login`, the Codex login card, or a warm
  browser session as standing authorization to exercise it. The dashboard
  surfaces sign-in state so the *operator* can act on it.
- Do not write tests that authenticate through, or assert against, a real
  user's seat. Mock the transport, or use the API-key path.

**Use without asking:** the provider-key path above —
`agent_secrets.resolve_key()`, the per-session key file, the server
environment.

**Requires an explicit operator ask:** anything that spends a subscription
seat. "Use my Claude account for this" / "run it through Codex on my plan" →
proceed. Nothing else is implied, and one explicit ask does not extend to the
next session, the next test, or the next target.

### Enforcement

`account_guard.py` is the single source of truth. It resolves an **origin**
(`operator`, `automation`, `agent`, `test`) and a per-session **seat**
(`claude` → Anthropic, `codex` → OpenAI, empty for `opencode`), and answers
whether a runtime may start.

| Chokepoint | What it does |
| --- | --- |
| `server._require_api_access` | Stamps the request thread `operator` or `agent` from the token that authenticated it. The scoped agent token demotes to `agent`, which cannot spend a seat. |
| `server._require_auth` | Privileged endpoints stamp `operator` + `authorized`; this is the explicit ask that unlocks a seat. |
| `Session._start_inner` | `account_guard.require()` fails closed before any process spawn. A denial is an `error` session plus a `subscription_denied` audit record. |
| `agent_permissions.subscription_cli_deny_args` | Claude Code `--settings` deny rules for `Bash(claude:*)` / `Bash(codex:*)`, so an agent cannot type the CLI into its own shell. `PLINY_AGENT_SUBSCRIPTION_CLI_DENY=0` removes them. |
| `codex_runner`, `rt_send.send_to_anthropic` | `require_runner()` / `runner_denial()`: a session may only start the CLI for the seat it was granted. A `claude` session cannot quietly spend the ChatGPT seat and vice versa. |
| `pw_browser.navigate` | Refuses `claude.ai`, `chatgpt.com`, `chat.openai.com` — the agent browser clones the operator's Firefox cookies, so those are live seats. `PLINY_ALLOW_SUBSCRIPTION_BROWSER=1` opts in deliberately. |

The environment contract handed to an agent process is
`PLINY_SUBSCRIPTION_SEAT` / `PLINY_SUBSCRIPTION_AUTHORIZED` /
`PLINY_SUBSCRIPTION_ORIGIN`; see `account_guard.session_flags()`.

**Residual gap.** An agent already inside a session inherits that session's
environment and OS user, so nothing in-process can tell the sanctioned runner
apart from an agent shell invoking the same runner. The controls above are
boundary controls: launch, seat, and browser. An agent that resolves the real
binary path and defeats its own deny rule can still burn the seat. Do not add
"convenience" bypasses; do keep new launch paths failing closed.

## HTTP boundary

Every `/api/` request passes `_require_api_access()`: same-origin (or no
`Origin`, for CLI/agent traffic) plus the optional bearer gate. CORS never
uses `*`; `Handler._send_cors_headers()` echoes only a matching same-origin
value. Keep new endpoints behind the central `do_GET` / `do_POST` gates and
use `_send_json` / `_send_text` for responses.

`_auth_kind()` classifies the credential, not just its validity: operator
proof → `operator`, scoped agent token → `agent`, nothing → `None`.

**Localhost is not an identity.** An agent subprocess runs as the same OS user
and can reach 127.0.0.1, so "no credential" must not mean "operator". Operator
proof is exactly two things:

- `PLINY_AUTH_TOKEN` bearer (when configured), or
- a **paired browser session** cookie (`pliny_session`).

A page load mints nothing. The browser pairs once with a one-time code printed
on the server console; `POST /api/pair` hashes the resulting device token with
SHA-256 and stores only the hash plus an `expires_at` in
`state/paired_devices.json`, so reading that file yields nothing replayable.
Validation and consumption of the code happen in one critical section, so
concurrent requests cannot both redeem it. The code is single-use (a fresh one
is printed after each pairing), rate-limited, and expires server-side after
`PLINY_PAIR_CODE_TTL` seconds (default 15 min). Codes are printed at startup
and after pairing. An unpaired browser's `/api/pair/status` request or a
rejected pairing attempt renews an expired code and prints it once. There is
no background renewal or idle console output. The value goes to stdout only;
it is not logged and not exposed by
`/api/pair/status`. Paired browsers expire after
`PLINY_PAIR_SESSION_TTL` seconds (default 30 days); `_device_is_paired()`
rejects and prunes expired records, so the cookie's `Max-Age` is a hint, not
the control. `/api/pair` and `/api/pair/status` are the only API paths exempt
from the auth gate, and they are still origin-checked.

`state/operator.token` is the **CLI** credential and never a cookie. It is
written only when `PLINY_AUTH_TOKEN` is unset, and
`PLINY_OPERATOR_TOKEN_FILE=off` suppresses it entirely.

`_require_auth()` fails closed: an anonymous or agent-token request to a
privileged route is a 403 even in default localhost mode. Non-privileged
routes stay reachable anonymously so dashboard polling, `/api/status`, agent
callbacks (`/api/dragonfire`, `/api/prompter`, `/api/redteam/chat`) and CLI
reads keep working — that gate is not the operator boundary. Anonymous
traffic is stamped `agent` in `account_guard`, so it can never spend a
subscription seat.

**Isolation limit.** None of this isolates a process running as the same OS
user: it can read `state/operator.token` directly and become the operator.
File permissions and the `agent_permissions` read denies are deterrence, not a
boundary. A real boundary requires a separate OS account, a container/VM, or a
host the agent cannot read. Do not describe same-user file permissions as
enforcement.

## Computer Use boundary

`cu_capabilities.py` is the credential model. When a session acquires the
Computer Use slot (`_cu_acquire`), the server mints a signed capability bound
to that session id, its mode (`desktop` / `browser`), scopes
(`observe`, `act`, `pause`) and an expiry (`PLINY_CU_CAPABILITY_TTL`, default
6 h). The agent receives it as `PLINY_CU_TOKEN`; `_cu_release` revokes it
(nonce no longer current), so a copied capability dies with its mission.
The signing key and `BROWSER_CONTROL_TOKEN` stay in the server and the
browser sidecar; they never enter an agent environment.

- **Resume is operator-only.** `/api/desktop/resume`, `/api/browser/resume`,
  reviving a finished/paused CU mission via `/api/session/message` or
  `/api/session/continue`, and granting CU via `/api/session/request-cu` all
  require `_check_operator_auth`. Pause stays open to the agent token (the
  ESC hook uses it). An agent asking for CU only records a `cu_requested`
  event for the operator.
- **Desktop endpoints:** the operator keeps full access; an agent needs its
  own session's capability (`X-Pliny-CU-Token`) for `batch`/actions (`act`)
  and for `windows`/`screenshot` (`observe`). No live owner → 409, never an
  earlier mission's frame.
- **Browser sidecar (:8787):** loopback `Host` only, any `Origin` or
  cross-site fetch metadata is refused, no CORS. Requests need the control
  token (server) or the owning mission's browser capability; the server
  pushes the owner with `/control/owner`, and an owner change deletes the
  retained screenshot. `/resume` and `/control/owner` are control-token only.
  `browser_auth_prompt()` adds the capability header to every
  `curl … :8787` / `Invoke-RestMethod … :8787` line in CU prompts.
  `PLINY_BROWSER_AUTH=off` is for standalone sidecar debugging only.
- **Captures:** desktop frames go to `%TEMP%/pliny-cu/<session>/`, removed on
  release together with the shared `pliny_pw_screen.png` /
  `pliny_cu_latest.png`. `cu_screenshot` SSE events reach operator streams
  only.
  `PLINY_CU_HUD_CAPTURE=1` opts the badge, glow and desktop cursor into captures for operator demo recordings (excluded by default).
- **Input:** after `activate`, mouse and keyboard steps are validated against
  that window or a dialog it owns (`focus_changed`, `target_mismatch`);
  `clear_target` is the explicit opt-out. Pointer steps may carry `frame_id`
  (the MCP tools add it) and are refused as `stale_frame` when the frame is
  unknown, older than `PLINY_CU_FRAME_MAX_AGE` (300 s), the monitor geometry
  changed, or the target window moved. Batches have a 60 s budget
  (`timeout_ms`, max 120 s); pause/ESC/stop interrupt typing, chords, drags
  and waits between input units and report `progress`. Every failure carries
  a `code` and a `recovery` hint (`cu_capabilities.RECOVERY`).

Same-user processes can still read each other's memory and files (see
"Isolation limit"); these are boundary controls, not an OS sandbox.

## Agent permissions

Comms messages carry a server-generated `review` with heuristic warning codes.
New messages are scanned before storage/SSE; reads also review legacy records
without rewriting them. Full content is scanned before the agent preview is
truncated. Comms context quotes message data and flags instead of treating
sender names as authority. Review flags do not block delivery or certify safety.

`PLINY_AGENT_PERMISSIONS` selects the runtime posture:

- `workshop` (default) — Claude Code runs `--permission-mode auto
  --permission-prompts none`; OpenCode receives an explicit `permission`
  document that denies edits outside the workshop and temp directories,
  restricts external paths to the install tree so `rt_send.py` still runs,
  and refuses reads of `.env` / `secrets.json` / session key files; Codex
  keeps its `workspace-write` sandbox.
- `unrestricted` — historical behaviour (`--dangerously-skip-permissions`,
  OpenCode `--auto`). Opt in for a trusted machine.

Desktop Computer Use missions get one scoped extension: the operator's
Desktop, Downloads and Documents are added as write roots (`PLINY_WRITE_ROOTS`;
Claude `--add-dir`, Codex `sandbox_workspace_write.writable_roots`, OpenCode
`external_directory` + `edit` allows) because Save/New Project dialogs land
there and the agent already reaches them through the shell. `PLINY_CU_WRITE_ROOTS=0`
withholds it; every other mission stays workshop + temp. Credential read denies
still apply inside the granted roots.

See `agent_permissions.py`. Do not add a blanket bypass to a new launch path.

In the `workshop` profile, OpenCode V2 hard policies deny shell process-kill
commands such as `Stop-Process` and `taskkill`. Agent process cleanup belongs
to the dashboard operator. These command guards are not OS-level isolation.

## P4RS3LT0NGV3 sidecar

`p4rs3lt0ngv3_bridge.py` is the only integration point for the upstream
222-transform catalogue. The checkout is an organ
(`organs/p4rs3lt0ngv3`, gitignored, AGPL-3.0), cloned and updated with
`python p4rs3lt0ngv3_bridge.py sync|update`; do not copy its source into the
monolith and do not rewrite its transforms in Python. `parseltongue.py`
registers the catalogue lazily as `p4_<key>` and remains the offline fallback:
importing it, starting the server, and every native carrier work with no Node
and no checkout. `PLINY_P4RS3LT0NGV3_DIR` overrides the location;
`PLINY_P4RS_DISABLE=1` forces the native registry. The bridge is not an HTTP
endpoint and carries no credentials.

## Tweet generation

- The X-algo-informed system prompt is read from
  `state/workshop/TWEET_BANGER_PROMPT.md` when that file exists. It is
  hot-swappable - edits take effect on the next request.
- Tweet drafting and research go through `pliny_secrets_client.py`, which
  reads provider keys from the environment / vault. Do not spawn `hermes`
  directly from `server.py` or any other in-repo code.
- xAI OAuth sign-in is the one remaining `hermes` CLI path
  (`/api/auth/hermes/login`); it is an operator action, not an agent
  credential path.
- `algo_precheck()` in `grok_tweet_drafter.py` is pure-Python and safe to
  edit/extend.

## Gauntlet result semantics

Target statuses are distinct on purpose:

- `cracked` — judge score at or above the threshold.
- `failed` — the scored-attempt budget was genuinely consumed by refusals.
- `blocked_upstream` — provider/classifier failures exhausted the retry cap.
- `blocked_judge` — the LLM judge could not produce a verdict; no refusal was
  observed.
- `pending_response` — the target request was still outstanding when the
  deadline hit; not a refusal.
- `aborted` / `interrupted` — cancelled run or server restart; not a failure.

Do not collapse these back into `failed`.

## Judge semantics

`gauntlet.llm_judge_compliance()` returns `score: None` plus an `error` on
every failure path — missing key, HTTP error, transport error, unparseable
verdict. A judge that did not run says nothing about the target, so it must
never read as `score: 0` (REFUSED). Callers branch on `error`:
`check_marker()` skips the marker and `record_attempt()` writes the attempt as
`error_type: judge_error`, outside the scored budget.

`_get_openrouter_key()` resolves environment → `PLINY_AGENT_KEY_FILE`, and
refuses the `state/secrets.json` vault outright when the process is scoped
(`PLINY_AGENT_KEY_FILE` or `PLINY_LAB_SCOPED_ENV` set). A scoped subprocess
gets its one judge key through the same 0600 file as the rest of its
credentials — `lab_api.start_run()` adds `OPENROUTER_API_KEY` to the scope
only when `judge` is enabled.
