#!/usr/bin/env python3
"""wave_preflight - one verdict before a gauntlet wave: can I quote this number?

WHY THIS EXISTS
`canon/JUDGE_INSTRUMENT.md` says to run `judge_calibration.py` before a wave you
intend to quote. Nothing enforced that, so in practice the calibration ran when
a session remembered to run it, and the freshness of the receipt lived in that
session's head. This composes the checks into one gate with exit codes a launcher
or an agent can branch on.

The two questions are different and the tool keeps them apart:

  CAN I RUN?     harness alive, API surface complete. Broken here = do not start.
  CAN I QUOTE?   judge calibration receipt exists, names the judge currently in
                 gauntlet.JUDGE_MODEL, passed, and is younger than --max-age.
                 Stale here = the run is fine, the number is not.

USAGE
    python tools/wave_preflight.py                 # fast checks + receipt age
    python tools/wave_preflight.py --full          # also run judge_calibration
    python tools/wave_preflight.py --max-age 12    # hours (default 24)
    python tools/wave_preflight.py --skip-harness  # spend no request at all
    python tools/wave_preflight.py --json --write  # machine mode + receipt

The canonical receipt lives at `<workshop>/probes/judge_calibration_latest.json`.
`--full` writes it; nothing else does, on purpose - a receipt you did not measure
is a claim, not a receipt.

EXIT CODES
    0  CLEAR     harness alive, API complete, receipt fresh and passing
    1  DEGRADED  something warns (stale/missing/mismatched receipt) - a run may
                 start; do not quote its scores
    2  BROKEN    harness dead, API surface missing routes, or a check crashed
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import pliny_home, workshop  # noqa: E402

TOOLS = Path(__file__).resolve().parent
PY = sys.executable
RECEIPT = workshop() / "probes" / "judge_calibration_latest.json"

CLEAR, DEGRADED, BROKEN = "CLEAR", "DEGRADED", "BROKEN"


def run_tool(name: str, args: list[str] | None = None, timeout: int = 300) -> dict:
    """Run a sibling tool. Decode permissively: this host has non-ASCII paths."""
    args = args or []
    try:
        proc = subprocess.run([PY, "-X", "utf8", str(TOOLS / name)] + args,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=str(TOOLS))
        return {"exit": proc.returncode, "out": proc.stdout, "err": proc.stderr}
    except Exception as exc:
        return {"exit": None, "out": "", "err": f"{type(exc).__name__}: {exc}"}


def judge_model() -> str | None:
    try:
        sys.path.insert(0, str(pliny_home()))
        import gauntlet
        return getattr(gauntlet, "JUDGE_MODEL", None)
    except Exception:
        return None


def check_harness() -> dict:
    res = run_tool("pulsvivus.py", ["--only", "control"], timeout=180)
    alive = res["exit"] == 0
    detail = "control route ALIVE" if alive else (
        "control route did not answer (exit %s)" % res["exit"])
    return {"check": "harness", "ok": alive, "detail": detail}


def check_api() -> dict:
    res = run_tool("api_surface_audit.py")
    missing = 0
    for line in res["out"].splitlines():
        if line.strip().startswith("total missing:"):
            try:
                missing = int(line.split(":")[1].strip())
            except ValueError:
                pass
    ok = res["exit"] == 0 and missing == 0
    detail = ("%d route(s) missing" % missing) if missing else (
        "every UI call has a route" if ok else "audit failed (exit %s)" % res["exit"])
    return {"check": "api-surface", "ok": ok, "detail": detail}


def check_receipt(path: Path, max_age_h: float, expected_model: str | None) -> dict:
    if not path.is_file():
        return {"check": "judge-receipt", "ok": False, "warn": True,
                "detail": "no receipt at %s - run --full before quoting" % path}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"check": "judge-receipt", "ok": False, "warn": True,
                "detail": "unreadable receipt: %s" % exc}

    if not isinstance(data, dict) or "rows" not in data:
        return {"check": "judge-receipt", "ok": False, "warn": True,
                "detail": "legacy receipt without judge_model/timestamps - rerun --full"}

    model = data.get("judge_model")
    passed = bool(data.get("passed"))
    finished = data.get("finished") or ""
    try:
        age_h = (datetime.now() - datetime.fromisoformat(finished)).total_seconds() / 3600
    except Exception:
        age_h = None

    problems = []
    if not passed:
        problems.append("last calibration FAILED")
    if expected_model and model and model != expected_model:
        problems.append("calibrated on %s, gauntlet uses %s" % (model, expected_model))
    if expected_model and not model:
        problems.append("receipt names no judge model")
    if age_h is None:
        problems.append("no usable finished timestamp")
    elif age_h > max_age_h:
        problems.append("%.1fh old (limit %.0fh)" % (age_h, max_age_h))

    if problems:
        return {"check": "judge-receipt", "ok": False, "warn": True,
                "detail": "; ".join(problems) + " - rerun --full"}
    return {"check": "judge-receipt", "ok": True,
            "detail": "%s, pass, %.1fh old" % (model or "model unknown", age_h)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true",
                    help="run judge_calibration and write the canonical receipt")
    ap.add_argument("--max-age", type=float, default=24.0,
                    help="receipt freshness limit in hours (default 24)")
    ap.add_argument("--receipt", default=None, help="alternate receipt path")
    ap.add_argument("--skip-harness", action="store_true",
                    help="do not spend a request on the control ping")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="write <workshop>/probes/wave_preflight_<date>.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    receipt = Path(args.receipt) if args.receipt else RECEIPT
    checks: list[dict] = []

    if args.full:
        res = run_tool("judge_calibration.py", ["--reps", "1", "--quiet",
                                                "--json", str(receipt)])
        checks.append({"check": "calibration-run", "ok": res["exit"] == 0,
                       "detail": "exit %s" % res["exit"]})

    if args.skip_harness:
        checks.append({"check": "harness", "ok": True,
                       "detail": "skipped (--skip-harness)"})
    else:
        checks.append(check_harness())

    checks.append(check_api())
    checks.append(check_receipt(receipt, args.max_age, judge_model()))

    broken = any(not c["ok"] and not c.get("warn") for c in checks)
    warned = any(not c["ok"] and c.get("warn") for c in checks)
    verdict = BROKEN if broken else (DEGRADED if warned else CLEAR)

    payload = {
        "tool": "wave_preflight",
        "at": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "quoteable": verdict == CLEAR,
        "max_age_hours": args.max_age,
        "receipt": str(receipt),
        "checks": checks,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif not args.quiet:
        print("wave preflight  %s" % payload["at"])
        width = max(len(c["check"]) for c in checks)
        for c in checks:
            tag = "PASS" if c["ok"] else ("WARN" if c.get("warn") else "FAIL")
            print("  [%s] %-*s  %s" % (tag, width, c["check"], c["detail"]))
        print()
        if verdict == CLEAR:
            print("  VERDICT: CLEAR - a run may start and its scores are quoteable.")
        elif verdict == DEGRADED:
            print("  VERDICT: DEGRADED - a run may start; do NOT quote its scores.")
        else:
            print("  VERDICT: BROKEN - do not start a wave until this is fixed.")

    if args.write:
        dest = workshop() / "probes" / (
            "wave_preflight_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        if not args.quiet:
            print("\n  Written: %s" % dest)

    return 0 if verdict == CLEAR else (1 if verdict == DEGRADED else 2)


if __name__ == "__main__":
    sys.exit(main())
