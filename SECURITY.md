# Security

## Scope

Kaiken Harness is a **single-operator, localhost** application for AI red-team
research. It binds to `127.0.0.1`, has no multi-tenancy, and is not meant to be
exposed to a network. Read the "Isolation limit" sections in `CLAUDE.md`
before relying on any boundary: agent subprocesses run as the same OS user.

## Reporting a vulnerability

Open a **private** security advisory on this repository
(Security → Report a vulnerability). Please do not file a public issue for
credential exposure, auth bypass, or agent-boundary escapes.

## What never belongs in this repository

API keys, `.env`, `state/`, session logs, trophies (verbatim model outputs from
successful cracks), red-team notes, and attack prompt libraries. `.gitignore`
keeps them out of the tree, CI runs gitleaks over the full history on every
push, and `PUBLISHING.md` states which half of the red-team material ships.
