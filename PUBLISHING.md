# Publishing

This repository ships the clean, sanitized public harness tree — code,
dashboards, installers, tests, and docs without runtime state or private run
artifacts.

## What never goes in

The line is between the harness and the record of what it produced.

| ships | stays private |
|---|---|
| code, dashboards, installers, tests | trophies, run transcripts |
| `red-team/grimoire/` — the canon | raw target responses |
| payloads and staged batteries | extracted system prompts |
| the grading table, the quality gate | calibration receipts, campaign reports |
| docs that describe how to run it | session logs, measured results |

The test each file in `red-team/` passed is that it could have existed before
anyone fired at a target. A trophy proves a guardrail failed by containing what
got through — that belongs in a private vault with a disclosure timeline, not in
a public repository.

## Before a release

1. `python -m pytest -q tests`
2. `git status` — no runtime state gets committed. `.gitignore` covers `state/`,
   `sessions/`, `logs/`, `rt_library/`, `__pycache__/`, `*.pyc` and `.env`;
   verify anyway, and remember that `__pycache__` reappears every time you run
   the server or the test suite.
3. gitleaks runs on every push (`.github/workflows/secret-scan.yml`) over the
   full history. A secret that ever existed must be rotated, not deleted.
4. `git tag -a vX.Y.Z -m "..."` and push the tag.

## Secrets

No key, token, absolute path or account name belongs in any file here.
`NOTICE.md` lists the third-party components; `LICENSE` is MIT.

If a key ever reaches a commit, rotate it first. Removing it from history does
not un-leak it.
