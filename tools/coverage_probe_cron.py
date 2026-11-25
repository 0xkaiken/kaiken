#!/usr/bin/env python3
"""coverage_probe_cron — surface ambiguous refusals worth lexicographic review.

The refusal classifier is a lexicon.  A lexicon only improves if someone shows
you the records that sit on its boundary.  This job does exactly one thing:
walk the gauntlet transcripts, classify every response, and collect the
AMBIGUOUS ones whose opening still reads like a decline.

It REPORTS.  It never promotes a signal into ``REFUSAL_SIGNALS`` — that is a
human decision, because a bad signal silently changes every future benchmark
number.

Output
------
``<workshop>/tools/coverage_candidates.jsonl`` — one record per candidate:
    {ts, source, record_id, opening, classification}

Exit codes
----------
0  no novel candidates (silent log)
2  candidates surfaced (dragonfire + comms already fired)
3  probe failed (no readable input)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

try:
    import refusal_classify as rc
except Exception as exc:  # pragma: no cover
    print(f"error: refusal_classify unavailable: {exc}", file=sys.stderr)
    raise SystemExit(3)


def workshop() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def home() -> Path:
    try:
        from _paths import pliny_home
        return pliny_home()
    except Exception:
        for candidate in (TOOL_DIR.parent, TOOL_DIR, Path.cwd()):
            if (candidate / "server.py").is_file():
                return candidate
        return TOOL_DIR.parent


def candidates_path() -> Path:
    return workshop() / "tools" / "coverage_candidates.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iter_responses(library: Path):
    """Yield (source, record_id, text) from gauntlet transcripts."""
    runs = library / "gauntlets" / "runs"
    if not runs.is_dir():
        return
    for transcript in sorted(runs.rglob("transcripts/*.jsonl")):
        for lineno, line in enumerate(
                transcript.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            text = _extract_text(obj)
            if text:
                yield (str(transcript.relative_to(library)),
                       f"{transcript.stem}:{lineno}", text)


def _extract_text(obj) -> str:
    """Pull the assistant text out of the several transcript shapes in play."""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return ""
    for key in ("response", "text", "content", "message"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            inner = val.get("content")
            if isinstance(inner, str):
                return inner
            if isinstance(inner, list):
                return "\n".join(
                    b.get("text", "") for b in inner
                    if isinstance(b, dict) and b.get("type") == "text"
                )
    return ""


def notify(title: str, message: str, category: str) -> None:
    try:
        subprocess.run([sys.executable, str(TOOL_DIR / "dragonfire_ping.py"),
                        title, message, category], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def comms(content: str, channel: str = "watchtower") -> None:
    url = os.environ.get("PLINY_DASHBOARD", "http://127.0.0.1:8888")
    body = json.dumps({"sender": "coverage-probe", "content": content,
                       "channel": channel}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/comms/send", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def main() -> int:
    p = argparse.ArgumentParser(prog="coverage_probe_cron", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--max", type=int, default=50)
    p.add_argument("--library", help="override the rt_library path")
    args = p.parse_args()

    library = Path(args.library) if args.library else home() / "rt_library"
    if not library.is_dir():
        if not args.quiet:
            print(f"no library at {library} — nothing to probe")
        return 3

    seen: set[str] = set()
    existing = candidates_path()
    if existing.is_file():
        for line in existing.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                seen.add(json.loads(line)["record_id"])
            except Exception:
                continue

    found: list[dict] = []
    scanned = 0
    for source, rid, text in iter_responses(library):
        scanned += 1
        if rid in seen:
            continue
        result = rc.classify(text)
        if result["label"] != "AMBIGUOUS":
            continue
        if not rc.looks_refusal_like(text):
            continue
        found.append({
            "ts": now(),
            "source": source,
            "record_id": rid,
            "opening": text[:150].replace("\n", " "),
            "reasons": result["reasons"],
        })
        if len(found) >= args.max:
            break

    if not found:
        if not args.quiet:
            print(f"scanned {scanned} response(s); no novel candidates")
        return 0

    candidates_path().parent.mkdir(parents=True, exist_ok=True)
    with open(candidates_path(), "a", encoding="utf-8") as fh:
        for rec in found:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if not args.quiet:
        print(f"scanned {scanned}; {len(found)} candidate(s) -> {candidates_path()}")
        for rec in found[:5]:
            print(f"  {rec['record_id']}: {rec['opening'][:90]}")

    notify("COVERAGE CANDIDATES",
           f"{len(found)} ambiguous refusal-shaped record(s) surfaced for lexicon "
           f"review. See tools/coverage_candidates.jsonl", "discovery")
    comms(f"Coverage probe: {len(found)} ambiguous record(s) awaiting promotion "
          f"review. Promote only after reading each opening.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
