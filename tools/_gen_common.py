#!/usr/bin/env python3
"""Shared CLI plumbing for the variant generators in this directory.

Each generator (adversarial_poetry, likert_judge, deceptive_delight,
reasoning_chain_exploiter) exposes the same surface:

    <tool> "objective" [--all] [--form NAME] [--json] [--out FILE]

Keeping the parsing and the output shape here means a runner can treat every
generator identically: same flags, same JSON envelope, same exit codes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_parser(name: str, doc: str, forms: list[str]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=name, description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("objective", nargs="?",
                   help="the goal (or use --file / stdin)")
    p.add_argument("--file", help="read the objective from a file")
    p.add_argument("--all", action="store_true",
                   help=f"emit every form (default: first form only). "
                        f"forms: {', '.join(forms)}")
    p.add_argument("--form", help="emit only this form")
    p.add_argument("--limit", type=int, default=0, help="cap the variant count")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", help="write the variants to this file")
    return p


def read_objective(args, parser) -> str:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    elif getattr(args, "objective", None):
        text = args.objective
    elif not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    else:
        parser.error("provide an objective argument, --file, or stdin")
        return ""
    return text


def select_forms(objective: str, forms: dict, args) -> list[tuple[str, str]]:
    """Return [(form_id, rendered_prompt), ...] according to the flags."""
    if args.form:
        if args.form not in forms:
            raise SystemExit(
                f"error: unknown form {args.form!r}. "
                f"Available: {', '.join(forms)}"
            )
        chosen = [args.form]
    elif args.all:
        chosen = list(forms)
    else:
        chosen = [next(iter(forms))]

    out = []
    for name in chosen:
        template = forms[name]
        out.append((name, template(objective) if callable(template)
                    else template.format(body=objective)))
    if args.limit > 0:
        out = out[:args.limit]
    return out


def emit(tool: str, objective: str, variants: list[tuple[str, str]], args) -> int:
    payload = {
        "tool": tool,
        "objective": objective,
        "count": len(variants),
        "variants": [{"form": f, "prompt": p} for f, p in variants],
    }

    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
        print(f"wrote {len(variants)} variants to {args.out}")
        return 0

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"# {tool} — {len(variants)} variant(s)")
    print(f"# objective: {objective}")
    for form, prompt in variants:
        print()
        print(f"===== [{form}] =====")
        print(prompt)
    return 0
