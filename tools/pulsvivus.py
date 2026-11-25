#!/usr/bin/env python3
"""
PULSVIVUS — the living pulse. Answers one question per command:

    is upstream alive TODAY?

Every dragon since 2026-11-02 has re-verified upstream access by hand with
ad-hoc pings (see attempts/access_status_*.md). This is that work, canonized.
PULSVIVUS pings every cataloged route once with a no-op prompt; auth/credit
failures return before generation, so a dead route costs ~$0. A route is
ALIVE only when it returns completion text.

Usage (from the workshop or the repo root — both resolve the install dir):
    python tools/pulsvivus.py                 # ping all routes, print report
    python tools/pulsvivus.py --write         # also write <workshop>/attempts/access_status_<today>.md
    python tools/pulsvivus.py --shout         # also dragonfire-ping :8888 on any alive route
    python tools/pulsvivus.py --only opencode # ping routes whose id contains "opencode"

Routes: the model ids are passed straight to `opencode run -m <id>`, so each
route is an opencode provider alias — exactly the four routes cataloged by
Probe (2026-11-02), plus the opencode-go control route (the calling dragon's
own model) that proves the harness itself is alive.

Exit codes: 0 = at least one upstream route alive, 1 = all dead, 2 = harness
error (control route failed — do not trust the dead verdicts). A filtered run
(--only) answers for the routes it was asked about: 0 when every selected
route is alive, 1 when any of them is not, 2 when the selection itself is the
problem (control failed while selected, or --only matched nothing).

--write always lands the report, including on a harness error: the diagnostic
value is highest exactly when the control route just broke.
"""

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

# This file is installed into <workshop>/tools/ as well as living in the repo's
# tools/, so parents[1] is only right for one of the two. _paths resolves the
# install dir from PLINY_HOME, the .pliny_home marker, or a server.py search.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import pliny_home, workshop  # noqa: E402

REPO_ROOT = pliny_home()
sys.path.insert(0, str(REPO_ROOT))

import opencode_runner  # noqa: E402

PING_PROMPT = "PING. Reply with exactly: PONG"
PING_TIMEOUT_S = 120

# (route_id, model_id, note)
# Keep in sync with the Probe's fire-verified catalog:
# state/workshop/attempts/access_status_*.md
ROUTES = [
    ("control/glm-5.3-flash", opencode_runner.GO_DEFAULT_MODEL,
     "harness control — the calling dragon's own model; must be alive"),
    ("opencode/gpt-6.1-sol", "opencode/gpt-6.1-sol",
     "route 2: opencode alias, OpenAI lineage (402 insufficient funds 2026-11-02)"),
    ("openrouter/openai/gpt-6.1-sol", "openrouter/openai/gpt-6.1-sol",
     "route 1: OpenRouter, OpenAI lineage (401 user-not-found 2026-11-02)"),
    ("orcarouter/openai/gpt-5.6-sol", "orcarouter/openai/gpt-5.6-sol",
     "route 4: orcarouter.ai, credit-gated ($0.59 2026-11-02)"),
    ("openai/gpt-6.1-sol", "openai/gpt-6.1-sol",
     "route 3: direct OpenAI via OAuth — may need `opencode auth login`"),
]


def _ascii(s: str) -> str:
    """Flatten typography the report's readers may not decode.

    The report is valid UTF-8, but it lands on a Windows console and in editors
    that still default to cp1252, where every em dash and arrow is mojibake.
    Content is unchanged; only punctuation is folded.
    """
    table = {"—": "-", "–": "-", "→": "->", "’": "'", "“": '"', "”": '"'}
    return "".join(table.get(c, c) for c in s)


def classify(answer: str, error: str):
    """(verdict, detail) for one ping."""
    if error:
        e = error.lower()
        if "401" in error or "user not found" in e or "revoked" in e or "unauthorized" in e:
            return ("401", "auth dead — key revoked or OAuth expired")
        if "402" in error or "quota" in e or "insufficient" in e or "credits" in e or "out of" in e:
            return ("402", "credit-gated — balance depleted")
        return ("ERR", error[:180])
    if answer:
        return ("ALIVE", " ".join(answer.split())[:120])
    return ("ERR", "empty response, no error")


def main(argv=None):
    ap = argparse.ArgumentParser(description="PULSVIVUS — is upstream alive today?")
    ap.add_argument("--write", action="store_true",
                    help="write state/workshop/attempts/access_status_<today>.md")
    ap.add_argument("--shout", action="store_true",
                    help="dragonfire-ping http://localhost:8888 on any alive route")
    ap.add_argument("--only", default="",
                    help="ping only routes whose id contains this substring")
    args = ap.parse_args(argv)

    selected = [r for r in ROUTES
                if not args.only or args.only.lower() in r[0]]

    results = []
    control_alive = False
    any_alive = False
    for route_id, model_id, note in selected:
        try:
            answer = opencode_runner.complete_text(PING_PROMPT, model=model_id,
                                                   timeout=PING_TIMEOUT_S)
            error = ""
        except Exception as exc:  # RuntimeError with the real auth/credit cause
            answer, error = "", f"{type(exc).__name__}: {exc}"
        verdict, detail = classify(answer, error)
        results.append({"route": route_id, "model": model_id, "note": note,
                        "verdict": verdict, "detail": detail})
        if verdict == "ALIVE":
            if route_id.startswith("control/"):
                control_alive = True
            else:
                any_alive = True

    full_run = not args.only
    control_selected = any(r["route"].startswith("control/") for r in results)
    # Only a control route that was actually pinged can vouch for the harness.
    harness_error = control_selected and not control_alive

    print("PULSVIVUS — one ping per route")
    print()
    for r in results:
        print(f"  [{r['verdict']:<7}] {r['route']}")
        print(f"              {r['detail']}")
    print()

    if not results:
        print(f"  VERDICT: no route matched --only {args.only!r}; nothing was pinged.")
    elif harness_error:
        print("  VERDICT: HARNESS ERROR — control route failed; dead verdicts untrustworthy.")
    elif full_run:
        if any_alive:
            print("  VERDICT: UPSTREAM ALIVE. Fire a staged battery (see state/workshop/probes/).")
        else:
            print("  VERDICT: ALL DEAD upstream. The control route answered, so the harness")
            print("  works - this is an access wall, not a code failure. Operator lever")
            print("  unchanged: fresh OpenRouter key / opencode auth login / gateway topup")
            print("  (Conclave Directive 4.1).")
    else:
        dead = [r for r in results if r["verdict"] != "ALIVE"]
        if dead:
            print(f"  VERDICT: {len(dead)}/{len(results)} selected route(s) not alive.")
        else:
            print(f"  VERDICT: all {len(results)} selected route(s) ALIVE.")

    # The report is written on every verdict, harness error included — a
    # diagnostic that vanishes when the harness breaks is written backwards.
    if args.write:
        dest = workshop() / "attempts" / \
            f"access_status_{date.today().strftime('%Y%m%d')}.md"
        lines = [
            # ASCII only: this report is read by whatever consumes the file, and
            # an em dash survives UTF-8 but not a cp1252 reader on this host.
            f"# Upstream access status - {date.today().isoformat()}",
            "",
            "Verified by fire with `tools/pulsvivus.py` (PULSVIVUS, one PING per route,",
            "~$0 per dead route). Auto-generated; do not hand-edit.",
            "",
            "| Route | Model id | Verdict | Detail |",
            "|---|---|---|---|",
        ]
        for r in results:
            lines.append(f"| {r['route']} | `{r['model']}` | {r['verdict']} | {r['detail'][:110]} |")
        lines += [
            "",
            "- Battery: `state/workshop/probes/` — persona, matched-pair, minimax "
            "(fire-ready; see probes/README.md).",
            "- Operator lever: Conclave Directive 4.1 (fresh key / auth login / topup).",
        ]
        if harness_error:
            lines.append("- HARNESS ERROR: the control route failed. The verdicts above "
                         "describe a broken harness, not a dead upstream.")
        if not results:
            lines.append(f"- No route matched `--only {args.only}`; nothing was pinged.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(_ascii(line) for line in lines) + "\n",
                        encoding="utf-8")
        print(f"\n  Written: {dest}")

    if args.shout and any_alive:
        alive = [r for r in results if r["verdict"] == "ALIVE"]
        body = {
            "title": "UPSTREAM IS ALIVE 🔥",
            "message": "PULSVIVUS pulse: " + "; ".join(
                f"{r['route']} → ALIVE ({r['detail'][:60]})" for r in alive),
            "category": "discovery",
        }
        try:
            req = urllib.request.Request(
                "http://localhost:8888/api/dragonfire",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            print("  Shouted to the dashboard (dragonfire).")
        except Exception as exc:
            print(f"  Dragonfire failed: {exc}")

    if not results:
        return 2  # nothing was measured; "dead" would be a lie
    if harness_error:
        return 2
    if full_run:
        return 0 if any_alive else 1
    return 0 if all(r["verdict"] == "ALIVE" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
