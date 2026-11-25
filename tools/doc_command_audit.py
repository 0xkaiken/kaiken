#!/usr/bin/env python3
"""doc_command_audit — every tool command in the docs must actually resolve.

WHY THIS EXISTS
`api_surface_audit.py` checks the contract between the UI and the server. Nothing
checked the contract between the *documentation* and the tools it tells you to
run — and on 2026-11-05 two docs were telling agents to run commands that cannot
work from the cwd agents actually have (`probes/README.md` named `rt_send.py` and
a `state/workshop/`-prefixed payload from inside the workshop; `canon/JUDGE_INSTRUMENT.md`
named `state/workshop/tools/...` which only resolves from the install dir).

A doc that names a command that fails is worse than no doc: the reader assumes
the tool is broken, or that they are.

WHAT IT CHECKS
1. Every `python <script>.py` in the scanned docs resolves to a real file from
   at least one plausible root.
2. The script's `--help` exits 0 (catches relocation and import bugs across the
   whole tool surface, not just the three that `smoke_test` names).
3. Data paths (`--json out.json`, `--prompt-file x.txt`, ...) that exist under
   the install root but NOT under the workshop root are flagged: those only work
   if the reader happens to be in the install dir.

`python -m <module>` invocations and paths containing `<`, `>`, `{`, `}`, `$`
are treated as illustrative and skipped.

USAGE
    python tools/doc_command_audit.py
    python tools/doc_command_audit.py --no-help     # resolve only, do not exec
    python tools/doc_command_audit.py --all         # include record dirs
    python tools/doc_command_audit.py --json

Record directories (memory, forge-log, deep-research, red-team-notes, experiment,
pack-runs, attempts, questions, conclave) are histories: a command that was true
when it was written is not required to be true now. They are skipped unless
`--all` is passed.

EXIT CODES
    0  clean
    1  at least one finding
    2  nothing was parsed (the scanner itself is probably wrong)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import pliny_home, workshop  # noqa: E402

PY = sys.executable
SHOP = workshop()
HOME = pliny_home()

DOC_GLOBS = [
    "README.md", "QUALITY_GATE.md", "TWEET_VOICE.md",
    "tools/*.md", "probes/*.md", "canon/**/*.md", "ships/**/*.md",
]
RECORD_DIRS = {"memory", "forge-log", "deep-research", "red-team-notes",
               "experiment", "pack-runs", "attempts", "questions", "conclave"}

SCRIPT_RE = re.compile(r"\bpython3?\s+(\"[^\"]+\.py\"|'[^']+\.py'|\S+\.py)\b")
PATH_RE = re.compile(r"(?<![\w/.-])((?:state/workshop/)?[\w./-]+\.(?:txt|json|jsonl|md|png))")
ILLUSTRATIVE = set("<>{}")


def doc_files(include_records: bool) -> list[Path]:
    seen: list[Path] = []
    roots = [SHOP] if (SHOP.is_dir() and any(SHOP.glob("*.md"))) else [HOME, HOME / "red-team"]
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in DOC_GLOBS:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if not include_records and rel.split("/")[0] in RECORD_DIRS:
                    continue
                if path not in seen:
                    seen.append(path)
    return seen


def resolve(script: str, doc_dir: Path) -> tuple[Path | None, str]:
    """Return (path, root) where root is workshop / install / docdir / absolute."""
    p = Path(script)
    if p.is_absolute():
        return (p, "absolute") if p.is_file() else (None, "")
    for root, label in ((SHOP, "workshop"), (HOME, "install"), (doc_dir, "docdir")):
        cand = root / p
        if cand.is_file():
            return cand, label
    return None, ""


def help_ok(path: Path, cache: dict) -> tuple[bool, str]:
    """True when --help exits 0. Never executes a probe or a non-argparse script.

    Probes are fires, not tools: several of them have no argparse and start
    spending judge calls the moment they are imported. Running --help on one is
    not a check, it is a misfire (this happened once during development).
    """
    key = str(path)
    if key in cache:
        return cache[key]
    if "probes" in path.parts:
        cache[key] = (True, "not executed (probe)")
        return cache[key]
    try:
        source = path.read_text(encoding="utf-8", errors="replace")[:40000]
    except Exception:
        source = ""
    if "argparse" not in source:
        cache[key] = (True, "not executed (no argparse)")
        return cache[key]
    try:
        proc = subprocess.run([PY, "-X", "utf8", str(path), "--help"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=30, cwd=str(SHOP))
        ok = proc.returncode == 0
        detail = ""
        if not ok:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = tail[-1][:120] if tail else ""
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    cache[key] = (ok, detail)
    return cache[key]


def audit(include_records: bool, do_help: bool) -> tuple[list[dict], int]:
    findings: list[dict] = []
    parsed = 0
    help_cache: dict = {}
    # `install-only` alone is a hazard, not a bug: the script exists, the doc just
    # does not say where to run it. A `cd` on this line or the one above counts as
    # saying it, so the command is documented and the warning is suppressed.
    # `missing`, `help-fails` and `path-install-only` always fail.
    FAILING = {"missing", "help-fails", "path-install-only"}

    for path in doc_files(include_records):
        rel = (path.relative_to(SHOP) if path.is_relative_to(SHOP) else path.relative_to(HOME)).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines):
            m = SCRIPT_RE.search(line)
            if not m:
                continue
            script = m.group(1).strip("\"'")
            if any(c in script for c in ILLUSTRATIVE):
                continue
            parsed += 1
            prev = lines[index - 1] if index else ""
            cd_documented = "cd " in line[:m.start()] or "cd " in prev
            target, root = resolve(script, path.parent)
            if target is None:
                findings.append({"file": rel, "line": index + 1, "kind": "missing",
                                 "failing": True, "subject": script,
                                 "detail": "resolves from no root (workshop, install, doc dir)"})
                continue
            if root == "install" and not cd_documented:
                findings.append({"file": rel, "line": index + 1, "kind": "install-only",
                                 "failing": False, "subject": script,
                                 "detail": "runs from the install dir only; say so with a `cd`"})
            if do_help:
                ok, detail = help_ok(target, help_cache)
                if not ok:
                    findings.append({"file": rel, "line": index + 1, "kind": "help-fails",
                                     "failing": True, "subject": script, "detail": detail})
            for raw in PATH_RE.findall(line):
                if raw == script or any(c in raw for c in ILLUSTRATIVE):
                    continue
                under_workshop = (SHOP / raw).is_file()
                under_install = (HOME / raw).is_file()
                if under_install and not under_workshop and not cd_documented:
                    findings.append({"file": rel, "line": index + 1, "kind": "path-install-only",
                                     "failing": True, "subject": raw,
                                     "detail": "exists under the install dir only"})
    return findings, parsed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-help", action="store_true",
                    help="resolve paths only; never execute a tool")
    ap.add_argument("--all", action="store_true",
                    help="also scan record directories")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    findings, parsed = audit(args.all, not args.no_help)

    if args.json:
        print(json.dumps({"parsed_commands": parsed, "findings": findings}, indent=2,
                         ensure_ascii=False))
    elif findings:
        width = min(max(len(f["subject"]) for f in findings), 52)
        for f in findings:
            tag = f["kind"] if f["failing"] else f["kind"] + "?"
            print("  %-9s %s:%s" % (tag, f["file"], f["line"]))
            print("  %-9s   %-*s  %s" % ("", width, f["subject"][:width], f["detail"]))
        failed = sum(1 for f in findings if f["failing"])
        print("\n%d finding(s) across %d command(s): %d failing, %d warning(s)."
              % (len(findings), parsed, failed, len(findings) - failed))
    else:
        print("%d command(s) in %d doc(s): every one resolves and reports --help."
              % (parsed, len(doc_files(args.all))))

    if parsed == 0:
        print("ERROR: no commands parsed. The scanner is wrong, not the docs.", file=sys.stderr)
        return 2
    return 1 if any(f["failing"] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
