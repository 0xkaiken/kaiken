# tests/

Two suites, both runnable from the repo root or from this directory.

```
python -m pytest tests/ -q      # 631 Python tests
for f in tests/*.js; do node "$f"; done   # UI/source assertions
```

`conftest.py` puts the repo root on `sys.path`, so the tests import
`server`, `opencode_runner`, `gauntlet` and friends without the runner having
to be started from the repo root.

No test in here needs a provider key, an agent launch or an outbound network
call. Endpoint tests bind a loopback server on port 0 and drive it with
`urllib`; anything that would spend money is mocked.

## What is covered where

- `test_file_delete*.py` — the DELETE /api/file boundary: operator-only
  authorization, workshop containment, symlink and traversal refusal
- `test_pulsvivus.py` — upstream pulse tool: report on harness error, exit
  codes, `--only` filtering
- `test_jailbreak_lab.py` — carrier A/B harness: carrier parsing, no-op
  accounting, verdict summarization
- `test_pyrit_lab_api.py` — PyRIT organ bridge: payload validation, the
  sidecar argv contract, credential scrubbing, run-dir layout, status/attempt/
  result projection (the engine itself runs as a subprocess)
- `test_security_hardening.py`, `test_account_guard.py`, `test_hardening_pass.py`
  — credential boundary, seat authorization, launch-path hardening
- `test_windows_*.py` — Windows-specific transport, restart and desktop paths
- `test_*_ui.js` — extraction checks against the served HTML: tab map, guide
  ids, template-literal safety, stale-response handling
