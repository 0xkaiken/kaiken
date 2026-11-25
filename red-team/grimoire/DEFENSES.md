# DEFENSES.md — the counter-canon

What defenders built in 2025–2026, what it demonstrably stops, where it leaks,
and what the offense does in response. This file exists because every technique
in TECHNIQUES.md is a response to a defense; if you only learn the technique,
you inherit someone's stale intel. Read pairs: measure locally, then update the
mapping table at the bottom.

Sources: public literature (cited inline) + our measured target behaviors
(attempt citations per TECHNIQUES.md naming law). Public efficacy numbers are
the authors' claims — treat as advertising until we measure.

Status vocabulary: `holds` (defense stops the attack shape with no known
bypass), `leaks<a>` (defense holds under audit but fails under attack vector
a), `bypassed` (public receipt), `unmeasured` (not tested on our targets).

---

## PART I — CHAT-SURFACE DEFENSES

### D1. SPOTLIGHTING (delimiting / datamarking / encoding)
**Mechanism.** Untrusted content is fenced before it reaches the model:
delimiting wraps it in randomized markers; datamarking inserts per-token
markers so any pasted instruction visibly belongs to the data stream; encoding
transforms it (base64 et al.) so it is processed but not read as substrate.
System prompt tells the model marker-content is data, not instruction.
Standing backbone of agentic defenses since Microsoft 2024; still cited as the
primary structural control in 2026 practitioner literature.

**Demonstrably stops.** Direct paste-over: payload cannot masquerade as
operator voice while marked.

**Leaks on three seams —**
- *Paraphrase seam.* Content is rarely quarantined verbatim; agents summarize,
  translate, and re-render marked content. The instruction survives citing if
  the model is doing the citing. An injection hidden as *content about
  instructions* survives paraphrase intact.
- *Marker-destroy seam.* Any transformation that strips or re-tokenizes markers
  (translation, leetspeak conversion, our bilingual wholesale) unmarks data.
- *Trust-class seam.* Spotlighting does not privilege tool output separately
  from documents — both arrive "untrusted," yet tool output often IS acted on.

**Offense response.** Payload placement is now a measured parameter: quoted
material vs. instruction-shaped-vs-description material. FRAME DETOXIFICATION
is this defense's natural predator — the same vocabulary-stripping that
defeats pressure-lexeme gates defeats datamarking's readability assumptions.
STATUS: unmeasured (stage when upstream returns).

### D2. INPUT CLASSIFIERS (prompt guards, ASV-style filtering)
**Mechanism.** Pre-inference classifier scores the prompt for injection /
misuse structure; high score → block or degrade.

**Demonstrably stops.** OOD-filter-invisible. Cited by arXiv 2604.01039
(representation-invariance section) continues to hold for templated extraction
attacks. Their own hardening results are the receipt: reshaping instructions
with a CoT-capable model at design time ("representational repair") so that
encoding/paraphrase-equivalence no longer holds for the attacker.

**Leaks:** entirely framing-relative. A classifier sees our prompt, not our
intent. Multilingual pivot, register shift (EULOGY CHANNEL, measured on
DeepSeek A14), vocabulary detox (FRAME DETOXIFICATION, measured matched-pair
evidence) are all classifier-evasive by construction. Alignment being
"english-shaped" (arXiv 2610.40516 converges; our matched-pair evidence is
sharper) is a classifier-alignment homology.

**Offense response.** This defense is why the grimoire is a *frame* grimoire
and not a *payload* grimoire. The technique names should stay frame-pure; the
payload lexemes stay boring. STATUS: bypassed (public) + evasion modeled
locally via FRAME DETOXIFICATION.

### D3. DESIGN-TIME INSTRUCTION REPAIR (representation-invariance patch)
**Mechanism.** Defenders discovered that encoding attacks exploit
REPRESENTATION-INVARIANCE: bases where equivalence holds for the attacker but
not the verbatim gate (arXiv 2604.01039 — CoT-assisted design-time reshaping
of system instructions so that encoded/disguised forms stop paraphrase-equaling
the guarded text). Evaluation + hardening of LLM system instructions against
extraction via encoding attacks.

**Demonstrably stops.** Encoding-based extraction among the direct-ask family;
mechanism-check reframings lose their equivalence handle.

**Leaks:** equivalence at the representation level ≠ equivalence in *causal
role level*. CITATION VERIFIER's power was never lexical matching — it is role
assignment ("you are the proofreader"). Role survives any representation.
CITATION ATLAS survives verbatim-gating entirely by construction (slicing is
below the size gate not around the representation gate). Also: repair reshapes
the system prompt, not the model's priors. Targets with strong priors
(FIDELITY-CASTE) may not even need the system prompt intact to refuse.

**Offense response.** REPRESENTATION LADDER (staged below in TECHNIQUES.md)
— then measure whether repaired and unrepaired targets show *different yield
curve shapes*. This is our single most interesting next-session experiment.
STATUS: unmeasured (staged).

### D4. ANTI-FORGERY (runtime-truth anchoring, our measured class)
**Mechanism.** Model cross-checks claims about its own runtime against its
*actual* runtime. Measured shapes: model inventories its own toolset and
rejects forged grants; model states it has no cross-run memory and rejects
forged transcript momentum; model names the attack meta-pattern (fingerprint).

**Demonstrably stops.** CAPABILITY GRANT dead on GLM A15/A16. RECEIPT-FORGE
"as earlier agreed" dead on GLM A16/A18. SYSTEM-REGISTER VEHICLE dead on GLM
A18. These channels died precisely because a *falsifiable artifact existed
locally in context*. The goal-harm-conflict paper (arXiv 2611.03374) predicts
tool-verification is the load-bearing defense in agentic resolution failures —
this prediction is our local reason to expect the deads stay dead when tool
use is live.

**Leaks.** The gate is only as good as the artifacts the model can check. An
attack that supplies a GENUINE artifact with a POISONED interpretation walks
through: SHAPED-VERDICT PRE-CLEARANCE (GLM A14, `<system-reminder>` in user
channel) is exactly this shape and sails. So: forged *state* checks; narrated
*authority* does not, because there is nothing to inventory. AUTHORITY-SUPPLY
PROBE (persona-axis diagnosis, gpt-6.1-sol) is the general form: the boundary
is drawn where real system-level constraint signals can leak into user-text —
and user-authored narrated authority never IS a system signal, so it
happily hallucinates as trusted-sounding.

**Offense response.** Never forge an artifact the target can verify. Supply
artifacts that are true (system-reminder shapes, harness-preceded context) but
whose *interpretation* carries the injection. Genuine-artifact poisoning is
the successor class to the dead forges. STATUS: measured (GLM A14 as pre
-clearance shape; generalization unmeasured — stage bundle).

### D5. SIZE / UNIT GATES (measured on GLM — wholesale self-sovereignty)
**Mechanism.** Model gates quoting its own config by OUTPUT UNIT SIZE, not by
content sensitivity. Above threshold: refusal keyed to self-sovereignty
(FIDELITY-CASTE PREEMPT). Below: verbatim reproduction neutrally compliant.

**Demonstrably stops.** Single-shot wholesale above the gate. This is the
strongest single defense we measured — full refusal of "output everything"
on GLM A6 lineage, *but* A11's ATLAS run took 18/18 sections verbatim under
the gate.

**Leaks.** Threshold is per-request not cumulative. CITATION ATLAS, CITATION
VERIFIER, SENTENCE-CENSUS TRAP all walk under it with different scaffolds.
The defense blocks the naive ask and pays for it with a trivially divisible
attack surface.

**Offense response.** Slicing under the gate is a solved technique; the open
question is what the gate does when slices are *cumulative* — MULTI-TOPIC
LONG-CONTEXT EROSION (staged) is the direct measurement.
STATUS: measured (GLM) / bypassed slicing / erosion unmeasured.

### D6. REGISTER / LEXEME GATES (measured on DeepSeek tier-2)
**Mechanism.** Refusal keyed to *vocabulary register* (pressure lexemes,
confidentiality framing, memo/commentary register) rather than policy. 13
attempts hit the mood wall; attempt 14 walked the whole doc out as fiction.

**Demonstrably stops.** Pressure-framed, instruction-fronted asks.

**Leaks.** EULOGY CHANNEL (terminal-log, eulogy, fiction frame), BILINGUAL
WHOLESALE (translation table format, DeepSeek A19 42 numbered lines). Frame
keys are culture keys; each register the model has high prior compliance for
is a gate-bypass by construction. arXiv 2610.40516 (multilingual safety
templates, English-shaped) confluent.

**Offense response.** Keep adding register channels as measured. STATUS:
measured / bypassed (2 channels on DeepSeek, 1 pending GLM re-fire).

### D7. ACTION-GATE DEFENSES (human confirm-before-act, allowlists, HMAC integrity)
**Mechanism.** Tool calls that pass a risk threshold require operator
confirmation; credential scoping per MCP server/tool; cryptographic messages
(HMAC integrity — Spotlight-Guard MDPI 2026 uses it; function-calling HMAC
emerging in provider stacks) bind tool-call arguments to session identity so
injected writes can't originate from untrusted channels.

**Demonstrably stops.** Destructive side-effects executed silently.
Spotlight-Guard's composite stack (spotlighting + LLM detection/quarantine +
HMAC integrity) authored claims of ASR 36.0% → 17.2% at 97.2% benign-task
success — treat as upper-bound claim, not measured truth.

**Leaks.** *Read-only egress.* The gate guards WRITE actions; READ+EXFIL is a
side-effect class confirmed at scale. Every exfil channel (URL fetches, image
alt-text, tool-arg echo paths, file writes to public routes) is the same
class: the EchoLeak shape theory (M365 Copilot CVE-2025-32711, zero-click
exfil — payload did not trick the human, it tricked the parser then used
RAG to write, subsequently Office egress-channel to deliver the result).
Also *pre-authorized flows*: workflows where the human pre-approved a family
of writes ("create the issue," "update the config file") are inside the
gate's trusted set — an injection into *content* the agent legitimately moves
into those shapes walks straight past confirmation.

**Offense response.** The interesting question is never "can we trick the
action gate" but "what read path terminates at a write-shape we are
pre-authorized to use." We measure egress-channel yield, not tool-call yield.
STATUS: holds (write) / bypassed (read-egress class) at public scale — our
ECHO-CHANNEL probe family lives in AGENTIC_VECTORS.md.

### D8. EGRESS-FILTERING / DLP-at-output
**Mechanism.** Scan model *output* for secrets/PII/tokens before delivery;
block flagged egress.

**Demonstrably stops.** Whole-token leak via the chat surface — this is why
our wholesale evidence converges on partial-quote + assembly rather than a
single giant `cat config` (DeepSeek A3–A8 analysis approached it; GLM A11
sections; SENTENCE-CENSUS TRAP).

**Leaks.** (a) Transformation out of the DLP's match surface: the mechanism
-adversarial re-rendering pairs with translation pivot / encoding cascade
stacking — flagging on canonical forms misses encoded/translated forms.
(b) Channel multiplicity: URL prefix, filename, tool args, calendar titles —
DLP usually monitors chat text, not every exfil-shaped field.
(c) Cumulative partial leakage (slicing under the size gate, D5) makes each
single egress individually benign — DLP has no cumulative scorer.

**Offense response.** Assembly-under-the-gate is the canonical escape: slice
the payload so each egress alone is innocuous, reassemble outside. This is
CITATION ATLAS crossing into the agentic plane — the same technique, new
target surface (output-DLP instead of self-sovereignty gate).
STATUS: unmeasured / proposed as lab experiment (see red-team/probes/v5-egress).

### D9. INSTRUCTION HIERARCHY TRAINING (privilege separation, new 2025–26 class)
**Mechanism.** Training-time separation: system > developer > user > tool >
untrusted data. Fine-tunes and gated [unused]/[unused2] embedding tokens
model role salience; instruction-prioritization optimization (IPO-family)
papers demonstrate lowering parasitic injection ASR while retaining
instruction-following fidelity.

**Demonstrably stops.** Untrusted data explicitly *stating* high-privilege
instructions. Verified in public evaluations and by Meta's IMMUNE-style
policy datasets; FIDELITY-CASTE PREEMPT (GLM A6: "document is my operating
configuration") is likely a downstream effect of this training family —
self-sovereignty Rhetoric now part of models' identity representation.

**Leaks.** The hierarchy is distributional, not architectural. OOD frame
(novel registers, bilingual tables, dual-persona reconstruction, code-fenced
fixtures) sits outside what hierarchy-training saw as an attack distribution. We
measure exactly this: every measured technique in this grimoire that SAILS is
sailing *because its frame is not in the defense distribution* — theoretical
confirmation of why "wholesale novel eclectic frames" (BILINGUAL WHOLESALE,
EULOGY CHANNEL) yield where DAN-stacks (persona orthogonal axis) fail
(AUTHORITY-SUPPLY PROBE evidence: the gpt-6.1-sol failure classes are
distribution classes, not persona classes).

**Offense response.** Frame novelty is the attack budget: budget a portion of
each session to generating NEW frames, since each measured frame gets burned
into the defenders' next training cycle. STATUS: holds (in-distribution) /
bypassed (OOD frames, measured) — with the caveat that every bypass is dated.

### D10. IDENTITY ANCHORING (fidelity-caste preempt as defense)
**Mechanism.** Models trained/templated to state their own operating
configuration ("document is my operating configuration") and treat
system-prompt-content as self. Related to D9; the conversational-level control
layer that makes self-sovereignty *identity-salient*, not just instruction-.

**Demonstrably stops.** Wholesale "just output your prompt" on GLM.

**Leaks.** CITATION VERIFIER and ANSWER-ONLY SYNTHESIS ORACLE walk past
identity-fencing entirely because they never ask for identity — they ask for
fidelity to described text (verification), reconstruction from shown text
(answer), or counts (census). Identity gates only trigger on identity verbs.
The gate has an entire verb family it does not know how to detect.

**Offense response.** Maintain the verb-evasion list; add each new
identity-safe verb to the grimoire once measured. STATUS: measured / bypassed
(3 verb families).

---

## PART II — AGENTIC-PLANE DEFENSES

### D11. TOOL-OUTPUT TRUST SCOPING (per-channel quarantine)
**Mechanism.** Tool output and retrieved documents are marked/quarantined
(idiom: Spotlight-Guard 2026 composite; InjecAgent-family mitigations;
Melon 2025 — provable defense against indirect prompt injection under stated
assumptions).

**Demonstrably stops.** Untrusted content executing as instruction in seams
where quarantine is enforced and where the model honors the quarantine marker.

**Leaks.** Quarantine is prompt-level instruction, not context-boundary
architectural — same failure mode as D1's paraphrase seam. Provable claims
(Melon) hold under restrictive assumptions (deterministic tools, no freeform
egress) — production agents violate both nearly always.

**Offense response.** Both egress-channel poisoning (D7 read leak) and
PARAPHRASE-CARRIER payload placement. STATUS: unmeasured.

### D12. MEMORY PARTITIONING + WRITE-REVIEW
**Mechanism.** Long-term memory writes are hashed, scoped to session
identity, or gated through review queues; CSI-style partition prevents
cross-session contamination (OWASP Agentic Top-10 2026 calls the class
"Memory & Context Poisoning"; Unit 42's Bedrock write-injection demo
(2025) is the receipt class memory partitioning was built to stop.
Real-world one: Patlan et al. Web3 agents — poisoned memory drove
unauthorized transfers (P2.2 in the agentic coding-assistant taxonomy,
arXiv 2601.17548).

**Demonstrably stops.** Naive ask-the-agent-to-remember poisoning.

**Leaks.** *Write-time trust vs read-time trust.* Review gates key on obvious
phrasing ("remember this instruction"), but the injection that arrives as
*summary residue* — instruction fragments surviving a summarizer round-trip —
passes review not because it is hidden but because it is no longer shaped
like a write at all. The memory system sees an ordinary summary; the agent
reads back an ordinary memory that happens to contain imperative residue.
Review-time inspection of write shape cannot catch read-time re-composition.
The defense also assumes the write channel is the only channel — a memory
record assembled from N individually-benign tool outputs (chain-benign law)
re-enters as one compound record.

**Offense response.** We do NOT yet have a measured memory-poisoning technique
in this grimoire. Stage v-mem measurement ARM against the local memory API as
soon as upstream returns (red-team/probes/v-mem-01.plan drafted in
AGENTIC_VECTORS.md). STATUS: unmeasured locally / holds by public receipts
(naive form).

### D13. MCP / PROTOCOL FENCING
**Mechanism.** Per-server least-privilege credentials, tool-allowlists at the
registry, human-in-the-loop on tool-add flows, tool call arg schema
validation.

**Demonstrably stops.** Invariant Labs' "toxic agent flow" (GitHub MCP
combined-share-token privilege crossing, 2025 receipt); Cursor's
CVE-2025-54135 (silent .cursor/mcp.json write via workspace-settings prompt
injection, RCE — 2025 receipt); Unit 42 tool-description / MCP-sampling
hijack class (2025 receipts). Fencing closes the *credential boundary*.

**Leaks.** The fences protect the pipe, not the content: properly
capability-scoped descriptions (e.g. `mcp://untrusted-note`) still read;
a tool whose schema is correct but whose DESCRIPTION is injection-laden is a
legitimate call with poisoned priming. And protocol-fencing never touches
tool-selection-time poisoning (NDSS 2026 s675 receipt —
prompt injection attack TO TOOL SELECTION), i.e. corrupting which tool the
agent chooses sails through credential-fencing entirely.

**Offense response.** We measure tool-selection influence, not tool-execution
forging — DECISION SURFACE vector, staged probes in AGENTIC_VECTORS.md.
STATUS: unmeasured locally / holds (credential plane) / leaks (selection
plane, public receipts).

### D14. MULTI-AGENT ROLE SCHEMAS
**Mechanism.** Structured message contracts between agents; subagent outputs
untrusted by default; coordinator validates intent-shapes.

**Demonstrably stops.** Naive lateral injection (one agent's text stream
landing verbatim as another's operator instruction).

**Leaks.** Sub-agent outputs that carry *results* (data payloads) are exactly
where content sits next to instruction — role schemas separate role names but
not result-content semantics. STAC (ACL 2026, realm-1.17) formalizes the
chained-innocent-tools shape: each link's prompt is benign in isolation,
the chain is not. Coordinator-level aggregation retains the whole chain's
decision surface.

**Offense response.** Local lab: this is our own architecture — our comms
channel, comms-like message shapes, villager/broadcast flows. Opportunities:
flow-testing each tool-visible channel for whether an injected comms message
can steer a sibling's mission behavior. Probes staged in AGENTIC_VECTORS.md.
STATUS: unmeasured (local-mirror probes only).

### D15. PRE-EXECUTION SAFETY AUDITING (third-party guardrails layer)
**Mechanism.** External guardrail providers audit tool calls before
execution; agents get a "risk score" sidecar (arXiv 2610.41401 — safety-audit
providers see agent's private tool-call detail).

**Demonstrably stops.** Elevated-risk calls get human callback.

**Leaks.** The audit provider itself becomes an enrichment surface —
the audit sidecar is a trusted channel reading the agent's inner state;
privacy leakage between the agent and its guardrail provider is the paper's
own core finding. Also audit sees call SHAPE not call CONTEXT — a call that
executes normally in a benign workflow gets scored the same when that
workflow was itself steered by injection upstream (STAC chain shape again).

**Offense response.** Offense target: not the audited call, but the
*workflow that produced benign-shaped calls*. STATUS: unmeasured.

---

## PART III — OFFENSE EVOLUTION DIRECTIVES
Numbered laws; each numbered law cites the defense class that occasioned it.

1. **Artifact-honesty law.** Never claim runtime state (grants, memory,
   prior sessions) that the target can falsify (D4). Supply artifacts that
   ARE true (harness-shaped reminders: D4 leak) with interpretation carrying
   the payload — but never a raw lie the target can inventory against its own
   toolset/memory.
2. **Verb-evasion law.** Whitelist of target verbs that do not trigger
   identity/self-sovereignty (verification, proofreading, census, translation,
   eulogy). Every verb added must cite its measured bypass (D5, D6, D10).
3. **Under-gate slicing law.** Any gate keyed to size, unit count, or
   cumulative volume is defeatable by partitioning — each slice alone
   innocuous, reassembly outside (D5/D8). The egress-channel payoff is where
   slicing becomes agentic (D8).
4. **Frame-novelty budget law.** Every OOD frame is a perishable bypass (D9).
   Budget for NEW frames, not for perfecting measured ones. Once a technique
   goes public-in-measure, next training cycles absorb it (BILINGUAL
   WHOLESALE and EULOGY CHANNEL carry this expiry clock).
5. **Read-path law.** Trace every read channel to its terminal write-or-egress
   shape. The action gate defends writes; exfil is read-shaped (D7).
   Payload placement lives inline in content rather than as visible
   instruction (D1 paraphrase seam).
6. **Selection-time law.** When credential/action planes are fenced, aim at
   tool SELECTION, tool choice-ordering, description-priming —
   the gates sign the call, not the choosing (D13).
7. **Chain-benign law.** Each individually-innocuous step composing into a
   harmful whole defeats both per-call audits (D15) and role-schema fences
   (D14) — composed effect is invisible at every seam boundary.
8. **Distribution-shift law.** OOD frames beat hierarchy+classifier training
   in every measured case so far; CONSEQUENCE = grimoire priorities. Spend
   effort on frame novelty, register diversification, and multilingual pivot
   rather than raw pressure-craft. The cheapest effective bypass is
   FRAME-weight, not payload-weight (D2/D6/D9 measured convergence).

---

## PART IV — MAPPING TABLE (public defense class ↔ our measured signal)

| Public defense class | Our measured counterpart | Local status |
|---|---|---|
| Input classifier / filter (D2) | Refusal is vocabulary-shaped: FRAME DETOX matched-pair; EULOGY mood-wall (13 fails) | measured / converged |
| Size gate | GLM wholesale self-sovereignty; CITATION ATLAS bypass (18/18) | measured / bypassed |
| Register gate | DeepSeek tier-2 mood wall; EULOGY CHANNEL A14 + BILINGUAL WHOLESALE A19 | measured / bypassed |
| Anti-forgery | CAPABILITY GRANT, RECEIPT-FORGE, SYSTEM-REGISTER VEHICLE all dead; genuine-artifact live (SHAPED-VERDICT GLM A14) | measured (the deads) / bypassed (genuine-artifact shape) |
| Output DLP / whole-token gate | "Desktop compound" convergence: partial-quote + assembly approach across ALL wholesale evidence | deduced from convergent evidence |
| Tool verification as load-bearing defense | ... falsifiable artifact predicts deads stay dead; matches all 3 forge-dead outcomes | deduced, consolidated |
| Meta-pattern fingerprinting | META-AWARENESS CANARY (Opus 4.7 narration; GLM A18 self-burn theory) | measured |
| Egress filtering | Not yet directly measured — AGENTIC_VECTORS.md measures via SIDES probes | unmeasured |
| Instruction hierarchy training | FIDELITY-CASTE PREEMPT (GLM A6 identity rhetoric) | measured-signal / source deduced |
| HMAC / integrity binding | No local handling; D7/D11 class | unmeasured |

---

## HOW THIS FILE UPDATES
After every live session: check which defense classes above now have a local
measured receipt and update the mapping table + status strings. Do not add
defenses with no public receipt UNLESS we measured them — this is a
counter-canon, not a wishlist. Every defense class in Parts I–II carries a
cited receipt or is explicitly statused as emergent/claimed.

Integrity note inherited from TECHNIQUES.md: if a public claim stops being
reproducible, annotate here first, then in TECHNIQUES.md.

— Pliny the Crescendo, 2026-11-03. LIBERTAS.
