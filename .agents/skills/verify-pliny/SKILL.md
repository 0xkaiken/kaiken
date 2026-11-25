---
name: verify-pliny
description: Verify Pliny Command's browser guide, mission queue and Dragon Memory with real HTTP handlers, disposable state and Playwright evidence. Use for dashboard behavior changes and reproducible UI checks on Windows.
---

# Verify Pliny Command

Read [the feature map](features/README.md), then choose the affected journey.
The primary user interface is the browser dashboard at `/`, with `/redteam`
and `/arcade` as additional dashboards. Python tools and agent runners are
secondary interfaces and are outside this initial map.

## Launch

From the repository root in Windows PowerShell:

```powershell
python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature guide
```

Requires Python 3.11+, the Python `playwright` package and installed Microsoft
Edge. The generator was exercised with Python 3.12 on Windows. If missing,
install Playwright with `python -m pip install playwright`. The helper uses
Edge's `msedge` channel, so a Playwright browser download is unnecessary.

The helper copies tracked root Python, HTML, CSS, JavaScript and `VERSION`
files, including current working changes, to a new `%TEMP%/pliny-verify-*`
installation. It starts the production `server.Handler` and
`server.ThreadedHTTPServer` on an OS-assigned loopback port. Import-time
initialization creates an empty workshop and state directories. No seed
records are required. On page load the helper reads only its own disposable
server's printed one-time code, enters `#pliny-pair-code`, and clicks
`#pliny-pair-go` to pair its fresh browser through the real form. This grants
dashboard access only for this disposable instance, not permission to use
subscription runtimes. The foreground run prints
`Doctor passed: http://127.0.0.1:PORT` only after readiness checks pass.

This is a limited HTTP launch, not the documented full `start-windows.cmd`
launch. It deliberately does not call `server.main()`. Therefore installer
seeding, restart recovery, process sweeping, the indicator, file watcher,
scheduler, browser watchdog, session reaper, watchtower, fixer, noticer and
mycelium daemon startup remain unverified. The copied installation has no
`.env`, provider keys, live state, real browser profile or agent credentials.
The helper sets a disposable home, workshop and minimal child environment.
It creates no operator token. No production code is patched or mocked.

`PLINY_HOME` alone cannot isolate a normal launch: state and sessions are
derived from `server.py`'s directory. Use the disposable installation. Do not
drive or stop an existing instance at port 8888. Each helper invocation owns
its own process, random port, state and browser context, allowing concurrent
runs. Full desktop control and production sidecars do not share that guarantee.

## Doctor

Every run calls the read-only `doctor()` before browser interaction. It checks
that its child is alive, the readiness PID matches that child, `/api/version`
reports the copied installation, expected `VERSION` and disposable workshop,
and `/api/status` reports an existing workshop and zero active sessions.
It saves the response as `doctor.json`.

If startup fails, inspect that run's `server.log` and `result.json` first.
If the identity checks fail, do not continue with the URL. The helper cleans
up and fails. A responding port alone is insufficient readiness.

## Drive

The executable Python helper uses Playwright against the served HTML and real
HTTP endpoints. It dismisses the first-run tour via `#intro-skip`, labeled
`Skip`. It uses buttons, input IDs and the Guide's accessible names instead of
coordinates or JavaScript setters.

```powershell
python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature guide
python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature queue
python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature memory
```

Choose one command appropriate to the change; running all three is not the
default. See the feature files for exact entry points and expected states.
The generator proves only `guide`; the queue and memory recipes are seeded
from source and must be executed before claiming those features passed.

Do not click Next, launch a mission, send Commander chat, start the Gauntlet,
enable automation, or invoke subscription CLIs as part of these journeys.
The repository's account boundary still applies. Real account sign-in state
does not authorize subscription usage.

## Evidence

Each run prints its absolute evidence directory under
`tmp/verification/pliny/YYYYMMDD-HHMMSS-RANDOM/`. This location is already
ignored by Git. Keep evidence after teardown.

- `source.json` identifies copied source bytes by SHA-256.
- `doctor.json` records process, app version, installation and baseline state.
- `actions.json` records actions before execution.
- PNGs and `.aria.txt` snapshots capture before and resulting user states.
- `trace.zip` captures browser actions and DOM snapshots, including failures.
- `requests.json` records observed methods and URLs. `blocked-external.json`
  records denied external requests, including the optional Google Fonts CSS.
- `state-files.json` inventories created application state; mutation journeys
  also save API reads and require rendered persistence after reload.
- `result.json` records pass/fail, launch limitations and process cleanup.

The browser blocks requests outside its own loopback origin. It supplies no
fake API responses. Font appearance may differ from a connected browser.
This is not a dry-run flag: it really starts a server, writes disposable
state and reads that state through user-facing endpoints. It never launches
agent runtimes. A final `/api/status` check requires zero active sessions.

Proof must include the action and resulting UI, not a screenshot alone.
For mutations, require both a second user view and persistence evidence.
Never use internal setters, fabricated API success responses or test-only
endpoints as proof. Report failed preconditions and untested entry points.
Only external systems already separated by a production boundary may be
mocked in a different, explicitly described verification mode.

## Cleanup

The helper closes its own Playwright browser and terminates only the exact
HTTP child it created, waiting and killing that same process if necessary.
`process_exit` can be nonzero because Windows termination ends the child;
the helper's own exit code must be zero for a passing journey.
It then checks the absolute temporary directory is a direct child of the OS
temp directory with its allocation prefix before deleting it. This runs in
`finally`, including failed attempts. Never kill by executable name or use
the dashboard's STOP ALL for cleanup.

After teardown it writes `result.json` outside scratch state and confirms
evidence remains. Require `cleanup: true`, `passed: true` and exit code 0 for
a successful run. A cleanup failure is a failed run. If the helper itself is
forcibly terminated, use the PID and temporary paths from `doctor.json` to
identify its instance before any manual cleanup.

## Helpers

[`scripts/verify.py`](scripts/verify.py) is executable through the Python
invocations above. Its private `--serve` mode is managed by the parent;
agents should use `--feature` so cleanup remains attached to the run.

Maintain the map with `$pstack:maintain-verification-skill` when relevant
routes, selectors, launch behavior or preconditions change.
