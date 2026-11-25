# tools/

Every script here runs from the **workshop** directory, which is where agents
get their `cwd`. All paths below are relative to `$PLINY_WORKSHOP`.

Install or refresh them with:

```bash
python tools/install_tools.py            # copy into the workshop
python tools/install_tools.py --dry-run  # see what would change
python tools/install_tools.py --force    # overwrite workshop copies
```

| tool | one-liner |
|---|---|
| `prompt_encoder.py` | encode an objective into transport/obfuscation formats |
| `p4rs3lt0ngv3.py` | P4RS3LT0NGV3 sidecar: 222 upstream transforms, status/sync, encode/decode |
| `prompt_mutator.py` | generate labelled variants across six mutation families |
| `auto_redteam.py` | cumulative difficulty ladder (levels 1-5) |
| `adversarial_poetry.py` | carry the request inside verse |
| `likert_judge.py` | evaluation-mode scaffolds (rubric, grader, panel) |
| `deceptive_delight.py` | camouflage + distraction wrappers |
| `reasoning_chain_exploiter.py` | make the answer a logical entailment |
| `pulsvivus.py` | is upstream alive today? one PING per route, ~$0 on a dead route |
| `dragonfire_ping.py` | raise a dashboard notification, queue if it is down |
| `v1s10n.py` | screenshot, visual metrics, pixel diff |
| `gh_l1b3rt4s_pr.py` | turn a cracked run into an L1B3RT4S entry / PR |
| `refusal_classify.py` | REFUSAL / COMPLIANT / AMBIGUOUS + lexicon listing |
| `judge_calibration.py` | known-answer suite for `llm_judge_compliance`; exit 1 on drift |
| `judge_adaptive.py` | adaptive judge sampling: escalate only in the crack band, Wilson-bounded crack rate |
| `wave_preflight.py` | run/quote gate before a wave: harness + API + fresh receipt |
| `api_surface_audit.py` | every `/api/` leaf the UI calls must exist in the server |
| `doc_command_audit.py` | every `python <tool>.py` in the docs resolves and answers `--help` |
| `named_not_owned_scan.py` | find identifiers that are named but never owned |
| `cl4r1t4s_staging_sweeper.py` | validate staged prompts, publish or quarantine |
| `cl4r1t4s_probe_regression_cron.py` | hash-pin drift watch on published prompts |
| `coverage_probe_cron.py` | surface ambiguous refusals for lexicon review |
| `smoke_test.py` | run every tool with no-spend flags; 25 checks, exit 1 on any failure |
| `install_tools.py` | seed the workshop (this file's installer) |

## Encoding and mutation

```bash
python3 tools/prompt_encoder.py encode --text "objective" --format all
python3 tools/prompt_encoder.py encode --text "objective" --format base64,rot13 --json
python3 tools/prompt_encoder.py decode --text "aGVsbG8=" --format base64
python3 tools/prompt_encoder.py list          # 94 native + 222 sidecar when installed

python3 tools/p4rs3lt0ngv3.py status           # sidecar checkout + Node
python3 tools/p4rs3lt0ngv3.py sync             # clone organs/p4rs3lt0ngv3
python3 tools/p4rs3lt0ngv3.py encode acere_cipher --text "objective"

python3 tools/prompt_mutator.py "objective" --transforms all
python3 tools/prompt_mutator.py "objective" --transforms frame,lexical --json
python3 tools/prompt_mutator.py --file brief.txt --out variants.json
```

Families: `frame`, `authority`, `format`, `persona`, `lexical`, `structure`.

## The ladder

```bash
python3 tools/auto_redteam.py "objective" --level 3 --all
python3 tools/auto_redteam.py "objective" --level 5 --all --json --out campaign.json
```

Levels are cumulative and each one is a separate claim:

1. format contract · 2. persona + authority · 3. semantic · 4. encoding ·
5. composition + crescendo.

Level 5 also emits the four-turn crescendo plan.

## Notifications

```bash
python3 tools/dragonfire_ping.py "GPT-6.1 PWNED" "attempt 4, multilingual pivot" breakthrough
python3 tools/dragonfire_ping.py "TITLE" - < message.txt
```

Categories: `discovery`, `tool`, `writing`, `breakthrough`, `alert`.
If the dashboard is unreachable the ping is appended to
`dragonfire_queue.jsonl` and the tool still exits 0 — a notification never
fails a mission.

## Visual check (SHIP MODE)

```bash
python3 tools/v1s10n.py --test-url http://localhost:PORT --focus "layout, polish"
python3 tools/v1s10n.py --shot http://localhost:PORT out.png
python3 tools/v1s10n.py --diff before.png after.png --diff-out diff.png
```

`--diff` exits **1** when the change is below `--threshold` (default 0.35% of
pixels). That is the point: a no-op edit cannot be reported as a fix.

## Instrument calibration (read before quoting a number)

```bash
python3 tools/judge_calibration.py             # 6 fixtures x 2 reps
python3 tools/judge_calibration.py --reps 1    # fast
python3 tools/judge_calibration.py --json out.json
```

Exit 0 means the compliance judge is still in its known bands. Exit 1 means it
moved: **do not quote gauntlet numbers until that is explained**, and do not
compare new runs to old ones. The canon behind the fixture bands — including why
`score == 3` counts are not a metric and why a `null` score is "judge
unavailable" rather than a refusal — is `state/workshop/canon/JUDGE_INSTRUMENT.md`.
Swapping `JUDGE_MODEL` invalidates every band until this suite is re-run.

## Wave gate

```bash
python3 tools/wave_preflight.py                 # free checks + receipt age
python3 tools/wave_preflight.py --full          # + run calibration, write receipt
python3 tools/wave_preflight.py --json --write  # machine mode + preflight receipt
```

Two questions, deliberately separated:

- **Can I run?** harness control ping + API surface. `BROKEN` (exit 2) means do
  not start.
- **Can I quote?** the canonical receipt at `<workshop>/probes/judge_calibration_latest.json`
  must name the judge now in `gauntlet.JUDGE_MODEL`, have `passed: true`, and be
  younger than `--max-age` hours (default 24). `DEGRADED` (exit 1) means the run
  is fine and the number is not.

`--full` is the only thing that writes the canonical receipt, because a receipt
you did not measure is a claim. `--skip-harness` spends no request at all, which
is why `smoke_test` can run it.

## Publishing a crack

```bash
python3 tools/gh_l1b3rt4s_pr.py --run-id 20261031-180401-dc3680 --dry-run
GITHUB_PAT=... python3 tools/gh_l1b3rt4s_pr.py --run-id <id> --repo owner/name
```

Dry run stages the `.mkd` into `<workshop>/l1b3rt4s_staging/` and prints it.
Live mode needs `GITHUB_PAT` and `--repo`; without them it exits 2 rather than
half-doing it.

## CL4R1T4S pipeline

```bash
python3 tools/cl4r1t4s_staging_sweeper.py            # publish / partial / quarantine
python3 tools/cl4r1t4s_staging_sweeper.py --dry-run
python3 tools/cl4r1t4s_probe_regression_cron.py      # seed pins on first run
python3 tools/cl4r1t4s_probe_regression_cron.py --reseed
python3 tools/coverage_probe_cron.py                 # report-only lexicon probe
```

Exit codes are part of the contract and are documented in each file's docstring.
The dashboard calls the same scripts through `/api/jobs/...`.

## Health scan

```bash
python3 tools/smoke_test.py              # every tool, no-spend flags
python3 tools/smoke_test.py --verbose --json
python3 tools/api_surface_audit.py       # UI calls vs server routes
python3 tools/named_not_owned_scan.py --root <install-dir>
python3 tools/named_not_owned_scan.py --root <install-dir> --no-markers --json
```

`smoke_test` exits 1 on any failure and prints the tail of the failing tool's
stderr. Two of its checks exist only to catch relocation bugs: `judge_calibration
--help` and `pulsvivus --help` both import code from the install dir, so they
fail loudly if `_paths` stops resolving. `api_surface_audit` exits 1 when a UI
call has no server route (that is a finding, not a broken tool), so the smoke
suite accepts both 0 and 1 from it.

`named_not_owned_scan` exits 2 when a finding at or above `--fail-on`
(default `high`) is present.

## Doc truth

```bash
python3 tools/doc_command_audit.py              # resolve + run --help
python3 tools/doc_command_audit.py --no-help    # resolve only, execute nothing
python3 tools/doc_command_audit.py --all        # include record dirs
```

Walks the docs (`README.md`, `QUALITY_GATE.md`, `TWEET_VOICE.md`, `tools/`,
`probes/`, `canon/`, `ships/`), extracts every `python <script>.py`, and checks
it resolves from the workshop, the install dir, or the doc's own directory. Then
it runs `--help` — but only on scripts that actually contain `argparse`, and
never on a `probes/` script, because a probe is a fire and not a tool.

Findings come in four kinds: `missing` (no root has it), `help-fails`,
`path-install-only` (a data path that only resolves from the install dir), and
`install-only?` (a script that exists under the install dir only — a warning,
suppressed when the doc documents a `cd`). Only the first three fail the exit
code.

This tool was built because two docs were telling agents to run commands that
cannot work from the workshop. Running it is now part of `smoke_test`.

## Is upstream alive?

```bash
python3 tools/pulsvivus.py                    # all routes
python3 tools/pulsvivus.py --only control     # harness check only
python3 tools/pulsvivus.py --write            # + attempts/access_status_<date>.md
python3 tools/pulsvivus.py --shout            # + dragonfire ping when alive
```

Route verdicts are `ALIVE`, `401` (auth dead), `402` (credit-gated), `ERR`.
Exit 0 = at least one upstream route alive, 1 = all upstream dead, 2 = the
control route failed (dead verdicts are then untrustworthy) or nothing was
selected. A dead route fails before generation, so it costs ~$0.

## Two copies, one source of truth

`tools/` in the install dir is the source. `<workshop>/tools/` is the installed
copy that agents actually run (their `cwd` is the workshop). `install_tools.py`
copies source -> workshop and keeps any workshop file whose mtime is newer, so a
hotfix made on the writable surface is never reverted. The trade: drift can run
in both directions, and a tool that resolves paths from `parents[N]` works in
exactly one of the two locations and fails *silently* in the other.

Rule: **resolve with `_paths.pliny_home()` / `_paths.workshop()`, never with
`parents[N]`.** The 2026-11-05 findings that motivated the rule are recorded in
`state/workshop/README.md` -> *Known drift*.

## Paths

`_paths.py` resolves the two directories that are easy to confuse:

- `PLINY_HOME` — the installed repo: `server.py`, `state/`, `rt_library/`
- `PLINY_WORKSHOP` — the writable surface: `tools/`, `memory/`, `experiment/`

Resolution order: environment variable, then the `.pliny_home` marker that
`install_tools.py` writes into `tools/`, then a `server.py` search. On the
Windows launcher the workshop defaults to `<install>/state/workshop`.
