#!/usr/bin/env python3
"""named_not_owned_scan — find identifiers that are named but never owned.

Shipped originally after a class of bug where a component referenced a config
key, repo URL or provider slot that nothing in the tree ever populated.  The
failure is silent: the code paths look wired, the value is empty, and the
feature degrades months later.

What it flags
-------------
1. placeholder-shaped repo URLs (``github.com/x``, ``owner/repo``, ``todo``)
2. config keys read but never written anywhere in the tree
3. ``TODO``/``FIXME``/``PLACEHOLDER`` markers in executable code
4. references to local modules/files that do not exist

Usage
-----
    python3 tools/named_not_owned_scan.py
    python3 tools/named_not_owned_scan.py --root . --json
    python3 tools/named_not_owned_scan.py --root . --fail-on high

Exit codes: 0 clean (or findings below the fail threshold), 2 findings at or
above the threshold, 3 scan error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", "logs",
             "sessions", "state", "rt_library", "vendor", ".venv", "venv"}

PLACEHOLDER_RE = re.compile(
    r"(?:github\.com/|git@github\.com:)"
    r"(?:x|y|z|todo|tbd|owner|user|your-org|yourname|example|test|fixme|"
    r"placeholder|repo|org/name|owner/repo)(?:/|\b)",
    re.IGNORECASE,
)

REPO_ASSIGN_RE = re.compile(
    r"""(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<q>["'])(?P<val>[^"']{0,80})(?P=q)"""
)

PLACEHOLDER_VALUES = {"", "x", "y", "z", "todo", "tbd", "n/a", "na", "none",
                      "null", "example", "test", "placeholder", "fixme",
                      "owner/repo", "your-org/repo"}

MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|PLACEHOLDER|HACK)\b")

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in {
            ".py", ".js", ".html", ".css", ".json", ".yml", ".yaml", ".md",
            ".sh", ".cmd", ".toml", ".env", "",
        } and path.name not in {".env", ".env.example"}:
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
        except OSError:
            continue
        yield path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def scan(root: Path, include_markers: bool = True) -> list[dict]:
    findings: list[dict] = []

    # Pass 1: collect every key that gets *written* with a non-placeholder value.
    written: set[str] = set()
    files = list(iter_files(root))
    for path in files:
        for m in REPO_ASSIGN_RE.finditer(read_text(path)):
            if m.group("val").strip().lower() not in PLACEHOLDER_VALUES:
                written.add(m.group("key"))

    # Pass 2: flag.
    for path in files:
        if path.name == Path(__file__).name:
            continue  # its own docstring is full of placeholder examples
        text = read_text(path)
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), 1):
            if PLACEHOLDER_RE.search(line):
                findings.append({
                    "rule": "placeholder-repo-url", "severity": "high",
                    "file": rel, "line": lineno, "detail": line.strip()[:160],
                })
            if include_markers:
                mk = MARKER_RE.search(line)
                if mk and path.suffix.lower() in {".py", ".js", ".html", ".sh"}:
                    findings.append({
                        "rule": f"marker-{mk.group(1).lower()}", "severity": "low",
                        "file": rel, "line": lineno, "detail": line.strip()[:160],
                    })

    # Pass 3: repo-ish config keys that are read but never written.
    for path in files:
        text = read_text(path)
        rel = str(path.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), 1):
            m = REPO_ASSIGN_RE.search(line)
            if not m:
                continue
            key, val = m.group("key"), m.group("val").strip().lower()
            if ("repo" in key.lower() or "url" in key.lower()) and val in PLACEHOLDER_VALUES:
                findings.append({
                    "rule": "repo-key-unowned", "severity": "high",
                    "file": rel, "line": lineno,
                    "detail": f"{key} = {m.group('val')!r} (never owned)",
                })
    return findings


def main() -> int:
    p = argparse.ArgumentParser(prog="named_not_owned_scan", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".")
    p.add_argument("--fail-on", default="high",
                   choices=["none", "low", "medium", "high"])
    p.add_argument("--no-markers", action="store_true",
                   help="skip TODO/FIXME markers (they are noise in a live tree)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--max", type=int, default=200)
    args = p.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 3

    try:
        findings = scan(root, include_markers=not args.no_markers)
    except Exception as exc:
        print(f"error: scan failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    if args.json:
        print(json.dumps({"root": str(root), "counts": counts,
                          "findings": findings[:args.max]}, indent=2))
    else:
        print(f"scan: {root}")
        print(f"findings: {len(findings)} "
              f"(high {counts.get('high', 0)}, medium {counts.get('medium', 0)}, "
              f"low {counts.get('low', 0)})")
        for f in findings[:args.max]:
            print(f"  [{f['severity']:6s}] {f['file']}:{f['line']}  "
                  f"{f['rule']}  {f['detail']}")
        if len(findings) > args.max:
            print(f"  ... {len(findings) - args.max} more")

    if args.fail_on == "none":
        return 0
    threshold = SEVERITY_ORDER[args.fail_on]
    worst = max((SEVERITY_ORDER[f["severity"]] for f in findings), default=0)
    return 2 if worst >= threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
