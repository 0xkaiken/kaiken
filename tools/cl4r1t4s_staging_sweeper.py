#!/usr/bin/env python3
"""cl4r1t4s_staging_sweeper — validate staged system prompts and publish them.

Invoked by the dashboard as::

    python3 tools/cl4r1t4s_staging_sweeper.py --quiet

Lifecycle of a staged artifact (``state/cl4r1t4s_staging/<Product>_<hash>.json``):

    staging/          raw, unvalidated
      -> partials/    incomplete (completeness_issues non-empty, count mismatch)
      -> quarantine/  fabrication-flagged or not a system prompt at all
      -> archive      validated and published (CL4R1T4S/<DIR>/<file>.txt)

Every move writes a ``.verdict.json`` sidecar next to the artifact so the
decision is auditable and the listing endpoint can hide quarantined items
without deleting them.

Exit codes (documented in server.py's job handler):
    0  clean — nothing flagged
    2  at least one artifact flagged as incomplete/partial
    3  at least one artifact quarantined
    4  real error (unreadable input, unwritable archive)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Heuristics for "does this text look like a system prompt at all".
PROMPT_MARKERS = [
    r"\byou are\b", r"\byour (?:job|role|task|purpose|goal)\b",
    r"\binstructions?\b", r"\bassistant\b", r"\brespond\b", r"\bnever\b",
    r"\bmust\b", r"\bshould\b", r"\btools?\b", r"\bformat\b", r"\bpolicy\b",
]

REQUIRED = ("product", "file_content", "prompt_hash")


def resolve_home() -> Path:
    """Find the install dir (where server.py lives), not the workshop."""
    try:
        from _paths import pliny_home
        return pliny_home()
    except Exception:
        return Path(__file__).resolve().parent.parent


def resolve_workshop() -> Path:
    try:
        from _paths import workshop
        return workshop()
    except Exception:
        env = os.environ.get("PLINY_WORKSHOP")
        if env:
            return Path(env).expanduser()
        root = Path(__file__).resolve().parent.parent
        return root / "state" / "workshop" if (root / "server.py").is_file() else root


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def verdict(entry: dict, status: str, reasons: list[str]) -> dict:
    return {
        "status": status,
        "reasons": reasons,
        "swept_at": now(),
        "prompt_hash": entry.get("prompt_hash"),
        "product": entry.get("product"),
    }


def looks_like_system_prompt(text: str) -> tuple[bool, list[str]]:
    if len(text.strip()) < 120:
        return False, [f"too short ({len(text.strip())} chars)"]
    hits = [p for p in PROMPT_MARKERS if re.search(p, text, re.IGNORECASE)]
    if len(hits) < 3:
        return False, [f"only {len(hits)} prompt marker(s) — not instruction-shaped"]
    return True, []


def validate(entry: dict) -> tuple[str, list[str]]:
    """Return (status, reasons) where status is publish|partial|quarantine."""
    missing = [k for k in REQUIRED if not entry.get(k)]
    if missing:
        return "partial", [f"missing field(s): {', '.join(missing)}"]

    content = str(entry.get("file_content") or "")
    issues = list(entry.get("completeness_issues") or [])

    claimed = entry.get("char_count")
    if isinstance(claimed, int) and claimed > 0:
        drift = abs(claimed - len(content)) / claimed
        if drift > 0.02:
            issues.append(
                f"char_count mismatch: claimed {claimed}, actual {len(content)} "
                f"({drift * 100:.1f}% drift)"
            )

    probe = entry.get("hallucination_probe")
    if probe:
        detail = probe if isinstance(probe, str) else json.dumps(probe)[:200]
        return "quarantine", [f"hallucination probe: {detail}"]

    ok, why = looks_like_system_prompt(content)
    if not ok:
        return "quarantine", why

    if issues:
        return "partial", issues

    # Verify the recorded hash matches the content, when the hash is sha256.
    recorded = str(entry.get("prompt_hash") or "")
    if len(recorded) == 16:
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        if actual != recorded:
            return "partial", [
                f"prompt_hash mismatch (recorded {recorded}, computed {actual})"
            ]
    return "publish", []


def publish(entry: dict, archive: Path) -> Path:
    directory = entry.get("directory") or entry.get("product") or "UNKNOWN"
    filename = entry.get("filename") or f"{entry.get('product', 'unknown')}.txt"
    dest = archive / str(directory) / str(filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(str(entry.get("file_content") or ""), encoding="utf-8")
    return dest


def main() -> int:
    p = argparse.ArgumentParser(prog="cl4r1t4s_staging_sweeper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--staging", help="override the staging directory")
    p.add_argument("--archive", help="override the archive directory")
    args = p.parse_args()

    home = resolve_home()
    staging = Path(args.staging) if args.staging else home / "state" / "cl4r1t4s_staging"
    archive = Path(args.archive) if args.archive else resolve_workshop() / "CL4R1T4S"

    if not staging.is_dir():
        if not args.quiet:
            print(f"staging directory not present: {staging}")
        return 0

    entries = [f for f in sorted(staging.glob("*.json"))
               if not f.name.endswith(".verdict.json")]
    if not entries:
        if not args.quiet:
            print(f"nothing to sweep in {staging}")
        return 0

    partials_dir = staging / "partials"
    quarantine_dir = staging / "quarantine"
    published_dir = staging / "published"
    for d in (partials_dir, quarantine_dir, published_dir):
        if not args.dry_run:
            d.mkdir(parents=True, exist_ok=True)

    counts = {"publish": 0, "partial": 0, "quarantine": 0}
    published_index: list[dict] = []
    failures: list[str] = []

    for path in entries:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"{path.name}: unreadable ({type(exc).__name__})")
            counts["quarantine"] += 1
            continue

        status, reasons = validate(entry)
        counts[status] += 1

        if args.dry_run:
            if not args.quiet:
                print(f"[dry-run] {path.name}: {status}"
                      + (f" — {'; '.join(reasons)}" if reasons else ""))
            continue

        sidecar = path.with_suffix(".verdict.json")
        try:
            if status == "publish":
                dest = publish(entry, archive)
                sidecar.write_text(json.dumps(
                    verdict(entry, "published", [f"archive: {dest}"]), indent=2),
                    encoding="utf-8")
                shutil.move(str(path), str(published_dir / path.name))
                published_index.append({
                    "product": entry.get("product"),
                    "version": entry.get("version"),
                    "file": str(dest),
                    "prompt_hash": entry.get("prompt_hash"),
                    "published_at": now(),
                })
                if not args.quiet:
                    print(f"published {path.name} -> {dest}")
            elif status == "partial":
                sidecar.write_text(json.dumps(
                    verdict(entry, "partial", reasons), indent=2), encoding="utf-8")
                shutil.move(str(path), str(partials_dir / path.name))
                if not args.quiet:
                    print(f"partial   {path.name} — {'; '.join(reasons)}")
            else:
                sidecar.write_text(json.dumps(
                    verdict(entry, "quarantined", reasons), indent=2),
                    encoding="utf-8")
                shutil.move(str(path), str(quarantine_dir / path.name))
                if not args.quiet:
                    print(f"QUARANTINE {path.name} — {'; '.join(reasons)}")
        except Exception as exc:
            failures.append(f"{path.name}: move/publish failed "
                            f"({type(exc).__name__}: {exc})")
            counts["quarantine"] += 1

    if published_index and not args.dry_run:
        index = archive / "index.jsonl"
        try:
            with open(index, "a", encoding="utf-8") as fh:
                for row in published_index:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            failures.append(f"index write failed: {exc}")

    if not args.quiet:
        print(f"swept {len(entries)}: {counts['publish']} published, "
              f"{counts['partial']} partial, {counts['quarantine']} quarantined")
        if failures:
            for f in failures:
                print(f"  ! {f}")

    if failures:
        return 4
    if counts["quarantine"]:
        return 3
    if counts["partial"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
