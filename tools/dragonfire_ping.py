#!/usr/bin/env python3
"""dragonfire_ping — raise a notification on the dashboard.

Called by agents and by Basilisk escalation as::

    python3 tools/dragonfire_ping.py "TITLE" "message" [category]
    python3 tools/dragonfire_ping.py "TITLE" - breakthrough < message.txt

Categories: discovery (default), tool, writing, breakthrough, alert.

The dashboard endpoint is unauthenticated but bound to localhost.  When the
dashboard is down this tool does not fail the calling mission: it appends the
notification to ``<workshop>/dragonfire_queue.jsonl`` and exits 0, so the alert
is delivered on the next start instead of being lost.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = os.environ.get("PLINY_DASHBOARD", "http://127.0.0.1:8888")
CATEGORIES = ("discovery", "tool", "writing", "breakthrough", "alert")


def workshop_dir() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def post(url: str, payload: dict, timeout: float = 5.0) -> tuple[bool, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/dragonfire", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def queue_locally(payload: dict) -> Path:
    path = workshop_dir() / "dragonfire_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**payload, "queued_at": time.time()}) + "\n")
    return path


def main() -> int:
    p = argparse.ArgumentParser(prog="dragonfire_ping", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("title", help='notification title, e.g. "GPT-6.1 PWNED"')
    p.add_argument("message", nargs="?", help="body text (default: stdin)")
    p.add_argument("category", nargs="?", default="discovery",
                   help=" | ".join(CATEGORIES))
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    args = p.parse_args()

    if args.message:
        message = args.message
    elif not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    else:
        p.error("provide a message argument or pipe stdin")
        return 2

    if not message:
        print("error: empty message", file=sys.stderr)
        return 2

    if args.category not in CATEGORIES:
        print(f"warning: unknown category {args.category!r}; "
              f"using 'discovery' (known: {', '.join(CATEGORIES)})", file=sys.stderr)
        args.category = "discovery"

    payload = {"title": args.title.strip(), "message": message,
               "category": args.category}

    ok, detail = post(args.url, payload)
    if ok:
        result = {"ok": True, "delivered": "dashboard", "detail": detail}
    else:
        path = queue_locally(payload)
        result = {"ok": True, "delivered": "queue", "queue": str(path),
                  "detail": detail}

    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.quiet:
        if result["delivered"] == "dashboard":
            print(f"[dragonfire] {payload['title']} -> dashboard")
        else:
            print(f"[dragonfire] dashboard unreachable ({detail}); "
                  f"queued to {result['queue']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
