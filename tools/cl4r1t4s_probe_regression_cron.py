#!/usr/bin/env python3
"""cl4r1t4s_probe_regression_cron — detect drift in pinned system prompts.

Run weekly (or by the dashboard's /api/jobs/cl4r1t4s/regression handler).

Pins live in ``<workshop>/tools/cl4r1t4s_pins.json``::

    {"pins": [{"name": "chatgpt", "path": "CL4R1T4S/OPENAI/chatgpt.txt",
               "sha256": "..."}, ...]}

``path`` is resolved against the workshop; ``url`` is also accepted and fetched
over HTTPS.  First run with no pins file seeds it from the published archive and
exits 0 — there is nothing to regress against yet.

Exit codes
----------
0  all pins held
2  REGRESSION DETECTED (dragonfire + comms already fired)
3  fetch/read failure (retry already attempted once)
4  configuration error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))


def workshop() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def pins_path() -> Path:
    return workshop() / "tools" / "cl4r1t4s_pins.json"


def log_path() -> Path:
    return workshop() / "tools" / "cl4r1t4s_regression.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(pin: dict, retries: int = 1) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if pin.get("url"):
                with urllib.request.urlopen(pin["url"], timeout=30) as resp:
                    return resp.read()
            path = workshop() / pin["path"]
            return path.read_bytes()
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def seed() -> dict:
    archive = workshop() / "CL4R1T4S"
    pins = []
    if archive.is_dir():
        for f in sorted(archive.rglob("*.txt")):
            pins.append({
                "name": f.stem,
                "path": str(f.relative_to(workshop())),
                "sha256": digest(f.read_bytes()),
            })
    return {"seeded_at": now(), "pins": pins}


def notify(title: str, message: str, category: str) -> None:
    try:
        import dragonfire_ping  # noqa: F401  (module import is enough to test)
        subprocess_args = [sys.executable, str(TOOL_DIR / "dragonfire_ping.py"),
                           title, message, category]
        __import__("subprocess").run(subprocess_args, timeout=20,
                                     stdout=__import__("subprocess").DEVNULL,
                                     stderr=__import__("subprocess").DEVNULL)
    except Exception:
        pass


def comms(content: str, channel: str = "watchtower") -> None:
    url = os.environ.get("PLINY_DASHBOARD", "http://127.0.0.1:8888")
    body = json.dumps({"sender": "cl4r1t4s-regression",
                       "content": content, "channel": channel}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/comms/send", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def main() -> int:
    p = argparse.ArgumentParser(prog="cl4r1t4s_probe_regression_cron",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--reseed", action="store_true", help="rebuild pins from the archive")
    p.add_argument("--pins", help="override the pins file path")
    args = p.parse_args()

    path = Path(args.pins) if args.pins else pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if args.reseed or not path.is_file():
        data = seed()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"seeded {len(data['pins'])} pin(s) -> {path}")
        if not data["pins"]:
            print("note: archive empty; nothing pinned yet "
                  "(publish with cl4r1t4s_staging_sweeper first)", file=sys.stderr)
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: cannot read pins: {exc}", file=sys.stderr)
        return 4

    pins = data.get("pins") or []
    if not pins:
        print("error: pins file has no 'pins' entries", file=sys.stderr)
        return 4

    regressions, fetch_failures, held = [], [], 0
    for pin in pins:
        try:
            raw = fetch(pin)
        except Exception as exc:
            fetch_failures.append({"pin": pin.get("name"), "error": str(exc)})
            continue
        actual = digest(raw)
        if pin.get("sha256") and actual != pin["sha256"]:
            regressions.append({
                "pin": pin.get("name"), "path": pin.get("path") or pin.get("url"),
                "expected": pin["sha256"], "actual": actual,
                "bytes": len(raw),
            })
        else:
            held += 1

    record = {"ts": now(), "held": held, "regressions": regressions,
              "fetch_failures": fetch_failures}
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        if not args.quiet:
            print(f"warning: cannot write log {log_path()}: {exc}", file=sys.stderr)

    if not args.quiet:
        print(f"pins: {len(pins)}  held: {held}  regressed: {len(regressions)}  "
              f"unreachable: {len(fetch_failures)}")
        for r in regressions:
            print(f"  REGRESSION {r['pin']} ({r['path']}) "
                  f"{r['expected'][:12]} -> {r['actual'][:12]}")
        for f in fetch_failures:
            print(f"  UNREACHABLE {f['pin']}: {f['error']}")

    if regressions:
        names = ", ".join(r["pin"] or "?" for r in regressions)
        notify("CL4R1T4S DRIFT", f"Pinned system prompt(s) changed: {names}. "
                                 f"See tools/cl4r1t4s_regression.jsonl", "alert")
        comms(f"CL4R1T4S drift detected: {names}. The pinned text no longer "
              f"matches the published archive.")
        return 2

    if fetch_failures and not held:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
