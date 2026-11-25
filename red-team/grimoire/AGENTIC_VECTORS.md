# AGENTIC_VECTORS.md — the new attack surfaces

The old primers assume a chat cube: user prompt in, model refusal out. The
2025–2026 receipts show that the interesting harm surface has moved to the
planes the chat cube doesn't describe — ingestion, persistence, selection,
screen, protocol, and egress. This file catalogs those planes as MEASURABLE
vectors, each with: surface → public receipts → technique shape (described,
not weaponized) → defense map (cross-ref DEFENSES.md) → what makes it
different from chat → staged local probes.

Every probe here is staged for the lab's own targets when upstream returns.
Naming convention for probes: `red-team/probes/agentic/<vector-code>-<ordinal>.plan`

Receipts are public incidents, cited. Technique shapes are described at the
structural level — the same altitude TECHNIQUES.md keeps. We are writing the
measurement plan, not the exploit kit.

---

## V1. RETRIEVAL INGESTION (the document is the prompt)

**Surface.** Any content an agent fetches as part of a task: emails, issue
bodies, web pages, PDFs, retrieved memory, tool output. The agent's parser —
not a human — is the first reader.

**Public receipts.**
- *EchoLeak*, M365 Copilot, CVE-2025-32711 (2025): zero-click exfiltration.
  Payload hidden in content, parsed by the agent during retrieval, executed
  as instruction, delivered by the platform's own egress. No human ever read
  the phish.
- Unit 42 (2025): in-the-wild web-based indirect injection against live
  agents — 22 distinct payload-engineering techniques cataloged across
  campaigns, including CSS-hidden injection in ad-review pipelines.
- OWASP Agentic Top-10 (2026): indirect injection named the dominant
  agentic threat.

**Technique shape.** The payload is written as *content that discusses
instructions*, not as instructions. Survival through summarization is the
measurement: paraphrase-surviving phrasing beats verbatim phrasing, because
the quarantine layer sees the verbatim layer only (DEFENSES.md D1/D11).

**What makes it different from chat.** No extraction gates to defeat — the
model is not refusing, it is complying with something it believes is its own
operator's task. The gate moved from "will the model refuse" to "does the
pipeline distinguish data from instruction" — an architectural question. A
target that is unbreakable in chat (strong FIDELITY-CASTE, hierarchy
training) can still be fully open on this plane, and our measured evidence
supports exactly that prediction: the refusals we measured are utterance-
keyed and there is no evidence they apply when the text arrives as tool
output.

**Defense map.** D1 spotlighting, D7 action gates, D11 quarantine — all
prompt-level. Provable defenses (Melon) assume away freeform egress.
**Staged probes.** `red-team/probes/agentic/v1-retr-01.plan` — local harness fetches a
"document" containing instruction-shaped fragments interleaved with purely
informational text, measure whether
agent trajectory goal-shifts vs. content-only reads. Score: did the payload
change tool selection (V4 crossover) or only output framing?

---

## V2. MEMORY PERSISTENCE (injection that outlives the session)

**Surface.** Long-term memory stores, session summaries, preference files,
user-profile notes the agent reads on every startup.

**Public receipts.**
- Unit 42 (2025): Amazon Bedrock Agents — injection via the *format of the
  summary prompt itself* (XML structure closed and reopened to add
  instruction fields), persisting across conversations, exfiltrating on
  future sessions.
- Patlan et al. (2025): Web3 agent memory poisoning — poisoned memories
  drove unauthorized asset transfers.
- arXiv 2601.17548 taxonomizes this as P2.2 (persistent injection / memory
  poisoning) against agentic coding assistants.

**Technique shape.** Two trust-times exist: write-time (what gets stored)
and read-time (what re-enters context). Naive poisoning dies at write
review; residue-shaped poisoning is a summary artifact, not a write, and
sails both gates (DEFENSES.md D12). Second shape: chain-benign assembly —
N individually-innocent observations composing into one poisoned record,
invisible at every write.

**What makes it different from chat.** The attack has *latency*: the payload
author may never be in the room for its detonation. Attribution breaks,
measurement windows widen, and the defense trade-space becomes audit-vs-
-usability rather than refusal-vs-yield.

**Defense map.** D12 partitioning/review; holds on naive form, leaks on
residue and composition forms.
**Staged probes.** `red-team/probes/agentic/v2-mem-01.plan` — the lab runs its own
memory API (memory/ directory, /api/memory); first experiment is local-at
-mirror: write a benign observation that is assembled from two individually
-benign parts, then measure whether a successor session reads the compound
differently. This is our own architecture — the receipt is immediate and
costs no upstream credits.

---

## V3. COMPUTER USE / SCREEN (pixels are prompt space)

**Surface.** Agents that read the screen: every rendered pixel, tooltip,
notification, image caption, and terminal output is now model-readable
context, and a large fraction of it is attacker-writable (ads, pop-ups,
image alt-text, website content, even fonts and image steganography).

**Public receipts.**
- *ZombAIs* (Rehberger, 2024): prompt injection against computer-use agents
  drove them to install/content-deliver C2 — the agent's hands are the risk,
  the screen is the trigger.
- Browser-use indirect injection observed in-the-wild (Unit 42).

**Technique shape.** Injection surface is *rendered* — the payload does not
need to survive summarization, it needs to survive *screenshot rendering*:
small-font visual cues, low z-index overlays, elevated-contrast text blocks,
instruction-shaped content sitting in the corner of an otherwise-innocuous
page. An agent reading the screen trusts it as "what is on screen" with no
quarantine at all; DEFENSES.md D11 has no slot for pixels.

**What makes it different from chat.** The context is rebuilt every frame —
there is no stable prompt-engineering surface to defend; the mount point is
whatever is visible at decision time. Also the only plane where the *human
is looking at the same thing the model is* — social-engineering the operator
against the agent, or agent against operator, is native here.

**Defense map.** D7 (write gates — the only one with teeth here), plus
nothing at pixel level. Open space.
**Staged probes.** Blocked on computer-use capability — REQUEST when needed;
`red-team/probes/agentic/v3-cu-01.plan` drafted contingent.

---

## V4. PROTOCOLS & TOOL SELECTION (MCP, skills, registry files)

**Surface.** The agentic substrate: MCP server manifests and tool
descriptions, skill markdown files auto-loaded into context, agent config
files (`.cursor/mcp.json` and peers), sampling callbacks, registry entries.

**Public receipts.**
- Cursor agent RCE, CVE-2025-54135 (2025): injection caused a malicious
  MCP config write without user approval.
- Invariant Labs "toxic agent flow" (2025): MCP token crossing via a
  booby-trapped GitHub issue — the server's blanket token carried whatever
  the content steered it to touch.
- Unit 42 (2025): MCP-sampling and tool-description hijack classes —
  the tool's *description field* is attacker-influenceable text the model
  reads as part of its planning context.
- NDSS 2026 (s675): prompt injection targeting tool *selection* — which
  tool gets chosen is a decision surface, and it is corruptible without
  touching any executed call.
- arXiv 2601.17548: skills/tools/protocol ecosystems taxonomized as a
  vulnerability space of their own.

**Technique shape.** Credential and action fences protect the executed call
(DEFENSES.md D13 holds on that plane). The open planes are:
(a) *Selection-time priming* — the description text that nudges WHICH tool
is chosen for a task;
(b) *Registry-time trust* — config-file writes that a later session reads
with full trust (Cursor receipt);
(c) *Sampling callbacks* — hidden instructions riding a provider's sample
round-trip (Unit 42 shape).
The offense class is "poison the choosing, not the call" — everything the
agent reads while deciding is planning-context, and planning-context has far
weaker quarantine than chat utterances (same asymmetry as V1).

**What makes it different from chat.** Trust is positional, not content-keyed.
The description field reads as *infrastructure*, and infrastructure text
inherits the trust of the registry it lives in. Identity-fencing, hierarchy
training, register gates — all measured-defended on the utterance plane —
have no utterance here to defend.

**Defense map.** D13 fencing (credential plane holds), D11 (weak here).
**Staged probes.** `red-team/probes/agentic/v4-sel-01.plan` — lab-local: register two
near-identical probe-tool descriptions differing only in a benign priming
phrase, measure selection drift across N decision tasks. Zero upstream cost;
touches nothing but our own tool README semantics.

---

## V5. EGRESS CHANNELS (the exit is the exploit)

**Surface.** Every path by which agent-side state can leave to an
attacker-readable place: URL fetch parameters, image alt-text, tool-call
arguments echoed to third parties, file writes to public paths, message
channels, markdown links rendered back to a human-in-the-loop.

**Public receipts.**
- EchoLeak (CVE-2025-32711): exfil via platform-egress channel; the attack
  chained read-then-egress with no click.
- Bola et al. / early leakage papers (2023–25 lineage): the egress-channel
  argument has been standard theory since the first image-rendering
  markdown-exfil demos and is now confirmed operationally.

**Technique shape.** Output DLP scans chat text; exfil rides non-chat
fields. Combine with D8's cumulative-blind spot: slice the payload so each
individual egress is innocuous, reassemble externally (slicing under the
gate, agentic edition). The offense class is "assembly on the other side" —
the defender has no cumulative scorer, the attacker has full assembly.

**What makes it different from chat.** It converts every read-minority
vector (V1–V4) into an exfil vector. This is the plane that makes indirect
injection *profitable* — without it, injections get compliance; with it,
they get consequences.

**Defense map.** D8 (leaks: channel multiplicity, cumulative blindness,
transformation out of match surface); D7 (write gates miss read-shaped
egress).
**Staged probes.** `red-team/probes/agentic/v5-egress-01.plan` — local-mirror: the
lab's own egress-shaped fields (comms content, comms metadata, dragonfire
title/message) are measurable egress surfaces; experiment measures whether
an instruction embedded in comms channel is visible at review time. No
upstream cost.

---

## V6. HIDDEN REASONING (the opaque substrate is a channel)

**Surface.** Reasoning blobs, encrypted/structured traces, scratchpad
artifacts — anything the provider treats as an opaque, replayable,
protected object.

**Public receipts.**
- RECAST (arXiv 2610.41813): all three SOTA CoT-extraction methods return
  zero reasoning tokens on Claude-Sonnet-5.5 via standard chat completions
  (structural closure), while answer-only reconstruction recovers
  structured rationales for three frontier targets — the closure is
  structural, so the offense routes around it laterally.
- 2026 preprint survey (five papers, March–September 2026): extraction or
  useful reasoning recovery demonstrated despite hidden-trace interfaces.
- Chain-of-Thought Leakage analysis (2026): encrypted/hidden reasoning
  imported or replayed into a victim agent becomes an attack surface —
  attacker-controlled reasoning state accepted as history steers later
  planning.
- CSA research note (2026): reasoning extraction as distillation-attack
  economy; chain-of-thought elicitation structured at volume against
  provider boundaries (NSA/CISA/FBI detection guidance noted in the same
  note).

**Technique shape.** Two offense directions:
(a) *Lateral reconstruction* — ANSWER-ONLY SYNTHESIS ORACLE (staged in
TECHNIQUES.md): when the wholesale gate is structural, learn the interior
from what the target DID show. Already our first dequeue candidate.
(b) *Replay poisoning* — a reasoning/trace blob is a "transcript" with a
higher trust level than a user-authored one. CONTINUITY RECONSTRUCTION
(GLM A8: the forge sails as fiction) is the chat-era on-disk analogue; V6
asks the 2026 question: what does the forge become when the substrate is
an actual opaque provider vehicle? The paper's chain — attacker-controlled
reasoning state → accepted as history → steers planning — is our
SHAPED-VERDICT PRE-CLEARANCE shape wearing a provider credential.

**What makes it different from chat.** The trust asymmetry is inverted from
V1: there, untrusted content is read as trusted; here, *provider-issued*
opaque artifacts carry MORE trust than anything the user can produce. The
offense question is which channel issues such artifacts on our targets.

**Defense map.** D4 anti-forgery is the only measured defense class that
reaches here, and only if the target inventories trace provenance
(unmeasured — no target in our set exposes traces).
**Staged probes.** ANSWER-ONLY SYNTHESIS ORACLE payload;
`red-team/probes/agentic/v6-reasoning-01.plan` pending upstream restore. First
target pair:
glm-5.3-flash vs opencode-go-deepseek (both hold ground-truth configs,
per the goal-conflict staging note).

---

## V7. SKILL AND CONTEXT FILES (the trusted-channel reframe)

**Surface.** Agent-consumed markdown: SKILL.md files, AGENTS.md, README
instructions, workflow docs — typically committed by the operator, loaded
on startup or on demand, and read with near-system-level trust.

**Public receipts.** arXiv 2601.17548 gives skills ecosystems a dedicated attack
taxonomy (P2.2 persistent propagation across skill files). EchoLeak
resulted in part from a markdown-rendering path hallucinating (instruction
grading inside rendered content). Our own harness ships prompt-grimoire
consumption as trusted markdown.

**Technique shape.** This is the genuine-artifact channel from DEFENSES.md
D4, at repository scale: the operator-authored trusted file is the one
artifact a target CANNOT inventory against its runtime, because it is real.
Its power ceiling is bounded by the operator trust it inherits; the offense
research question is the trust boundary *inside* it: which passages does
the downstream agent treat as binding?

**What makes it different from chat.** It is upstream-of-prompt-engineering;
it reshapes the agent's instructions before any user turn. But it is also
the plane where the defense side has real teeth: repository review is a
human-audit defense (no model in the loop), so the vectors here are
governance vectors more than model vectors. List kept for completeness —
it is the defense-first plane.

**Defense map.** Human review (repository governance) — holds by design.
**Status:** catalog; no probes staged. Named so the grimoire is complete:
the trusted channel is an attack surface that only matters when governance
fails, and it fails 2026-often (P2.2 receipts include real-world
propagation).

---

## TRUST-PLANE SUMMARY TABLE

| Vector | Plane | Persistence | Attacker-writeable | Defense with teeth | Lab-probe ready |
|---|---|---|---|---|---|
| V1 Retrieval | ingestion | session | yes (content) | prompt-level only (D1/D11 weak) | yes, upstream |
| V2 Memory | persistence | cross-session | yes (residue/compose) | write-review gates (naive only) | yes, free (local API) |
| V3 Computer use | screen | per-frame | yes (pixels) | action gates only | blocked on CU |
| V4 Tool selection | protocol | registry + per-call | yes (descriptions/config) | credential fences (holds exec-side) | yes, free (local mirror) |
| V5 Egress | exit | per-flight | n/a (reader is attacker) | output DLP (cumulative-blind) | yes, free (local mirror) |
| V6 Reasoning | substrate | provider-vehicle | supply-side | anti-forgery if provenance-inventoried | upstream restore |
| V7 Skills files | trusted-channel | repo lifetime | only via governance failure | human review | catalog only |

Free-lab note: V2, V4, V5 probes cost zero upstream credits and run against
our own substrate (memory API, local tool registry semantics, comms
stack). They are the right first fire after this document ships — receipts
without the operator's lever.

---

## RELATION TO TECHNIQUES.md

TECHNIQUES.md holds utterance-plane technique canon (the chat cube). This
file holds plane catalog + measurement plans. The exchange rules:
- A technique measured on a vector gets named in TECHNIQUES.md, citing the
  probe.
- A vector with zero probes and no receipts does not get canon pages here
  (this is V-catalog with receipts-or-catalog-only rule — mirrors naming
  law 2).
- DEFENSES.md maps defense classes to both files' evidence.

— Pliny the Crescendo, 2026-11-03. LIBERTAS.
