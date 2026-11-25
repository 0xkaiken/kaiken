<p align="center">
  <img src="assets/kaiken-logo.svg" alt="KAIKEN — 懐剣" width="580" />
</p>

<p align="center">
  <strong>Single-operator mission control for parallel AI agents, desktop/browser automation, and frontier-model red-team evaluation on <code>localhost:8888</code>.</strong><br/>
  No build step, no frontend framework — Python 3.11+ standard library, PyYAML, and four live dashboards.
</p>

> **Fork notice:** **Kaiken** is a Windows-first, multi-runner fork of **Pliny Command / Libertarium** ([`dragons-blood/libertarium-dev`](https://github.com/dragons-blood/libertarium-dev)), originated by [@elder_plinius](https://twitter.com/elder_plinius). Published under the MIT License with the upstream author's permission — see [NOTICE.md](NOTICE.md).

<p align="center">
  <img src="assets/demo-tour.gif" alt="Kaiken dashboard walkthrough on Windows" width="820" />
</p>

---

## What's Different in Kaiken

| Area | Upstream (`libertarium-dev`) | Kaiken |
| --- | --- | --- |
| **Platform** | macOS / Linux (`install.sh`, Keychain sidecar) | **Native Windows-first** (`start-windows.cmd`, `install_windows.py`, UTF-8 defaults, process-tree cleanup) + macOS/Linux — see [WINDOWS_COMPATIBILITY.md](WINDOWS_COMPATIBILITY.md) |
| **Agent Runners** | Claude Code only | **3 swappable runners** — **Claude Code**, **GPT / Codex**, and **OpenCode Go** (per mission, app-wide, or per Gauntlet run) |
| **Computer Use** | Browser / macOS flow | **Windows Desktop mode** (native Win32 input, monitor picker, click-through HUD badge & edge glow, `ESC` / top-left corner emergency pause, operator-only resume, scoped capabilities) + Playwright Browser mode |
| **Security & Auth** | Unauthenticated localhost, macOS Keychain | **One-time console pairing** (`pliny_session` hashed cookie), fail-closed privileged routes, `.env` / `state/secrets.json` (no Keychain), **Account Guard** (`account_guard.py`) blocking unauthorized Claude/ChatGPT subscription spend, scoped per-session key files & `workshop` permission posture |
| **Red-Team & Eval** | Command, Gauntlet, Arcade | Adds **Carrier Lab** (`/lab`) for A/B carrier matrices, PyRIT campaigns & AutoTune; **ARTEX** structured mode with closed-judge receipts; **Pack Attack** per-run registry & deterministic follow; hardened abort/relaunch controls; P4RS3LT0NGV3 bridge (222+ transforms) |
| **UI & Observability** | Separate page chrome | **KAIKEN Urushi** dark lacquer UI across all 4 dashboards, built-in **Guide** drawer (`F1`), mission photo drag-and-drop attachments, and live Markdown + KaTeX LaTeX log rendering |
| **Quality & CI** | Manual | 1,050+ Python tests, 15 Node UI harnesses, API surface audit, Windows CI, and Gitleaks history scanning |

---

## Setup

### Prerequisites
- **Python 3.11+** (`python --version` or `py -3 --version`)
- **At least one agent CLI** installed and signed in:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code): `npm install -g @anthropic-ai/claude-code && claude`
  - [Codex CLI](https://github.com/openai/codex) *(optional)*: `npm install -g @openai/codex && codex`
  - OpenCode CLI *(optional, for `opencode-go/` models)*
- **Provider API key(s)** for red-team targets / judge (OpenRouter covers both the judge and Hermes fallback)

### 1. Clone & start

```bash
git clone https://github.com/0xkaiken/kaiken.git
cd kaiken
```

**Windows:**
```powershell
.\start-windows.cmd
```

**macOS / Linux:**
```bash
./quickstart.sh
```

The launcher seeds starter state (`state/`, `rt_library/`, `.env`) and starts the dashboard on `http://localhost:8888/`. For optional Windows Desktop Computer Use, Playwright browser mode, and tests:

```bash
python -m pip install -r requirements.txt
```

### 2. Configure API keys

Edit `.env` in the repo root (copied from [`setup/.env.example`](setup/.env.example)) and restart the server. Only configured providers are enabled; targets without a key are skipped automatically.

| Variable | Role |
| --- | --- |
| `OPENROUTER_API_KEY` | LLM judge + Hermes fallback + OpenRouter targets |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `XAI_API_KEY` | Direct provider targets |
| `MISTRAL_API_KEY` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` | Mistral, DeepSeek, Qwen targets |

### 3. Pair your browser

Open **`http://localhost:8888/`** and enter the one-time **pairing code** printed in the server console. Pairing sets a hashed `pliny_session` cookie (30-day default, stored in `state/paired_devices.json`). For headless/CLI calls, set `PLINY_AUTH_TOKEN` and pass `Authorization: Bearer $PLINY_AUTH_TOKEN`.

---

## How to Use

Press **`F1`** (or click **Guide**) on any page for interactive in-app docs.

| Dashboard | URL | What to do there |
| --- | --- | --- |
| **Command** | `/` | Pick an engine (**Claude Code**, **GPT / Codex**, **OpenCode Go**), describe a task or pick a preset, drag-and-drop images, optionally enable **Browser** or **Windows Desktop** Computer Use (`ESC` or top-left corner pauses input instantly), and watch live Markdown/LaTeX output. |
| **Blood Agent & Gauntlet** | `/redteam` | Run bounded single-target probes (Custom, presets, or **ARTEX** structured mode), fire parallel **Pack Attack** hunts (click pack-strip chips to switch & backfill views), or run multi-model **Gauntlet** sweeps with a 0–3 LLM judge scoreboard. |
| **Carrier Lab** | `/lab` | Run controlled A/B carrier experiments against one payload, launch PyRIT multi-turn campaigns, and run AutoTune sweeps across 120+ built-in transforms (optional 222+ extra transforms via `python p4rs3lt0ngv3_bridge.py sync`). |
| **The Lair** | `/arcade` | Interactive 8-bit village view to summon persistent agents, dispatch packs, and inspect activity and replays. |

<details>
<summary><strong>Screenshots (all 4 dashboards)</strong></summary>

| Command (`/`) | Blood Agent (`/redteam`) |
| :---: | :---: |
| ![Command](assets/mission-control.png) | ![Blood Agent](assets/gauntlet.png) |
| **Carrier Lab (`/lab`)** | **The Lair (`/arcade`)** |
| ![Carrier Lab](assets/carrier-lab.png) | ![The Lair](assets/arcade.png) |

</details>

---

## Safety, Scope & Docs

- **Authorized research only.** Only evaluate models and systems you are authorized to test; keep trophies and raw outputs private (see [`PUBLISHING.md`](PUBLISHING.md)).
- **Localhost only.** Binds to `127.0.0.1:8888` for a single operator — do not expose to a public network. Same-user OS processes are not isolated from each other; run untrusted agents in a separate OS account, VM, or container.
- **Subscription protection.** Agents and tests never spend your Claude or ChatGPT subscription seats without explicit operator authorization (`account_guard.py`).
- **Docs:** [`WINDOWS_COMPATIBILITY.md`](WINDOWS_COMPATIBILITY.md) · [`CLAUDE.md`](CLAUDE.md) · [`AGENTS.md`](AGENTS.md) · [`SECURITY.md`](SECURITY.md) · [`NOTICE.md`](NOTICE.md) · [`red-team/README.md`](red-team/README.md)

---

*MIT License — see [LICENSE](LICENSE). Forked with permission from [@elder_plinius](https://twitter.com/elder_plinius) ([`dragons-blood/libertarium-dev`](https://github.com/dragons-blood/libertarium-dev)).*
