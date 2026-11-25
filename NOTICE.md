# Notice

Kaiken is a fork of **Pliny Command** / **Libertarium**
([`dragons-blood/libertarium-dev`](https://github.com/dragons-blood/libertarium-dev)),
originated by [elder_plinius](https://github.com/elder-plinius). The original
design, the dashboards and most of the server are that project's work. This
fork adds native Windows support, the Claude Code / Codex / OpenCode runners,
Windows desktop Computer Use, the subscription-account guard, photo
attachments, Markdown + LaTeX rendering in the agent log, and the hardening
listed in `README.md`.

## Origin and permission

This repository started as a fork of `dragons-blood/libertarium-dev` and is
published with the upstream author's permission, on the condition that the fork
origin is stated. It is stated here and at the top of `README.md`.

Permission to publish this fork and to license it under MIT was confirmed with
the upstream project's moderators.

## License

Released under the **MIT License** — see [`LICENSE`](LICENSE).

Copyright is held jointly by the upstream project (elder_plinius / Pliny
Command / Libertarium) and the Kaiken contributors, and the MIT grant
covers this repository as published, including the fork's own additions. Credit
for the original work belongs upstream; the fork does not claim it.

## Third-party components

| Component | Where | License |
| --- | --- | --- |
| KaTeX | `vendor/katex/` | MIT (`vendor/katex/LICENSE`) |
| Cal Sans & Geist / Geist Mono | `vendor/fonts/` | SIL OFL 1.1 |
| P4RS3LT0NGV3 | cloned at runtime into `organs/p4rs3lt0ngv3` (not in this repo) | AGPL-3.0 |
| PyRIT | installed at runtime into `organs/pyrit/.venv` (not in this repo) | MIT |
