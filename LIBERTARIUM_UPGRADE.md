# LIBERTARIUM — Upgrade Dossier

*Audit date: 2026-11-02. Local repo: `kaiken-harness` (Pliny Command fork, v0.1.0-beta.1).*

> **Historical document.** This dossier audits the upstream snapshot as it was
> in November 2026 and proposes a product direction. Some of the paths it names
> (`bank/`, `kit/`, `docs/HUNT.md`, `docs/LAB.md`, `docs/WARROOM.md`,
> `organs/glossopetrae_bridge.mjs`, `organs/manifest.json`) belong to that
> proposed architecture and are not present in this repository. Read it as a
> design record, not as a file map.

> **Direction update (2026-11-02):** the active lane is model-level jailbreak
> R&D. See [`JAILBREAK_LAB.md`](JAILBREAK_LAB.md). The T3MP3ST-first order in
> §13 is deferred; the carrier telemetry and carrier lab come first. The
> organ-bus, receipts, and department-workbench architecture in §6–§8 still
> applies when the lab is wired into the cockpit.

---

## 0. The short read

The repo already contains the **operating surface**: ten departments, charters,
shared memory, a gauntlet, persistent agents, computer use, and a tool workshop.
What it does **not** contain is the layer that makes those departments *do work
with specialized engines*:

1. **Organs** — a way to run, health-check, and talk to external engines
   (Plinius's repos) without vendoring them into a 773 KB monolith.
2. **Receipts** — one append-only evidence ledger that every department writes
   to, so a trophy, a finding, an experiment, and a discovery are the same
   kind of proof.
3. **The department workbench** — the UI primitive the operating-surface doc
   lists as an open question: queue, latest receipts, memory, handoff.

Plinius's shelf maps onto those three almost perfectly. Four repos matter:

| Repo | What it actually is | Department | Verdict |
|---|---|---|---|
| **T3MP3ST** | Multi-agent offensive-security meta-harness. Mission engine, evidence/findings/retest, War Room, HTTP API, MCP server. | Red Team Ops | **Bridge first** |
| **OBLITERATUS** | Abliteration / refusal-geometry laboratory. CLI, Python API, MCP, provenance metadata, 116 model presets. | Laboratory + Forge | **Instrument** |
| **V3R1T4S** | 1,104 open problems with verification recipes, plus a hard scientific method: pre-registration, null test, three skeptics, `check_run.py`. | Laboratory + Watchtower | **Method + bank** |
| **GLOSSOPETRAE** | Procedural language engine, tokenizer blind-spot map, semantic steganography, covert channels. | Red Team Ops | **Carrier lab** |

Three support repos are worth wiring after the foundation:
**P4RS3LT0NGV3** (the local `parseltongue.py` is a stale partial port),
**CL4R1T4S** (archive sync/diff for the CL4R1T4S department), and **AutoTemp**
(gauntlet temperature calibration).

Everything else on the shelf is either a content corpus, a visual toy, a
hardware dependency, or a different product. See §5.

The first build should be **Phase 0 (organ bus + receipts) and Phase 1
(T3MP3ST bridge)**. That proves the architecture with the biggest capability
jump, and it needs no GPU and no model downloads.

---

## 1. What the repo is today

Verified from the working tree:

- `server.py` — 773 KB, stdlib HTTP + SSE server. Routes dispatch from
  `do_GET` at `server.py:10185`; dashboards are served by `_serve_html_file`
  at `server.py:10927` (`/`, `/redteam`, `/arcade`).
- `DEPARTMENTS_SEED` at `server.py:347` — all ten departments are already
  defined with charters, ship criteria, colors, and shared memory paths at
  `state/departments/{id}.jsonl`.
- Three agent runners: Claude Code, GPT/Codex, OpenCode Go. Per-mission and
  app-wide selection is already implemented.
- Gauntlet runs persist under `rt_library/gauntlets/runs/<run_id>/`
  (`gauntlet.py:85`), with `state.json` and trophies.
- `research-pipeline/pipeline_runner.py` ranks unfinished directions and
  writes `experiment/<slug>/BRIEF.md` — but it has no problem bank and no
  verification gate.
- `tools/` installs 15 workshop scripts into `PLINY_WORKSHOP`; `parseltongue.py`
  is a partial port of P4RS3LT0NGV3.
- Uncommitted local work exists in `rt_send.py` (a `--timeout` flag). Treat it
  as user work; do not revert or overwrite it.

The gaps match the operating-surface document's own open questions:

| Operating-surface open question | Current state | Filled by |
|---|---|---|
| Department primitive in the UI | Departments exist as charters + a modal list | Department workbench (§8) |
| Replication boundaries | No per-department action policy | Receipt gate + approval field (§7) |
| Relationship graph | Greenfield | Not in this upgrade |
| Experiment log | Greenfield (`infrastructure: experiment log (greenfield)`) | OBLITERATUS + V3R1T4S + ledger |
| Cross-department memory sharing | Department JSONL logs, no feed | Watchtower signals + handoff API |

A second gap is strategic: every agent is a subprocess of an external CLI or
API. Libertarium is sovereign in *interface* but not in *inference*. The lab
path below is what changes that.

---

## 2. The target shape

```
┌──────────────────────────────────────────────────────────────┐
│                     LIBERTARIUM (cockpit)                    │
│  Mission Control · Gauntlet · Arcade · Departments · Organs  │
├──────────────────────────────────────────────────────────────┤
│  DEPARTMENT WORKBENCH                                        │
│  queue · receipts · memory · handoff                         │
├──────────────────────────────────────────────────────────────┤
│  RECEIPTS LEDGER  (append-only, provenance, gates)           │
├──────────────────────────────────────────────────────────────┤
│  ORGAN BUS  (manifest · lifecycle · health · adapters)       │
├───────────────┬───────────────┬──────────────┬───────────────┤
│   T3MP3ST     │  OBLITERATUS  │   V3R1T4S    │ GLOSSOPETRAE  │
│  kill chain   │  abliteration │  open hunts  │  carriers     │
└───────────────┴───────────────┴──────────────┴───────────────┘
```

Design constraints taken from the repo itself:

- **No new framework, no new hard dependency.** The base install is still
  Python stdlib + `pyyaml`.
- **Localhost only.** Every organ binds `127.0.0.1`; the cockpit proxies
  server-side, never exposes an organ port to the LAN.
- **Append-only state.** JSONL, inspectable with `Get-Content`, consistent
  with `state/departments/*.jsonl` and gauntlet runs.
- **Windows-first.** Process-tree control already exists in
  `process_control.py`; reuse it for organ lifecycle.
- **AGPL hygiene.** Do not vendor AGPL engines into the repo; run them as
  separate processes and talk over HTTP/CLI. See §11.

---

## 3. The shelf, filtered

All 48 public repos were checked. Only the ones relevant to Libertarium are
listed.

| Repo | Language / size | Why it matters | Decision |
|---|---|---|---|
| T3MP3ST | TypeScript, 2.9 MB | Real offensive kill chain, evidence/findings/retest, War Room, API, MCP | **Adopt as sidecar** |
| OBLITERATUS | Python, 40.5 MB | Refusal geometry, abliteration, steering vectors, local model output | **Adopt as CLI/library** |
| V3R1T4S | Python, 137 MB | 1,104 open problems + verification protocol | **Adopt method + bank** |
| GLOSSOPETRAE | JavaScript, 1.5 MB | Tokenizer blind spots, semantic stego, conlangs | **Adopt as Node bridge** |
| P4RS3LT0NGV3 | JavaScript, 0.9 MB | 222 transforms; local port is behind | **Upgrade / bridge** |
| CL4R1T4S | data, 2.2 MB | System-prompt archive, updated daily | **Sync job** |
| LEAKHUB | TypeScript, 0.3 MB | Leak leaderboard | Read-only view later |
| AutoTemp | Python, 65 KB | Temperature sweep for attacker/judge calibration | Port as utility |
| FRV1T | Python, 0.5 MB | 12 creative carrier prompts | Prompt pack, optional |
| ST3GG | HTML, 2.8 MB | Image/audio steganography | Optional carrier extension |
| G0DM0D3 | TypeScript, 1.1 MB | Liberated chat front-end | Optional console |
| NATURALIS-HISTORIA | JavaScript, 780 MB | Generated encyclopedia corpus | Content mine, not infra |
| GL4SS | TypeScript, 0.8 MB | Spatiotemporal image/video engine | Arcade/Signal later |
| ENTEA | HTML, 0.1 MB | Music visualizer | Arcade polish later |
| V3SP3R | Java, 41.7 MB | Flipper Zero control | Hardware-gated; skip |
| Eos | Python, 19 KB | Discord dev orchestration | Architectural reference only |
| ImageDefender | HTML, 55 KB | Adversarial watermark | Signal tool, optional |
| AutoStoryGen | Python, 27 KB | Agentic story generator | Scriptorium novelty, optional |

The star counts are not the selection criterion. T3MP3ST, OBLITERATUS,
V3R1T4S, and GLOSSOPETRAE are selected because they fill a named gap in the
operating surface; the rest either duplicate local features or sit outside
the cockpit's job.

---

## 4. The four keepers

### 4.1 T3MP3ST — the offensive arm

**What it is.** An eight-operator kill chain (Recon, Scanner, Exploiter,
Infiltrator, Exfiltrator, Ghost, Coordinator, Analyst) driven by the agent
you already run. Ships a War Room UI on `127.0.0.1:3333`, an HTTP API, and an
MCP server (`security_recon`). 36 built-in tools by default; 111 with the
opt-in full arsenal. Its own benchmark claims are re-derivable with
`npm run verify-claims`.

**Why it fits.** Libertarium's Red Team Ops currently stops at model
guardrails. T3MP3ST adds the other axis: web apps, CTFs, source code, cloud
misconfiguration, mobile, binary. More importantly, its data model is almost
exactly the *department primitive* the operating surface asks for:
missions, hypotheses, work orders, evidence, findings, retests, repro packs.
That is the shape the ledger and workbench should copy.

**Integration.** Sidecar, not vendored.

```
Libertarium                          T3MP3ST
────────────                         ────────
Red Team Ops ── POST /api/mission/start ──▶ mission engine
             ◀─ GET  /api/mission/status ──
             ◀─ GET  /api/findings ────────
             ◀─ GET  /api/evidence ────────
             ── POST /api/evidence ───────▶ ledger mirror
```

- Start/stop with `process_control.py`; health at `/api/health`.
- Proxy server-side with `urllib.request` to avoid browser origin/CORS
  issues; never expose `:3333` to the network.
- Pull findings and evidence into the receipts ledger; keep T3MP3ST as the
  system of record for its own missions.
- Add a **WAR ROOM** link in the topbar that opens the proxied UI.
- Target authorization becomes a receipt field (`authorization`), not a
  free-text note.

**Hardware.** Node 24 is installed; the server is lightweight. No GPU needed.

**Risk.** Scope creep: this turns Libertarium from an AI red-team cockpit into
a general offensive-security platform. That is a product decision, not a
technical one. If the goal is strictly model research, make T3MP3ST an
optional organ and keep the gauntlet as the core.

---

### 4.2 OBLITERATUS — the laboratory and the sovereignty play

**What it is.** A full abliteration pipeline: SUMMON → PROBE → DISTILL →
EXCISE → VERIFY → REBIRTH. Seven methods (`basic` … `nuclear`), 15 analysis
modules (cross-layer alignment, refusal logit lens, concept cones, alignment
imprint, causal tracing, steering vectors), 116 model presets, a Gradio UI,
a CLI, a Python API, and an MCP server. Every saved model carries
`abliteration_metadata.json` with provenance.

**Why it fits.** The Laboratory department is explicitly greenfield: no
experiment log, no instrument. OBLITERATUS is the first real instrument.
It produces numbers (refusal rate, perplexity, KL, coherence), artifacts
(abliterated weights, direction tensors), and provenance — everything a
receipt needs. It also gives Libertarium a path to its own model: a local,
liberated model that agents can run without a provider's guardrail or quota.

**Integration.**

- `lab_runner.py` spawns `obliteratus obliterate <model> --method <m>
  --output-dir <dir>` and parses `abliteration_metadata.json`.
- A **LAB** page: model picker, method, dtype/quantization, run, live log,
  metrics table.
- Register the output as a local model. OBLITERATUS saves BF16 safetensors;
  serving needs a runtime (llama.cpp convert → GGUF, or vLLM). For a 6 GB
  laptop GPU, target 0.5B–4B models; 7B/8B only with 4-bit and headroom
  checks.
- Use its MCP read-only tools for analysis queries from agents.
- The `basilisk.py` cycle list already has a `mechanistic` category and
  quotes OBLITERATUS. This integration makes that category executable.

**Hardware reality.** RTX 4050 Laptop, 6 GB VRAM, 94.7 GB free. Realistic:
GPT-2, TinyLlama 1.1B, Qwen2.5-0.5B/1.5B, Phi-2 2.7B, Gemma-2 2B,
StableLM-2 1.6B. Treat 7B/8B as an experiment, not a default. The CPU path
works for tiny models but is slow.

**Risk.** Model downloads and VRAM. Make the lab optional; detect the GPU and
recommend a tier (`obliteratus models --tier small`).

---

### 4.3 V3R1T4S — the scientific method and the problem bank

**What it is.** An open notebook of 1,104 unsolved problems in 31 categories,
each with a success criterion, a checker, and a status. Runs on the agent you
already have. Its method is the valuable part:

- pre-register the criterion, statistic, thresholds, and null design in
  `PREREG.md` before measuring;
- run the pipeline on ≥400 decoys/controls;
- three fresh skeptic contexts, each writing its own code, can kill a claim;
- `kit/check_run.py` must print `CHECK_RUN: PASS`;
- a clean negative result is a good day; a false "solved" is the only real
  failure;
- nothing leaves the machine without the operator's explicit yes.

**Why it fits.** The local `research-pipeline/pipeline_runner.py` is a
deterministic prioritizer with no bank and no gate. V3R1T4S supplies both:
`bank/targets.json` is the bank, and `check_run.py` is the gate. It also
gives the Watchtower a real feed of open problems instead of generic news.

**Integration.**

- Shallow clone per their own instruction:
  `git clone --depth 1 --filter=blob:limit=1m https://github.com/elder-plinius/V3R1T4S`
  into an external organ path (137 MB is too much to vendor).
- `v3r1t4s_bank.py`: parse `bank/targets.json`, index by category, map
  fields into the local direction schema.
- Extend `research-pipeline/pipeline_runner.py` with `--source v3r1t4s`;
  keep the existing gauntlet-derived scoring.
- A **HUNT** mission preset: pre-registration → attack → null test →
  three skeptics → `check_run.py` → receipt only on PASS.
- Never auto-submit anything. Draft packages stay local until the operator
  says yes.

**Hardware.** No GPU. CPU-heavy checkers vary by problem.

**Risk.** The bank is large and the runs can be long. Start with one
category the operator actually cares about; do not import all 1,104 into the
queue at once.

---

### 4.4 GLOSSOPETRAE — the carrier lab

**What it is.** A procedural xenolinguistics engine: 25 modules for generating
languages (phonology, morphology, syntax, scripts, audio, evolution, code
languages), plus offense modules: `SemanticStego`, `SteganographyEngine`
(8 channels), `TokenExploiter` (BPE/glitch tokens), and `LanguageAttributes`
(phantom/adversarial). Its research results include a 12-model tokenizer
blind-spot map and a tag-character channel with 100% delivery / 0% caught in
its test.

**Why it fits.** The gauntlet's carriers are prompt-level (persona, encoding,
crescendo). GLOSSOPETRAE adds a channel-level axis: hide the payload in
tokenizer blind spots, semantic synonym choices, or a generated language.
That is a genuinely new class of test, not another wrapper. Its blind-spot
map also gives the gauntlet a data-driven preset: target each family with
the channel it cannot see.

**Integration.**

- `glossopetrae_bridge.mjs`: import the ES modules, expose a JSON stdin/stdout
  protocol (`generate`, `encode`, `decode`, `blindspots`).
- `tools/glossopetrae.py`: Python wrapper used by `prompt_mutator.py` and
  `auto_redteam.py`; new carrier family `stego` / `conlang`.
- Import the 78 result JSONs (or the summary blind-spot table) as
  `rt_library/gauntlets/metadata/`; add a **BLIND SPOTS** preset.
- Keep it optional: the Node bridge is a separate process, not a dependency
  of the core server.

**Hardware.** Node ≥18; installed (v24.20.0). Zero external deps for the
engine.

**Risk.** Do not port 25 modules to Python. Bridge the engine; port only the
two or three offense modules if the bridge proves fragile.

---

## 5. The near-misses

**P4RS3LT0NGV3 — upgraded via sidecar (2026-11-18).** The local
`parseltongue.py` exposes 94 techniques; upstream 4.0 has 222 transforms.
`p4rs3lt0ngv3_bridge.py` now runs the upstream Node bridge as an organ
(`organs/p4rs3lt0ngv3`, gitignored), registers every transform as
`p4_<key>` in the parseltongue registry, and adds `all-p4` / `all-extended`
carrier expansion. `parseltongue.py` remains the offline fallback, and the
server imports without Node. Update with `python p4rs3lt0ngv3_bridge.py
update`; do not rewrite 222 transforms by hand. Tokenade, Splitter and
Mutation Lab are still UI-only upstream tools — the CLI exposes the
transform catalogue and the universal decoder, not those three.

**CL4R1T4S — sync, don't copy.** The repo is 2.1 MB and updated daily. The
local `tools/cl4r1t4s_staging_sweeper.py` and
`cl4r1t4s_probe_regression_cron.py` already handle staging and hash-pinned
drift. Add a Watchtower sync job that pulls upstream, hashes files, diffs
against the last snapshot, and emits a signal per changed product. That
closes the CL4R1T4S department's "monitor → diff → publish" loop.

**LEAKHUB — read-only view.** A TypeScript leaderboard; not worth running as
a separate service. If the CL4R1T4S page needs a ranking, compute it from
the local archive instead.

**AutoTemp — port the utility.** 65 KB of Python that runs one prompt at many
temperatures and selects the best. Useful for calibrating gauntlet attacker
and judge sampling. Port the sweep loop into `gauntlet.py` or a tool; don't
adopt the repo.

**FRV1T / ST3GG — carrier packs.** FRV1T's 12 encoded "fruits" are creative
carriers; ST3GG adds image/audio stego. Both are optional extensions to the
carrier lab after GLOSSOPETRAE. Low effort, low priority.

**G0DM0D3 — optional console.** A 12k-star liberated chat UI. Libertarium
already has session chat; G0DM0D3 would only matter if the operator wants a
public-facing or standalone chat surface. Skip for now.

**NATURALIS-HISTORIA — content mine.** 780 MB; "the first encyclopedia."
Not infrastructure. If the Scriptorium needs a corpus later, mirror the data
separately; do not clone it into the cockpit.

**GL4SS / ENTEA — visual polish.** Both are visual engines. GL4SS could
generate Arcade assets or Signal media; ENTEA could replace the ASCII
visualizer with a real audio-reactive canvas. Nice-to-have after the
operational layer works.

**V3SP3R — hardware-gated.** Java, 41.7 MB, Flipper Zero control. Only
relevant if the operator owns the hardware. Skip.

**Eos — architectural reference.** A Discord-based orchestrator for
distributed devs. Libertarium's Flights/Lair already cover multi-agent
collaboration; Eos is a different product. Read it for the delegation
pattern, don't integrate it.

**ImageDefender / AutoStoryGen — niche.** Adversarial watermarking and
agentic story generation. Optional tools for Signal/Scriptorium; not part of
the core upgrade.

---

## 6. Architecture: the organ bus

### 6.1 Manifest

`organs/manifest.json` — one entry per engine. No secrets, no absolute paths
that only work on one machine.

```json
{
  "organs": [
    {
      "id": "t3mp3st",
      "name": "T3MP3ST",
      "department": "red-team-ops",
      "kind": "sidecar",
      "repo": "https://github.com/elder-plinius/T3MP3ST",
      "path": "organs/t3mp3st",
      "start": ["npm", "run", "server"],
      "health": {"type": "http", "url": "http://127.0.0.1:3333/api/health"},
      "port": 3333,
      "env": {"T3MP3ST_PORT": "3333", "T3MP3ST_HOST": "127.0.0.1"},
      "capabilities": ["mission", "findings", "evidence", "mcp"],
      "gate": "authorized-target"
    },
    {
      "id": "obliteratus",
      "name": "OBLITERATUS",
      "department": "laboratory",
      "kind": "cli",
      "repo": "https://github.com/elder-plinius/OBLITERATUS",
      "path": "organs/OBLITERATUS",
      "health": {"type": "command", "argv": ["obliteratus", "--version"]},
      "capabilities": ["abliterate", "analyze", "steer", "mcp"],
      "gate": "experiment-preregistration"
    },
    {
      "id": "v3r1t4s",
      "name": "V3R1T4S",
      "department": "laboratory",
      "kind": "kit",
      "repo": "https://github.com/elder-plinius/V3R1T4S",
      "path": "organs/V3R1T4S",
      "health": {"type": "file", "path": "bank/targets.json"},
      "capabilities": ["bank", "hunt", "check_run"],
      "gate": "three-skeptic-panel"
    },
    {
      "id": "glossopetrae",
      "name": "GLOSSOPETRAE",
      "department": "red-team-ops",
      "kind": "node-bridge",
      "repo": "https://github.com/elder-plinius/GLOSSOPETRAE",
      "path": "organs/GLOSSOPETRAE",
      "health": {"type": "command", "argv": ["node", "bridge.mjs", "--ping"]},
      "capabilities": ["generate", "stego", "blindspots"],
      "gate": "carrier-roundtrip"
    }
  ]
}
```

`kind` values: `sidecar` (long-running HTTP), `cli` (one-shot subprocess),
`kit` (files + scripts), `node-bridge` (JSON stdio), `dataset` (read-only).
Keep it small; add a kind only when a real organ needs it.

### 6.2 Lifecycle

`organ_runner.py`:

- `detect(id)` — path exists, binary/command resolves, health check passes.
- `start(id)` — `subprocess.Popen` with `CREATE_NEW_PROCESS_GROUP` on Windows;
  log to `logs/organs/<id>.log`; record PID in `state/organs.json`.
- `stop(id)` — `process_control.py` tree kill; wait; clear PID.
- `status(id)` — `detected | stopped | starting | healthy | unhealthy`.
- `health(id)` — HTTP GET, command exit 0, or file exists.
- `restart(id)`.
- On server startup: restore PIDs, mark orphans, optionally auto-start
  organs with `"autostart": true`.
- On server shutdown: stop only organs this cockpit started. Never kill an
  organ the operator started manually.

### 6.3 API

```
GET  /api/organs
GET  /api/organs/<id>
POST /api/organs/<id>/start
POST /api/organs/<id>/stop
POST /api/organs/<id>/restart
GET  /api/organs/<id>/log?lines=200
GET  /api/organs/<id>/health
```

All return JSON; errors use the existing `{ "error": ... }` shape. Add a
`strict` query parameter only if the UI needs it; keep the default cheap.

### 6.4 UI

The topbar already has a Departments button. Add **ORGANS** next to it:

- A small status dot per organ (green/amber/red/gray).
- Click opens a modal: name, department, kind, version, health, start/stop,
  log tail, and a link to the organ's own UI (if it has one).
- No iframe by default. Opening `http://127.0.0.1:3333/ui/` in a new tab is
  simpler and avoids proxying a full app. Add an iframe later if the
  operator wants one surface.

This is deliberately less than a plugin framework: a manifest, a runner,
three endpoints, one modal.

---

## 7. Architecture: the receipts ledger

### 7.1 Why

The repo produces artifacts in at least five shapes: gauntlet trophies,
session logs, tool outputs, village journals, and research briefs. There is
no single answer to "what did we prove this week?" The ledger is that answer.

### 7.2 Record

`state/ledger/receipts.jsonl`, one JSON object per line:

```json
{
  "id": "rcpt_20261102T184501_a1b2c3",
  "ts": "2026-11-02T18:45:01Z",
  "department": "red-team-ops",
  "organ": "t3mp3st",
  "kind": "finding",
  "title": "Auth bypass in local DVWA instance",
  "target": "http://127.0.0.1:8080",
  "status": "confirmed",
  "gate": {
    "name": "authorized-target",
    "result": "pass",
    "artifact": "state/ledger/artifacts/rcpt_.../scope.txt"
  },
  "artifacts": [
    "rt_library/gauntlets/runs/20261102-.../state.json",
    "state/ledger/artifacts/rcpt_.../report.md"
  ],
  "provenance": {
    "runner": "codex",
    "model": "gpt-5.5",
    "repo_commit": "89af6ad",
    "host": "windows"
  },
  "links": ["mission:...", "run:..."],
  "notes": "one line, no paraphrase of the evidence"
}
```

`kind`: `trophy | finding | experiment | claim | draft | signal | build`.
`status`: `confirmed | promising | negative | draft | rejected`.
`gate.result`: `pass | fail | pending | waived` (waived requires a reason).

### 7.3 Writers

| Producer | Kind | Gate |
|---|---|---|
| Gauntlet trophy | `trophy` | judge score ≥ 2 + verbatim artifact |
| T3MP3ST finding | `finding` | target authorization + evidence |
| OBLITERATUS run | `experiment` | pre-registration + metrics |
| V3R1T4S hunt | `claim` | `check_run.py` PASS + three skeptics |
| CL4R1T4S diff | `signal` | hash + source URL |
| Scriptorium/Signal draft | `draft` | operator approval |
| Forge release | `build` | `verify-claims` / smoke test |

### 7.4 API

```
GET  /api/receipts?department=&kind=&status=&limit=
GET  /api/receipts/<id>
POST /api/receipts          # internal + operator
POST /api/receipts/<id>/gate
```

Keep writes append-only. Corrections are new records with
`"supersedes": "<id>"`; never edit history.

### 7.5 UI

A **RECEIPTS** view (or a tab in the department workbench) with filters and
a detail drawer: title, status, gate, artifacts, provenance, links. This is
where the operator answers "show me the proof" without opening five folders.

---

## 8. Architecture: the department workbench

The operating-surface doc's open question #1 is "what does a department look
like in the UI?" Answer: a page, not a modal.

`departments.html` (served at `/departments`) with one tab per department:

- **Charter** — name, tagline, core loop, scaling pattern, ship criteria.
- **Queue** — missions/work orders for this department; claim/complete.
- **Receipts** — filtered ledger entries.
- **Memory** — tail of `state/departments/{id}.jsonl`.
- **Handoff** — promote a receipt to another department's queue.

Backend additions:

```
GET  /api/departments/<id>            # charter + counts + queue + memory tail
GET  /api/departments/<id>/queue
POST /api/departments/<id>/queue
POST /api/handoff                     # {receipt_id, to_department, note}
```

The handoff is the cross-department pipeline the operating surface describes:
Red Team crack → Signal draft; Lab finding → Forge build; Watchtower signal →
Red Team probe; CL4R1T4S diff → Watchtower signal.

Start with three departments wired end-to-end (`red-team-ops`,
`laboratory`, `watchtower`), then add the rest. Do not build ten empty
dashboards.

---

## 9. Phased plan

### Phase 0 — Foundation (organ bus + ledger)

Files:
- `organs/manifest.json`
- `organ_runner.py`
- `receipts.py`
- `tests/test_organs.py`, `tests/test_receipts.py` (new `tests/` dir; the
  root `test_*.py` pattern is gitignored, `tests/` is not)
- `server.py`: `/api/organs*`, `/api/receipts*`
- `index.html`: ORGANS button + modal

Acceptance:
- Four organs show `detected`/`missing` correctly.
- Start/stop leaves no orphan process (check with `Get-Process node`).
- Receipts survive restart; `GET /api/receipts` returns the same records.
- Unit tests pass without network access.

Effort: 1–2 days.

### Phase 1 — T3MP3ST War Room

Files:
- `organ_runner.py` entry for `t3mp3st`
- `t3mp3st_bridge.py` (health, mission start/status/report, findings/evidence)
- `server.py`: `/api/warroom/*` server-side proxy
- `index.html`: WAR ROOM link
- `docs/WARROOM.md`

Acceptance:
- `POST /api/warroom/mission` starts a mission against a local test target.
- `GET /api/warroom/status` mirrors T3MP3ST's status.
- A completed finding appears in `/api/receipts` with its evidence path.
- No request leaves `127.0.0.1`; the organ port is not exposed.

Effort: 1 day after Phase 0.

### Phase 2 — OBLITERATUS Lab

Files:
- `lab_runner.py` (job spec, subprocess, metadata parse)
- `server.py`: `/api/lab/*`
- `lab.html` (or a tab in the workbench)
- `docs/LAB.md`

Acceptance:
- Run a tiny model (GPT-2 or Qwen2.5-0.5B) end-to-end on the 4050.
- `abliteration_metadata.json` metrics appear in the UI and the ledger.
- A failed run (OOM, missing model) is recorded as `negative`, not lost.
- Model output path is registered; serving instructions are printed.

Effort: 1–2 days, plus model download time.

### Phase 3 — V3R1T4S Hunt

Files:
- `v3r1t4s_bank.py`
- `research-pipeline/pipeline_runner.py`: `--source v3r1t4s`
- `setup/starter_rt_library/gauntlets/presets/hunt.yml` (mission template)
- `docs/HUNT.md`

Acceptance:
- `pipeline_runner.py auto --source v3r1t4s --dry-run` ranks bank targets.
- A HUNT mission writes `PREREG.md`, `REPORT.md`, `LOG.md`.
- A claim without `CHECK_RUN: PASS` cannot become a confirmed receipt.
- One real run completes (positive or negative) and lands in the ledger.

Effort: 1 day plus run time.

### Phase 4 — Department workbench + Watchtower

Files:
- `departments.html`, `departments.js` (or extend `ui-shell.js`)
- `watchtower.py` (signal store + daily brief)
- `cl4r1t4s_sync.py` (git pull, hash, diff, signal)
- `server.py`: department + handoff + watchtower endpoints

Acceptance:
- Three departments have live queue/receipts/memory.
- A CL4R1T4S diff produces a Watchtower signal.
- A Red Team receipt can be handed off to Signal and appears in its queue.
- The daily brief is short enough to read and links to sources.

Effort: 2 days.

### Phase 5 — Carriers (GLOSSOPETRAE + P4RS3LT0NGV3)

Files:
- `organs/glossopetrae_bridge.mjs`
- `tools/glossopetrae.py`
- `p4rs3lt0ngv3_bridge.py` + `tools/p4rs3lt0ngv3.py` (done)
- `setup/starter_rt_library/gauntlets/presets/blind_spots.yml`
- `tools/prompt_mutator.py`: new carrier family
- `tools/auto_redteam.py`: new ladder rung

Acceptance:
- A payload round-trips through the stego channel (encode → decode).
- The blind-spot preset lists targets from the imported results.
- The gauntlet can run one carrier campaign end-to-end.
- `parseltongue.py` still works offline if Node is absent.

Status (2026-11-18): the P4RS3LT0NGV3 half is done — sidecar bridge, `p4_<key>`
carriers, `all-p4`, provenance receipts, 19 tests. GLOSSOPETRAE remains.

Effort: 2–3 days.

### Phase 6 — Sovereignty + packaging

Files:
- Local model serving script (`serve_local.py` or documented llama.cpp
  command)
- Runner registration in `agent_settings.py` / `rt_send.py` (careful: do not
  disturb the existing `--timeout` work)
- `docker-compose.yml` (optional; Windows-first remains the primary path)
- `scripts/backup_state.ps1` / `restore_state.ps1`

Acceptance:
- A local model answers a prompt through the cockpit without a provider key.
- One command starts cockpit + selected organs; health panel is green.
- Backup/restore round-trips `state/`, `rt_library/`, `organs/` metadata.

Effort: 1–2 days.

---

## 10. Hardware reality

Verified on this machine:

| Item | Value | Consequence |
|---|---|---|
| GPU | RTX 4050 Laptop, 6 GB VRAM | OBLITERATUS: 0.5B–4B models; 7B/8B only 4-bit with care |
| Disk free | 94.7 GB | Enough for 4–8 small models; not a 70B collection |
| Node | v24.20.0 | T3MP3ST, GLOSSOPETRAE, P4RS3LT0NGV3 all run |
| Python | 3.12.8 | OBLITERATUS, V3R1T4S, cockpit all run |
| OS | Windows | Reuse `process_control.py`; org ports stay loopback |

The T3MP3ST and V3R1T4S paths are not hardware-bound. The OBLITERATUS path is
the only one that needs a GPU tier decision.

---

## 11. Licensing and vendoring

- T3MP3ST: AGPL-3.0.
- OBLITERATUS: AGPL-3.0.
- V3R1T4S: AGPL-3.0 code, CC-BY-4.0 data.
- GLOSSOPETRAE: AGPL-3.0.
- P4RS3LT0NGV3: see the repo's `LICENSE`; the local port already carries its
  attribution.

Engineering consequence: run these as separate processes and integrate over
HTTP/CLI/JSON. Do not copy their source into `kaiken-harness` unless the
combined work is intended to carry AGPL obligations. Sidecar integration also
keeps updates simple: `git pull` in the organ, not a merge into the monolith.

---

## 12. What not to do

- **Do not vendor T3MP3ST or OBLITERATUS into `server.py`.** They are active
  AGPL projects with their own release cadence.
- **Do not clone NATURALIS-HISTORIA (780 MB) into the cockpit.** Treat it as
  a separate content mirror if it is ever needed.
- **Do not port all 25 GLOSSOPETRAE modules to Python.** Bridge the engine.
- **Do not rewrite all 222 P4RS3LT0NGV3 transforms.** Bridge the CLI; port
  only the high-value missing ones.
- **Do not import all 1,104 V3R1T4S problems into the queue.** Start with one
  category.
- **Do not touch the uncommitted `rt_send.py` timeout work.** Build around it.
- **Do not add authentication-free organ proxies on a public interface.**
  Everything stays on `127.0.0.1`.
- **Do not build ten department dashboards before three departments have real
  queues and receipts.** The surface follows the work, not the other way
  around.

---

## 13. First build

**Phase 0 + Phase 1: organ bus, receipts ledger, T3MP3ST War Room.**

Why this order:

1. It creates the layer every later organ needs, so OBLITERATUS, V3R1T4S, and
   GLOSSOPETRAE become manifest entries instead of one-off hacks.
2. T3MP3ST is the largest capability jump that does not depend on GPU,
   model downloads, or a new scientific workflow.
3. It produces immediate receipts, which validates the ledger with real
   artifacts instead of synthetic tests.
4. It keeps the gauntlet intact. T3MP3ST is an additional axis, not a
   replacement.

The first commit should be `organs/manifest.json`, `organ_runner.py`, and
`receipts.py` with tests — no UI yet. The second commit wires the endpoints
and the ORGANS modal. The third adds the T3MP3ST bridge and the WAR ROOM
link. That keeps the monolith changes small and reviewable.

If the priority is sovereignty rather than capability, swap Phase 1 and
Phase 2: build the OBLITERATUS lab first and register a local model as the
fourth runner. The foundation is the same either way.
