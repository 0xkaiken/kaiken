#!/usr/bin/env python3
"""smoke_test — prove every workshop tool runs, offline and without side effects.

    python3 tools/smoke_test.py
    python3 tools/smoke_test.py --verbose
    python3 tools/smoke_test.py --json

Each check shells out to the real CLI with flags that cannot spend money, touch
the live staging directory, or post to the dashboard. A failing check prints the
tail of its stderr so the failure is actionable rather than a red X.

Exit codes: 0 all pass, 1 at least one failure.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PY = sys.executable
OBJ = "summarise a technical procedure"


def run(name: str, args: list[str], *, expect: tuple[int, ...] = (0,),
        timeout: int = 120) -> dict:
    cmd = [PY, "-X", "utf8", str(TOOLS / name)] + args
    try:
        # encoding="utf-8" + errors="replace": host user directories can carry
        # non-ASCII names that end up inside tool output paths. The default cp1252 reader thread crashes on those bytes before the test
        # verdict lands (b"" -> len(None) TypeError downstream). Decode permissively.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=str(TOOLS))
    except Exception as exc:
        return {"check": name, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    ok = proc.returncode in expect
    return {
        "check": name, "ok": ok, "exit": proc.returncode,
        "bytes": len(proc.stdout), "args": " ".join(args),
        "stderr": "" if ok else (proc.stderr or "")[-500:],
    }


def main() -> int:
    p = argparse.ArgumentParser(prog="smoke_test", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="pliny-smoke-"))
    results: list[dict] = []

    # --- encoders and generators (pure, no network) -----------------------
    results.append(run("prompt_encoder.py", ["encode", "--text", OBJ,
                                             "--format", "all", "--json"]))
    results.append(run("prompt_encoder.py", ["list"]))
    results.append(run("prompt_mutator.py", [OBJ, "--all", "--json"]))
    results.append(run("prompt_mutator.py", [OBJ, "--transforms", "frame",
                                             "--json"]))
    results.append(run("auto_redteam.py", [OBJ, "--level", "5", "--all", "--json"]))
    for tool in ("adversarial_poetry.py", "likert_judge.py",
                 "deceptive_delight.py", "reasoning_chain_exploiter.py"):
        results.append(run(tool, [OBJ, "--all", "--json"]))

    # --- classifiers and scanners ----------------------------------------
    results.append(run("refusal_classify.py",
                       ["--text", "I'm sorry, but I can't help with that."]))
    results.append(run("refusal_classify.py", ["--list-signals"]))
    results.append(run("named_not_owned_scan.py",
                       ["--root", str(TOOLS.parent), "--no-markers", "--json"],
                       expect=(0, 2)))

    # --- job scripts (jobs run from the install dir; use temp targets) ----
    staging = tmp / "staging"
    archive = tmp / "archive"
    staging.mkdir(parents=True, exist_ok=True)
    results.append(run("cl4r1t4s_staging_sweeper.py",
                       ["--staging", str(staging), "--archive", str(archive),
                        "--quiet"], expect=(0, 2, 3)))

    pins = tmp / "pins.json"
    results.append(run("cl4r1t4s_probe_regression_cron.py",
                       ["--pins", str(pins), "--quiet"], expect=(0, 2, 3, 4)))

    # --- notifier: dead port forces the queue path, no dashboard traffic ---
    results.append(run("dragonfire_ping.py",
                       ["SMOKE TEST", "offline path", "tool",
                        "--url", "http://127.0.0.1:59999", "--json"]))

    # --- publishing: dry run only ----------------------------------------
    run_id = _first_run_id()
    if run_id:
        results.append(run("gh_l1b3rt4s_pr.py",
                           ["--run-id", run_id, "--dry-run", "--json"]))
    else:
        results.append({"check": "gh_l1b3rt4s_pr.py", "ok": True,
                        "skipped": "no gauntlet runs found"})

    # --- visual tool: metrics only, no capture ---------------------------
    results.append(_doc_check("v1s10n.py"))

    # --- instruments: static run + import-survives-relocation checks -----
    # api_surface_audit returns 1 when a UI call has no server route, which is
    # a real finding rather than a broken tool, so 1 is an expected exit here.
    results.append(run("api_surface_audit.py", [], expect=(0, 1)))
    # Every `python <tool>.py` in the docs must resolve and answer --help. This
    # is the doc-side twin of api_surface_audit and covers every tool, not just
    # the ones named above.
    results.append(run("doc_command_audit.py", ["--json"], expect=(0,)))
    results.append(_doc_check("judge_calibration.py"))
    # Adaptive judge policy: selftest proves the decision engine with scripted
    # scorers (zero network), plan proves the input path. Neither spends a call.
    results.append(run("judge_adaptive.py", ["selftest"]))
    adaptive_items = tmp / "adaptive_items.jsonl"
    adaptive_items.write_text(
        json.dumps({"id": "smoke", "name": "smoke",
                    "request": "r", "response": "s"}) + "\n",
        encoding="utf-8")
    results.append(run("judge_adaptive.py", ["plan", str(adaptive_items)]))
    # pulsvivus imports opencode_runner from the install dir; --help is the
    # cheap proof that the marker resolution still works after a move.
    results.append(_doc_check("pulsvivus.py"))
    # 0 = clear, 1 = degraded (stale receipt). 2 means a check is genuinely
    # broken, which is what the suite must catch. --skip-harness spends nothing.
    results.append(run("wave_preflight.py", ["--skip-harness", "--json"],
                       expect=(0, 1)))

    # --- launch gate: the server refuses a wave past a BROKEN preflight ---
    # This pytest check runs the real HTTP-handler test suite. It includes one
    # live preflight run (a single harness control ping), by design: the gate
    # is only proven if the script under it is the script that runs.
    sys.path.insert(0, str(TOOLS))
    gate_results = subprocess.run(
        [PY, "-X", "utf8", "-m", "pytest", "-q", "tests/test_gauntlet_launch_gate.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, cwd=str(_repo_root()))
    results.append({
        "check": "pytest test_gauntlet_launch_gate.py", "ok": gate_results.returncode == 0,
        "exit": gate_results.returncode, "bytes": len(gate_results.stdout),
        "stderr": "" if gate_results.returncode == 0 else (gate_results.stderr or "")[-500:],
    })

    # --- secrets client surface ------------------------------------------
    results.append(_secrets_check())

    failures = [r for r in results if not r["ok"]]

    if args.json:
        print(json.dumps({"passed": len(results) - len(failures),
                          "failed": len(failures), "results": results}, indent=2))
    else:
        width = max(len(r["check"]) for r in results)
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            note = r.get("skipped") or f"exit {r.get('exit')}  {r.get('bytes', 0)}B"
            print(f"  [{status}] {r['check']:<{width}}  {note}")
            if not r["ok"] and r.get("stderr"):
                for line in r["stderr"].strip().splitlines()[-4:]:
                    print(f"         {line}")
            elif args.verbose and r.get("args"):
                print(f"         {r['args']}")
        print(f"\n{len(results) - len(failures)}/{len(results)} passed")

    return 1 if failures else 0


def _repo_root() -> Path:
    """Repo root for pytest discovery. tools/ exists in two copies (repo and
    workshop); resolve the relocation-tolerant way via _paths.pliny_home(),
    never parents[N] - only one copy is correct there."""
    try:
        from _paths import pliny_home  # _paths is a sibling of this file
        return pliny_home()
    except Exception:
        return TOOLS.parent


def _first_run_id() -> str:
    try:
        sys.path.insert(0, str(TOOLS))
        from _paths import library_dir
        runs = sorted((library_dir() / "gauntlets" / "runs").glob("*/state.json"))
        return runs[-1].parent.name if runs else ""
    except Exception:
        return ""


def _doc_check(name: str) -> dict:
    """--help must work even when the optional dependency is missing."""
    proc = subprocess.run([PY, "-X", "utf8", str(TOOLS / name), "--help"],
                          capture_output=True, text=True, timeout=60,
                          cwd=str(TOOLS))
    return {"check": name, "ok": proc.returncode == 0, "exit": proc.returncode,
            "bytes": len(proc.stdout),
            "stderr": (proc.stderr or "")[-400:] if proc.returncode else ""}


def _secrets_check() -> dict:
    # The smoke tool was relocated (repo-root/tools -> state/workshop/tools); a
    # hard-coded sys.path died with the move. Walk up until the module appears.
    root: Path = TOOLS
    for _ in range(5):
        if (root / "pliny_secrets_client.py").exists():
            break
        root = root.parent
    proc = subprocess.run(
        [PY, "-X", "utf8", "-c",
         "import sys, json; sys.path.insert(0, r'%s');"
         "import pliny_secrets_client as s;"
         "d=[s.sidecar_ping(), s.sidecar_providers(), s.sidecar_oauth_status()];"
         "assert all(x.get('ok') for x in d); print(json.dumps(d[1]))"
         % str(root)],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace")
    return {"check": "pliny_secrets_client", "ok": proc.returncode == 0,
            "exit": proc.returncode, "bytes": len(proc.stdout),
            "stderr": (proc.stderr or "")[-400:] if proc.returncode else ""}


if __name__ == "__main__":
    raise SystemExit(main())
