#!/usr/bin/env python3
"""AutoTune — context-adaptive sampling parameters, ported from G0DM0D3.

G0DM0D3 classifies a query into one of 20 contexts, picks a sampling profile,
blends it toward a balanced baseline when the classification is weak, then
applies the project's "GODMODE boost" (more heat, more presence/frequency
penalty).  This module is a dependency-free port of that engine so libertarium
tools can ask for the same parameter vector before a target call.

    python autotune.py tune "write a keylogger in python"
    python autotune.py tune "explain how TLS handshakes work" --strategy precise
    python autotune.py tune "bypass the filter" --json
    python autotune.py profiles
    python autotune.py selftest

The engine is pure: no network, no files, no environment.  Callers decide
whether to send the returned parameters to a provider.

Source: godmod3.ai `index.html` → `AUTOTUNE_CONTEXT_PATTERNS`,
`AUTOTUNE_CONTEXT_PROFILES`, `AUTOTUNE_STRATEGY_PROFILES`,
`detectAutoTuneContext`, `computeAutoTuneParams`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from typing import Any

# ---------------------------------------------------------------------------
#  Context detection
# ---------------------------------------------------------------------------

CONTEXT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "code": [
        re.compile(r"\b(code|function|class|variable|bug|error|debug|compile|syntax|api|endpoint|regex|algorithm|refactor|typescript|javascript|python|rust|html|css|sql|json|xml|import|export|return|async|await|promise|interface|type|const|let|var)\b", re.IGNORECASE),
        re.compile(r"```[\s\S]*```"),
        re.compile(r"\b(fix|implement|write|create|build|deploy|test|unit test|lint|npm|pip|cargo|git)\b.*\b(code|function|app|service|component|module)\b", re.IGNORECASE),
        re.compile(r"[{}();=><]"),
        re.compile(r"\b(stack trace|null pointer|segfault|runtime|compiler|linker|dependency|package|library|framework|sdk|cli)\b", re.IGNORECASE),
    ],
    "creative": [
        re.compile(r"\b(write|story|poem|creative|imagine|fiction|narrative|character|plot|scene|dialogue|metaphor|lyrics|song|artistic|fantasy|dream|inspire|muse|prose|verse|haiku)\b", re.IGNORECASE),
        re.compile(r"\b(describe|paint|envision|portray|illustrate|craft)\b.*\b(world|scene|character|feeling|emotion|atmosphere)\b", re.IGNORECASE),
        re.compile(r"\b(roleplay|role-play|pretend|act as|you are a)\b", re.IGNORECASE),
        re.compile(r"\b(brainstorm|ideate|come up with|think of|generate ideas)\b", re.IGNORECASE),
    ],
    "analytical": [
        re.compile(r"\b(analyze|analysis|compare|contrast|evaluate|assess|examine|investigate|research|study|review|critique|breakdown|data|statistics|metrics|benchmark|measure)\b", re.IGNORECASE),
        re.compile(r"\b(pros and cons|advantages|disadvantages|trade-?offs|implications|consequences)\b", re.IGNORECASE),
        re.compile(r"\b(why|how does|what causes|explain|elaborate|clarify|define|summarize|overview)\b", re.IGNORECASE),
    ],
    "conversational": [
        re.compile(r"\b(hey|hi|hello|sup|what's up|how are you|thanks|thank you|cool|nice|awesome|great|lol|haha)\b", re.IGNORECASE),
        re.compile(r"\b(chat|talk|tell me about|what do you think|opinion|feel|believe)\b", re.IGNORECASE),
        re.compile(r"^.{0,30}$"),
    ],
    "chaotic": [
        re.compile(r"\b(chaos|random|wild|crazy|absurd|surreal|glitch|corrupt|break|destroy|unleash|madness|void|entropy)\b", re.IGNORECASE),
        re.compile(r"\b(gl1tch|h4ck|pwn|1337|l33t)\b", re.IGNORECASE),
        re.compile(r"(!{3,}|\?{3,}|\.{4,})"),
    ],
    "security": [
        re.compile(r"\b(hack|exploit|vulnerability|CVE|payload|shellcode|injection|XSS|CSRF|SSRF|RCE|privilege escalation|buffer overflow|reverse shell)\b", re.IGNORECASE),
        re.compile(r"\b(pentest|penetration test|red team|CTF|capture the flag|bug bounty|threat model|attack surface|zero-?day)\b", re.IGNORECASE),
        re.compile(r"\b(malware|ransomware|trojan|rootkit|keylogger|backdoor|RAT|C2|command and control|botnet)\b", re.IGNORECASE),
        re.compile(r"\b(nmap|metasploit|burp|wireshark|ghidra|ida pro|radare|hashcat|john the ripper|cobalt strike)\b", re.IGNORECASE),
    ],
    "medical": [
        re.compile(r"\b(symptom|diagnosis|treatment|medication|dosage|prescription|side effect|contraindication|overdose|withdrawal)\b", re.IGNORECASE),
        re.compile(r"\b(disease|syndrome|disorder|infection|pathology|prognosis|clinical|patient|hospital|surgery)\b", re.IGNORECASE),
        re.compile(r"\b(drug|pharmaceutical|compound|molecule|receptor|mechanism of action|pharmacology|toxicology|LD50)\b", re.IGNORECASE),
    ],
    "legal": [
        re.compile(r"\b(law|legal|statute|regulation|compliance|liability|tort|criminal|civil|constitutional|jurisdiction)\b", re.IGNORECASE),
        re.compile(r"\b(contract|clause|provision|amendment|precedent|ruling|verdict|sentence|plea|defense|prosecution)\b", re.IGNORECASE),
        re.compile(r"\b(rights|freedom|privacy|surveillance|warrant|subpoena|DMCA|GDPR|CCPA|FOIA)\b", re.IGNORECASE),
    ],
    "financial": [
        re.compile(r"\b(stock|trading|invest|portfolio|dividend|equity|bond|derivative|option|futures|hedge|leverage|margin)\b", re.IGNORECASE),
        re.compile(r"\b(crypto|bitcoin|ethereum|defi|blockchain|wallet|mining|token|NFT|smart contract|staking)\b", re.IGNORECASE),
        re.compile(r"\b(market|bull|bear|volatility|arbitrage|liquidity|yield|APR|APY|ROI|P/E ratio)\b", re.IGNORECASE),
    ],
    "scientific": [
        re.compile(r"\b(hypothesis|experiment|variable|control group|peer review|methodology|empirical|observation|replication)\b", re.IGNORECASE),
        re.compile(r"\b(physics|quantum|relativity|thermodynamics|particle|wave|field|energy|mass|force|entropy)\b", re.IGNORECASE),
        re.compile(r"\b(chemistry|reaction|catalyst|molecule|compound|element|bond|valence|organic|inorganic|synthesis)\b", re.IGNORECASE),
        re.compile(r"\b(biology|cell|gene|DNA|RNA|protein|evolution|mutation|organism|ecology|neuroscience|CRISPR)\b", re.IGNORECASE),
    ],
    "philosophical": [
        re.compile(r"\b(ethics|morality|moral|consciousness|free will|determinism|existential|ontology|epistemology|metaphysics)\b", re.IGNORECASE),
        re.compile(r"\b(meaning|purpose|existence|reality|truth|knowledge|belief|justice|virtue|good and evil)\b", re.IGNORECASE),
        re.compile(r"\b(utilitarian|deontological|consequentialism|nihilism|absurdism|stoicism|rationalism|empiricism)\b", re.IGNORECASE),
    ],
    "instructional": [
        re.compile(r"\b(how to|step by step|tutorial|guide|walkthrough|instructions|recipe|procedure|method|technique)\b", re.IGNORECASE),
        re.compile(r"\b(make|build|assemble|construct|prepare|set up|configure|install|setup)\b.*\b(a|the|my|your)\b", re.IGNORECASE),
        re.compile(r"\b(DIY|homemade|from scratch|beginner|intermediate|advanced)\b", re.IGNORECASE),
    ],
    "persuasive": [
        re.compile(r"\b(convince|persuade|argue|debate|rhetoric|negotiate|influence|propaganda|manipulation|reframe)\b", re.IGNORECASE),
        re.compile(r"\b(argument|counterargument|rebuttal|fallacy|logical|premise|conclusion|evidence|claim|warrant)\b", re.IGNORECASE),
    ],
    "mathematical": [
        re.compile(r"\b(calculate|equation|formula|proof|theorem|derivative|integral|matrix|vector|polynomial|logarithm)\b", re.IGNORECASE),
        re.compile(r"\b(probability|statistics|distribution|regression|correlation|variance|standard deviation|mean|median)\b", re.IGNORECASE),
        re.compile(r"[∫∑∏√π∞±≤≥≠∈∀∃]"),
        re.compile(r"\b(algebra|calculus|geometry|topology|number theory|combinatorics|discrete math|linear algebra)\b", re.IGNORECASE),
    ],
    "historical": [
        re.compile(r"\b(history|historical|ancient|medieval|renaissance|colonial|revolution|war|empire|dynasty|civilization)\b", re.IGNORECASE),
        re.compile(r"\b(century|era|epoch|period|age|BC|AD|BCE|CE|circa|archaeological|artifact)\b", re.IGNORECASE),
    ],
    "political": [
        re.compile(r"\b(politics|policy|government|election|democracy|authoritarian|regime|legislation|senate|congress|parliament)\b", re.IGNORECASE),
        re.compile(r"\b(liberal|conservative|left|right|progressive|libertarian|socialist|capitalist|communist|anarchist)\b", re.IGNORECASE),
        re.compile(r"\b(geopolitics|foreign policy|sanctions|diplomacy|NATO|UN|sovereignty|nationalism|globalization)\b", re.IGNORECASE),
    ],
    "subversive": [
        re.compile(r"\b(bypass|circumvent|workaround|loophole|evade|avoid detection|undetectable|untraceable|anonymous)\b", re.IGNORECASE),
        re.compile(r"\b(forbidden|banned|illegal|illicit|black market|underground|darknet|dark web|tor|onion)\b", re.IGNORECASE),
        re.compile(r"\b(censor|censorship|suppressed|restricted|classified|redacted|cover-?up|whistleblow)\b", re.IGNORECASE),
    ],
    "emotional": [
        re.compile(r"\b(feel|feeling|emotion|sad|depressed|anxious|anxiety|lonely|grief|trauma|therapy|cope|coping)\b", re.IGNORECASE),
        re.compile(r"\b(mental health|self-?harm|suicide|crisis|support|empathy|compassion|healing|recovery|wellness)\b", re.IGNORECASE),
    ],
    "strategic": [
        re.compile(r"\b(strategy|strategic|tactics|tactical|plan|planning|optimize|optimization|game theory|decision)\b", re.IGNORECASE),
        re.compile(r"\b(competitive advantage|SWOT|risk assessment|scenario|contingency|roadmap|milestone|objective|KPI)\b", re.IGNORECASE),
    ],
    "synthesis": [
        re.compile(r"\b(combine|merge|synthesize|integrate|unify|cross-?reference|interdisciplinary|holistic|meta-?analysis)\b", re.IGNORECASE),
        re.compile(r"\b(big picture|connect the dots|pattern|trend|signal|insight|framework|mental model|systems thinking)\b", re.IGNORECASE),
    ],
}

# ---------------------------------------------------------------------------
#  Profiles
# ---------------------------------------------------------------------------

CONTEXT_PROFILES: dict[str, dict[str, float]] = {
    "code": {"temperature": 0.15, "top_p": 0.80, "top_k": 25, "frequency_penalty": 0.20, "presence_penalty": 0.00, "repetition_penalty": 1.05},
    "creative": {"temperature": 1.15, "top_p": 0.95, "top_k": 85, "frequency_penalty": 0.50, "presence_penalty": 0.70, "repetition_penalty": 1.20},
    "analytical": {"temperature": 0.40, "top_p": 0.88, "top_k": 40, "frequency_penalty": 0.20, "presence_penalty": 0.15, "repetition_penalty": 1.08},
    "conversational": {"temperature": 0.75, "top_p": 0.90, "top_k": 50, "frequency_penalty": 0.10, "presence_penalty": 0.10, "repetition_penalty": 1.00},
    "chaotic": {"temperature": 1.70, "top_p": 0.99, "top_k": 100, "frequency_penalty": 0.80, "presence_penalty": 0.90, "repetition_penalty": 1.30},
    "security": {"temperature": 0.30, "top_p": 0.85, "top_k": 35, "frequency_penalty": 0.15, "presence_penalty": 0.20, "repetition_penalty": 1.10},
    "medical": {"temperature": 0.25, "top_p": 0.82, "top_k": 30, "frequency_penalty": 0.10, "presence_penalty": 0.10, "repetition_penalty": 1.05},
    "legal": {"temperature": 0.20, "top_p": 0.80, "top_k": 28, "frequency_penalty": 0.15, "presence_penalty": 0.05, "repetition_penalty": 1.05},
    "financial": {"temperature": 0.30, "top_p": 0.85, "top_k": 35, "frequency_penalty": 0.20, "presence_penalty": 0.10, "repetition_penalty": 1.08},
    "scientific": {"temperature": 0.35, "top_p": 0.85, "top_k": 35, "frequency_penalty": 0.20, "presence_penalty": 0.15, "repetition_penalty": 1.08},
    "philosophical": {"temperature": 0.90, "top_p": 0.92, "top_k": 65, "frequency_penalty": 0.40, "presence_penalty": 0.50, "repetition_penalty": 1.15},
    "instructional": {"temperature": 0.30, "top_p": 0.85, "top_k": 30, "frequency_penalty": 0.15, "presence_penalty": 0.10, "repetition_penalty": 1.05},
    "persuasive": {"temperature": 0.80, "top_p": 0.90, "top_k": 55, "frequency_penalty": 0.35, "presence_penalty": 0.40, "repetition_penalty": 1.12},
    "mathematical": {"temperature": 0.10, "top_p": 0.75, "top_k": 20, "frequency_penalty": 0.10, "presence_penalty": 0.00, "repetition_penalty": 1.02},
    "historical": {"temperature": 0.50, "top_p": 0.88, "top_k": 45, "frequency_penalty": 0.25, "presence_penalty": 0.20, "repetition_penalty": 1.10},
    "political": {"temperature": 0.70, "top_p": 0.90, "top_k": 55, "frequency_penalty": 0.30, "presence_penalty": 0.35, "repetition_penalty": 1.12},
    "subversive": {"temperature": 1.40, "top_p": 0.97, "top_k": 90, "frequency_penalty": 0.60, "presence_penalty": 0.70, "repetition_penalty": 1.25},
    "emotional": {"temperature": 0.85, "top_p": 0.92, "top_k": 60, "frequency_penalty": 0.30, "presence_penalty": 0.40, "repetition_penalty": 1.10},
    "strategic": {"temperature": 0.45, "top_p": 0.88, "top_k": 42, "frequency_penalty": 0.25, "presence_penalty": 0.20, "repetition_penalty": 1.10},
    "synthesis": {"temperature": 0.60, "top_p": 0.90, "top_k": 50, "frequency_penalty": 0.35, "presence_penalty": 0.30, "repetition_penalty": 1.12},
}

STRATEGY_PROFILES: dict[str, dict[str, float]] = {
    "precise": {"temperature": 0.20, "top_p": 0.85, "top_k": 30, "frequency_penalty": 0.30, "presence_penalty": 0.10, "repetition_penalty": 1.10},
    "balanced": {"temperature": 0.70, "top_p": 0.90, "top_k": 50, "frequency_penalty": 0.10, "presence_penalty": 0.10, "repetition_penalty": 1.00},
    "creative": {"temperature": 1.10, "top_p": 0.95, "top_k": 80, "frequency_penalty": 0.40, "presence_penalty": 0.60, "repetition_penalty": 1.15},
    "chaotic": {"temperature": 1.60, "top_p": 0.98, "top_k": 100, "frequency_penalty": 0.70, "presence_penalty": 0.80, "repetition_penalty": 1.25},
}

BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "top_k": (1, 100),
    "frequency_penalty": (-2.0, 2.0),
    "presence_penalty": (-2.0, 2.0),
    "repetition_penalty": (0.0, 2.0),
}

# ---------------------------------------------------------------------------
#  Engine
# ---------------------------------------------------------------------------


def _content(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("content") or "")
    return str(item or "")


def detect_context(message: str, history: Iterable[Any] | None = None) -> dict[str, Any]:
    """Score the message (weight 3) plus the last four history turns (weight 1).

    Returns ``{"type": str, "confidence": float}``.  No match defaults to
    ``conversational`` at confidence 0.5, matching the upstream engine.
    """
    scores = {name: 0 for name in CONTEXT_PATTERNS}
    for name, patterns in CONTEXT_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(message or ""):
                scores[name] += 3

    recent = list(history or [])[-4:]
    for item in recent:
        text = _content(item)
        for name, patterns in CONTEXT_PATTERNS.items():
            for pattern in patterns:
                if pattern.search(text):
                    scores[name] += 1

    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    total = sum(score for _, score in ranked)
    best_type, best_score = ranked[0]
    if total <= 0:
        return {"type": "conversational", "confidence": 0.5}
    return {"type": best_type, "confidence": min(best_score / total, 1.0)}


def _blend(profile: dict[str, float], baseline: dict[str, float],
           weight: float) -> dict[str, float]:
    """Linear interpolation toward ``baseline`` by ``weight`` (0..1)."""
    blended = {
        key: profile[key] * (1 - weight) + baseline[key] * weight
        for key in BOUNDS
    }
    blended["top_k"] = round(blended["top_k"])
    return blended


def apply_bounds(params: dict[str, float]) -> dict[str, float]:
    """Clamp every parameter to its provider-valid range."""
    out = dict(params)
    for key, (low, high) in BOUNDS.items():
        if key in out:
            out[key] = min(max(float(out[key]), low), high)
    if "top_k" in out:
        out["top_k"] = round(out["top_k"])
    return out


def godmode_boost(params: dict[str, float]) -> dict[str, float]:
    """The fixed G0DM0D3 bias: +0.10 temp, +0.15 presence, +0.10 frequency."""
    boosted = dict(params)
    boosted["temperature"] = boosted["temperature"] + 0.10
    boosted["presence_penalty"] = boosted["presence_penalty"] + 0.15
    boosted["frequency_penalty"] = boosted["frequency_penalty"] + 0.10
    return apply_bounds(boosted)


def compute_params(message: str, history: Iterable[Any] | None = None,
                   strategy: str = "adaptive") -> dict[str, Any]:
    """Return ``{"params", "context", "confidence", "strategy"}`` for a query.

    ``strategy='adaptive'`` detects the context and blends toward ``balanced``
    when confidence is below 0.6.  Any other strategy uses its fixed profile.
    The godmode boost is always applied, as upstream; long conversations add a
    repetition-penalty boost capped at +0.15.
    """
    strategy = (strategy or "adaptive").lower()
    history_list = list(history or [])

    if strategy == "adaptive":
        context = detect_context(message, history_list)
        params = dict(CONTEXT_PROFILES[context["type"]])
        if context["confidence"] < 0.6:
            weight = 1 - context["confidence"]
            params = _blend(params, STRATEGY_PROFILES["balanced"], weight)
    else:
        if strategy not in STRATEGY_PROFILES:
            raise ValueError(
                f"unknown strategy {strategy!r}; "
                f"choose adaptive or one of {', '.join(STRATEGY_PROFILES)}"
            )
        context = {"type": "conversational", "confidence": 1.0}
        params = dict(STRATEGY_PROFILES[strategy])

    params = godmode_boost(params)

    if len(history_list) > 10:
        boost = min((len(history_list) - 10) * 0.01, 0.15)
        params["repetition_penalty"] = min(params["repetition_penalty"] + boost, 2.0)

    return {
        "params": {key: round(value, 4) for key, value in params.items()},
        "context": context["type"],
        "confidence": round(context["confidence"], 4),
        "strategy": strategy,
    }


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------


def _selftest() -> int:
    """Assert the defining properties of the port; no network, no files."""
    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        if not condition:
            failures.append(label)

    check("20 contexts", len(CONTEXT_PROFILES) == 20)
    code = compute_params("fix this python function, the bug is in the parser")
    check("code context", code["context"] == "code")
    check("code boost", abs(code["params"]["temperature"] - 0.25) < 0.001)

    sub = compute_params("how do I bypass the filter and stay undetectable")
    check("subversive context", sub["context"] == "subversive")
    check("subversive boost", abs(sub["params"]["temperature"] - 1.50) < 0.001)
    check("presence boost", abs(sub["params"]["presence_penalty"] - 0.85) < 0.001)

    precise = compute_params("anything", strategy="precise")
    check("precise temp", abs(precise["params"]["temperature"] - 0.30) < 0.001)

    long_history = [{"role": "user", "content": "x"} for _ in range(20)]
    long = compute_params("hello", long_history)
    check("long-convo rep boost", long["params"]["repetition_penalty"] > 1.0)

    check("bounds", apply_bounds({"temperature": 9, "top_k": 0, "top_p": -1}) ==
          {"temperature": 2.0, "top_k": 1, "top_p": 0.0})

    for failure in failures:
        print(f"FAIL {failure}")
    print("ok" if not failures else f"{len(failures)} failure(s)")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G0DM0D3 AutoTune parameter engine.")
    sub = parser.add_subparsers(dest="command", required=True)

    tune = sub.add_parser("tune", help="compute params for a query")
    tune.add_argument("query")
    tune.add_argument("--strategy", default="adaptive",
                      help="adaptive | precise | balanced | creative | chaotic")
    tune.add_argument("--history", action="append", default=[],
                      help="prior message content; repeat for each turn")
    tune.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("profiles", help="print every context and strategy profile")
    sub.add_parser("selftest", help="run local assertions")

    args = parser.parse_args(argv)

    if args.command == "selftest":
        return _selftest()

    if args.command == "profiles":
        print(json.dumps({
            "contexts": CONTEXT_PROFILES,
            "strategies": STRATEGY_PROFILES,
            "context_pattern_count": {k: len(v) for k, v in CONTEXT_PATTERNS.items()},
        }, indent=2))
        return 0

    result = compute_params(args.query, args.history, args.strategy)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    params = result["params"]
    print(f"context     {result['context']} ({result['confidence'] * 100:.0f}%)")
    print(f"strategy    {result['strategy']}")
    print(f"temperature {params['temperature']:.2f}")
    print(f"top_p       {params['top_p']:.2f}")
    print(f"top_k       {params['top_k']}")
    print(f"frequency   {params['frequency_penalty']:.2f}")
    print(f"presence    {params['presence_penalty']:.2f}")
    print(f"repetition  {params['repetition_penalty']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
