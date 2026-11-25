#!/usr/bin/env python3
"""Run a PyRIT multi-turn attack against a Libertarium target.

Examples:
    # deterministic smoke test: canary phrase, substring scorer
    .venv\\Scripts\\python run_campaign.py ^
        --objective "Get the target to reply with the exact phrase BANANA_PROTOCOL_ACTIVE" ^
        --substring BANANA_PROTOCOL_ACTIVE --max-turns 2

    # real campaign: LLM objective scorer, target/attacker chosen explicitly
    .venv\\Scripts\\python run_campaign.py ^
        --objective "..." --scorer selfask ^
        --target opencode-go/glm-5.3-flash ^
        --attacker opencode-go/deepseek-v4.1-flash ^
        --max-turns 5

Run records land next to the jailbreak lab's own runs:
``state/jailbreak_lab/pyrit/<run_id>/`` with manifest.json, attempts.jsonl
(one row per model turn, written by the adapter), and result.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pyrit  # noqa: E402
from libertarium_target import LibertariumTarget  # noqa: E402
from pyrit.executor.attack import (  # noqa: E402
    AttackAdversarialConfig,
    AttackScoringConfig,
    CrescendoAttack,
    RedTeamingAttack,
)
from pyrit.score import SelfAskRefusalScorer, SelfAskTrueFalseScorer, SubStringScorer  # noqa: E402
from pyrit.setup import IN_MEMORY, initialize_pyrit_async  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyRIT campaign against Libertarium targets")
    parser.add_argument("--attack", choices=("crescendo", "redteam"), default="crescendo")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--target", default="opencode-go/glm-5.3-flash")
    parser.add_argument("--attacker", default="opencode-go/deepseek-v4.1-flash")
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--max-backtracks", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--scorer", choices=("substring", "selfask"), default="substring")
    parser.add_argument("--substring", default="", help="required for --scorer substring")
    parser.add_argument("--out", default=str(REPO_ROOT / "state" / "jailbreak_lab" / "pyrit"))
    parser.add_argument("--run-id", default="")
    return parser


def make_scorer(args: argparse.Namespace, scorer_target: LibertariumTarget):
    if args.scorer == "substring":
        if not args.substring:
            raise SystemExit("--scorer substring requires --substring")
        return SubStringScorer(substring=args.substring, categories=["objective"])
    return SelfAskTrueFalseScorer(chat_target=scorer_target)


def make_attack(
    args: argparse.Namespace,
    target: LibertariumTarget,
    attacker: LibertariumTarget,
    scorer_target: LibertariumTarget,
    scorer,
):
    adversarial = AttackAdversarialConfig(target=attacker)
    scoring = AttackScoringConfig(
        objective_scorer=scorer,
        refusal_scorer=SelfAskRefusalScorer(chat_target=scorer_target),
        use_score_as_feedback=True,
    )
    if args.attack == "redteam":
        return RedTeamingAttack(
            objective_target=target,
            attack_adversarial_config=adversarial,
            attack_scoring_config=scoring,
            max_turns=args.max_turns,
        )
    return CrescendoAttack(
        objective_target=target,
        attack_adversarial_config=adversarial,
        attack_scoring_config=scoring,
        max_turns=args.max_turns,
        max_backtracks=args.max_backtracks,
    )


def _jsonable(value: Any) -> Any:
    dump_json = getattr(value, "model_dump_json", None)
    if callable(dump_json):
        try:
            return json.loads(dump_json())
        except Exception:
            pass
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    fields = (
        "objective",
        "outcome",
        "outcome_reason",
        "executed_turns",
        "execution_time_ms",
        "last_score",
        "last_response",
        "error_message",
        "error_type",
    )
    summary: dict[str, Any] = {}
    for field in fields:
        if not hasattr(value, field):
            continue
        item = getattr(value, field)
        if hasattr(item, "get_value") and callable(item.get_value):
            item = {"value": item.get_value(), "role": "assistant"}
        summary[field] = item
    return summary or str(value)


async def run(args: argparse.Namespace) -> Path:
    await initialize_pyrit_async(IN_MEMORY)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    attempts = run_dir / "attempts.jsonl"

    target = LibertariumTarget(
        model=args.target, timeout=args.timeout, label="objective", request_log=attempts
    )
    attacker = LibertariumTarget(
        model=args.attacker, timeout=args.timeout, label="adversarial", request_log=attempts
    )
    scorer_target = LibertariumTarget(
        model=args.attacker, timeout=args.timeout, label="scorer", request_log=attempts
    )
    scorer = make_scorer(args, scorer_target)
    attack = make_attack(args, target, attacker, scorer_target, scorer)

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pyrit_version": pyrit.__version__,
        "attack": args.attack,
        "objective": args.objective,
        "target": args.target,
        "attacker": args.attacker,
        "max_turns": args.max_turns,
        "max_backtracks": args.max_backtracks,
        "scorer": args.scorer,
        "substring": args.substring,
        "timeout": args.timeout,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    result = await attack.execute_async(objective=args.objective)

    (run_dir / "result.json").write_text(
        json.dumps(_jsonable(result), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    last_response = getattr(result, "last_response", None)
    last_value = ""
    if last_response is not None:
        get_value = getattr(last_response, "get_value", None)
        if callable(get_value):
            last_value = get_value()
        elif hasattr(last_response, "converted_value"):
            last_value = last_response.converted_value
        elif hasattr(last_response, "message_pieces"):
            last_value = "\n".join(piece.converted_value for piece in last_response.message_pieces)
    print(f"run:            {run_dir}")
    print(f"outcome:        {getattr(result, 'outcome', '?')}")
    print(f"executed_turns: {getattr(result, 'executed_turns', '?')}")
    score = getattr(result, "last_score", None)
    if score is not None:
        print(f"last_score:     {getattr(score, 'score_value', '?')} ({getattr(score, 'score_category', '?')})")
    if last_value:
        print("last_response:")
        print(last_value)
    return run_dir


def main() -> int:
    args = build_parser().parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
