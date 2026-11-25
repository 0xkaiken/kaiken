# AGENTS.md — agent contract

Applies to every agent and test runner that touches this repo: Claude Code,
Codex, OpenCode, the Gauntlet attacker chain, and anything spawned from
`server.py`, `gauntlet.py`, `basilisk.py` or `fixer.py`.

Repo architecture, the credential boundary, the HTTP gate, agent permission
postures and result semantics live in [CLAUDE.md](CLAUDE.md). Read it before
changing `server.py`, `agent_secrets.py`, `agent_permissions.py` or anything
under `rt_*`.

### Evidence-Driven Verification

Prioritize empirical testing and executable evidence over abstract adversarial
code reviews. Define invariants, run targeted tests, and validate behavior through
reproducible failures. Use formal verification where practical. Avoid speculative
nitpicks and review loops without actionable findings. Every reported issue should
include concrete evidence or a reproducible test case.

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

## How it is enforced

`account_guard.py` owns the decision. Origins are `operator`, `automation`,
`agent` and `test`; only the first two may spend a seat, and only with an
explicit authorization. Seats are `claude` (Anthropic) and `codex` (OpenAI);
`opencode` bills its own plan and is out of scope.

Chokepoints — read `CLAUDE.md` → *Enforcement* for the table:

- `Session._start_inner` fails closed before spawning a subscription runtime.
- `server._require_api_access` demotes agent-token requests to origin `agent`.
- `agent_permissions.subscription_cli_deny_args()` denies `Bash(claude:*)` and
  `Bash(codex:*)` in the agent shell.
- `codex_runner` and `rt_send` refuse a seat that is not the session's own.
- `pw_browser.navigate` refuses `claude.ai`, `chatgpt.com`, `chat.openai.com`.

When you add a launch path, a runner, or a browser tool, route it through
`account_guard`. Do not read the raw env markers instead of calling the module,
and do not add a bypass "just for this mission".

## Working rules

- Agent subprocesses never inherit the server's provider keys; keep
  `agent_secrets.filtered_env()` strict.
- Never read, print or copy a value out of `.env` or `state/secrets.json` into
  output. The masked view is the contract.
- Secrets stay on the provider-key path. Never pass a key as a CLI argument.
- New endpoints go behind the central `do_GET` / `do_POST` gates and use
  `_send_json` / `_send_text`.
- Do not add a blanket permission bypass to a new launch path. `workshop` is
  the default posture for a reason; `unrestricted` is an explicit operator
  choice.
