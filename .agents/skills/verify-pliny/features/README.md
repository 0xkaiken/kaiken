# Pliny verification map

This index is the maintained starting point for browser verification. Run
from the repository root with the helper described in [SKILL.md](../SKILL.md).

## Baseline preconditions

- Windows, Python Playwright and Microsoft Edge are available.
- Use a fresh disposable installation and OS-assigned loopback port per run.
- Start with no missions, memory records, sessions or provider credentials.
- Require the helper's doctor check before driving the UI.
- Pair the disposable browser using its own server console code and Pair form.
- Dismiss the introductory tour with `#intro-skip`, labeled `Skip`.
- Never reuse the user's port 8888 instance or browser profile.

## Driving conventions

Use `python -X utf8 .agents/skills/verify-pliny/scripts/verify.py --feature NAME`.
The helper launches, checks, drives and cleans up its own instance. Select
one relevant feature. It uses real browser controls and production HTTP
routes, with external browser requests blocked.

## Proof and skip reporting

Preserve `actions.json`, screenshots, ARIA snapshots, trace, identity and
cleanup result in the printed `tmp/verification/pliny/` run directory.
Mutation proof needs a reload and a read-only persisted-state view. Record
the feature and entry points driven. Do not claim an unexecuted recipe or
an untested alternate entry point passed. Missing seed data is a failure
with its reason recorded under the affected feature's Gotchas.

The initial generator run exercises Guide on Command. Other dashboards and
the queue and memory recipes start as unverified. Full launcher and daemon
behavior are outside this HTTP-only mode, as detailed in SKILL.md.

## Features

- [Guide](guide.md) covers button, keyboard, contextual help and search.
- [Mission queue](queue.md) covers saving work for later without launching it.
- [Dragon Memory](memory.md) covers creating and reopening a saved note.
