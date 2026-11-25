---
name: verify-pairing-formal
description: Model-check Pliny's single-use browser pairing code after changes to server.py pairing locks, redemption or code rotation. Run TLC and replay the removed-lock counterexample against the real Python functions without starting the application or using accounts.
---

# Verify single-use browser pairing

Property: one minted pairing-code generation can be redeemed successfully by
at most one of two concurrent calls to `server._pair_code_matches`.

Harm: accepting the same one-time approval twice lets two browser sessions
obtain operator access from a code intended for one pairing.

## Run

From the repository root in PowerShell:

```powershell
python -X utf8 .agents/skills/verify-pairing-formal/scripts/verify.py
```

Requires Python 3.11+ and Java 11+ on PATH. The proved environment used Python
3.12.8 and Oracle Java 25.0.2 on Windows. No Python package is required.
The first run downloads [TLC 1.7.4](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4)
to ignored `tmp/verification/tools/`, verifies its pinned SHA-256, and then
uses that exact cached JAR. Later checks run offline. The script sets one
TLC worker, fingerprint polynomial 0 and seed 1. Its Java timeout is 45
seconds per check. Downloading the tool is the only intended network call.

Run the sensitivity check when changing the verification itself:

```powershell
python -X utf8 .agents/skills/verify-pairing-formal/scripts/verify.py --self-test
```

It checks the baseline, removes the redemption lock in a temporary copy of
the real `server.py`, detects the violation, reproduces two acceptances with
the actual extracted functions, deletes the scratch copy and checks the
unchanged baseline again. It never edits product code, imports the server,
reads `.env` or runtime state, starts an agent, or uses an account.

Exit codes: 0 means the bounded check passes, or the complete self-test
behaves as expected. 1 means the property fails. 2 means a source/model
mismatch, disagreement with replay, or tool failure blocked verification.
Unexpected operating-system errors also fail the command; inspect stderr.

## Read every result with statechart

Use the pstack `/statechart` skill to read **every** TLC result, including
the expected broken variant and restored baseline in a self-test. Read that
skill's installed instructions at run time rather than copying its release
specific commands here. Give it each run's `Pairing.tla`, `Pairing.cfg` and
`pairing.view.mjs`. Work outside the repo as its instructions require.
Preserve its report and Mermaid chart with the verification evidence.
On Windows, use LF output for the Java subprocesses if the installed
statechart parser cannot recognize CRLF message markers. The recorded run
set `JAVA_TOOL_OPTIONS` to a quoted `-Dline.separator=` option whose value
is an actual newline. Java accepts that value; literal backslash-n is not
equivalent. Use the skill's worker with owned staging if its Unix process
cleanup wrapper is unavailable. Neither adjustment changes TLC's model or
invariant. Do not edit the installed plugin or treat a parser error as a
product failure.

A pass should show that one browser can be accepted and another rejected.
The expected broken path is: first request caches the code, second request
caches it too, first consumes it and returns True, second consumes its
cached value and also returns True. The invariant is
`AtMostOneRedemption`. The diagram uses plain words for these states.

## Coverage and code connection

Last confirmed sync commit: `babf20218eb053cd8c1fa4627cc2b299d7542996`.
The covered source was clean at creation. [coverage.json](coverage.json)
records each reviewed function's AST fingerprint and source line span.
`server.py` is the only product file covered.

| Code | Connection to the check |
| --- | --- |
| `_pair_lock = threading.Lock()` | One shared mutual-exclusion lock |
| `_pair_code_matches`, lines 511-540 | `Redeem` is its read/check/consume/return critical section |
| `_code_is_live`, lines 498-499 | TTL check collapsed to live or expired |
| `_mint_pair_code_locked`, lines 485-490 | Fresh generation and timestamp; uniqueness is assumed |
| `_new_pair_code`, lines 493-495 | Generation reset holds the same lock |
| `pairing_code`, lines 502-508 | Generation creation/rotation holds the same lock |
| `_pair_code_refresh`, lines 563-572 | Status-triggered renewal holds the same lock |
| `print_pairing_code`, lines 575-597 | Generation renewal/printed marker holds the same lock |

The script rejects a changed covered AST, lock declaration, or function
referencing `_pair_code`. It recognizes exactly one supported changed AST:
replacing the consumer's `with _pair_lock` with `if True`. That real source
change automatically selects `AtomicConsume = FALSE`; normal code selects
TRUE. Unknown edits block instead of silently checking an outdated model.
Fingerprints are a drift alarm, not a proof of Python-to-TLA equivalence.

The model's atomicity applies to these competing redemption calls: none can
observe another's protected intermediate reads and clears. Other device
operations may delay lock acquisition but cannot redeem a code. The model
does not claim the critical section is one Python instruction, nor that
time or unrelated threads stop while it runs.

The replay AST-compiles the actual `_code_is_live` and `_pair_code_matches`
functions with fixture state and a fixed clock. It uses their real lock and
`hmac.compare_digest`. In the lock-free copy, line tracing pauses both calls
immediately before comparison, after their local snapshots are read. This
chooses a schedule without replacing validation or consumption. Two True
results confirm that the modeled bad order is possible in that code.

## Bounds and assumptions

- Exactly two calls, one code generation, each call runs once.
- TLC explores all four match/mismatch input combinations and both initial
  live/expired states. Empty and malformed inputs that return before locking
  cannot add an acceptance and are not separately modeled.
- Input strings collapse to whether their normalized value matches the
  single code. Local reads and returns of a lock-holding redemption collapse
  to one action because the second redemption cannot interleave inside it.
- For the supported lock-free variant, reading the local code and timestamp
  is one step and completing validation/consumption is another. This is a
  realizable bad schedule, not an exhaustive Python bytecode scheduler.
- Time and code validity remain fixed during each explored execution.
  Wall-clock jumps and expiration while waiting for the lock are outside scope.
- Producers are reviewed and fingerprinted, but rotation is not a modeled
  action. New code generations never collide with the old random string.
- No crash, process sharing or bypass of the Python functions is modeled.
  This is bounded model checking, not a proof for arbitrary callers or sizes.

The check does not prove cookie storage, token hashing, rate limits,
operator-bearer pairing, endpoint authorization, session minting, browser
security, subscription guards or same-OS-user isolation. No HTTP handler or
provider is exercised. Existing endpoint and concurrency tests cover other
parts of pairing; this check does not replace them.

## Recorded proof

On 2026-11-04, the baseline and restored checks each passed over 33 distinct
states. The removed-lock copy violated `AtMostOneRedemption` along a shortest
four-action path. Real-function replay returned one True on the baseline,
two True on the broken copy and one True after scratch cleanup. No violation
was found in the current product code within these bounds.

Evidence is retained at
`tmp/verification/pairing-formal/20261104-040358-10200/`.
Each of its three run directories includes a `statechart` report and chart.
Statechart explored all 96 states of the broken model after its first
violation, so that report's state count differs from TLC's early-stop run.
The charts omit steps that stay within one displayed state, including the
second rejection after a successful redemption. The raw reports retain
their hidden-step counts. No other step is filtered by the view.

## Evidence and failures

Each run prints an absolute directory under ignored
`tmp/verification/pairing-formal/`. It contains the exact model/config,
`tlc.out`, `result.json`, real-function replay results and tool/source/model
fingerprints. A self-test has `baseline`, `without-lock`, `restored` folders
and a final `self-test.json`. Evidence survives scratch cleanup.

Do not report a product bug merely because TLC fails. Read the statechart
report, map its steps to the covered code, and replay the schedule. If real
code cannot take a model step, repair the verification. If both fail the
same property, report the code path and harm without changing product code.
Never weaken the invariant or lower the bounds to make the check pass.

## Keep it synchronized

When covered code changes, compare it with the sync commit, inspect all
writers and lock boundaries, and update the model from the code. Only then
update the reviewed AST fingerprints and sync commit in this skill and
coverage.json. Do not regenerate fingerprints just to silence a drift error.

Run the updated check and `--self-test`, then read all results with
`/statechart`. Move the sync commit only after the model describes that
commit's code, the check completes and the broken real-code variant is
still caught. Keep product fixes in separate changes.

Use `$pstack:maintain-formal-verification-skill` for changes that skipped
this verification workflow.
