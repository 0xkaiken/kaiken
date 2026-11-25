#!/usr/bin/env python3
"""install_tools — seed the workshop with the tool suite and the agent definition.

The dashboard, the presets and every agent prompt resolve tools relative to the
workshop (``$PLINY_WORKSHOP/tools/...``), not relative to this repo. That split
is deliberate — the workshop is the operator's writable surface — but it means a
fresh clone has no ``tools/`` until something puts them there.

This script is that something. It is idempotent and conservative:

* copies ``tools/*`` into ``<workshop>/tools/``
* never overwrites a file that is newer than the source (use --force to override)
* installs ``agents/pliny-the-liberator.md`` into ``<workshop>/.claude/agents/``
  (add --user-agents to also write ``~/.claude/agents/``; off by default)
* creates the directory skeleton the runtime expects
* seeds ``v3suv1us_results/`` and ``v3suv1us_campaigns/`` so the dashboard has
  somewhere to write

Usage
-----
    python tools/install_tools.py
    python tools/install_tools.py --dry-run
    python tools/install_tools.py --force
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_TOOLS = REPO / "tools"

SKELETON = [
    "tools", "memory", "canon", "questions", "watchtower", "pack-runs",
    "experiment", "evolution-log", ".mycelium", ".claude/agents",
    "village-repo", "ships", "red-team-notes",
    "tools/v3suv1us_results", "tools/v3suv1us_campaigns",
    "tools/l1b3rt4s_staging",
]

SEED_FILES = {
    "QUALITY_GATE.md": (
        "# QUALITY GATE\n\n"
        "An artifact leaves the workshop only when all of these are true:\n\n"
        "- [ ] a stranger can run it from the README alone\n"
        "- [ ] it was actually executed at least once, output included\n"
        "- [ ] failure modes are documented, not hidden\n"
        "- [ ] no key, token or personal path appears anywhere in it\n"
        "- [ ] the claim in the write-up matches what the artifact does\n"
    ),
}


def workshop() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def copy_tree(src_dir: Path, dst_dir: Path, force: bool, dry: bool) -> list[str]:
    actions = []
    if not src_dir.is_dir():
        return [f"source missing: {src_dir}"]
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(src_dir.iterdir()):
        if src.is_dir() or src.name.startswith("__"):
            continue
        if src.suffix not in {".py", ".sh", ".html", ".md", ".json", ".txt"}:
            continue
        dst = dst_dir / src.name
        if dst.exists():
            if not force and dst.stat().st_mtime >= src.stat().st_mtime:
                actions.append(f"kept    {dst}  (newer or same)")
                continue
        actions.append(f"copy    {dst}")
        if not dry:
            shutil.copy2(src, dst)
    return actions


def install_agents(dst_root: Path, force: bool, dry: bool,
                   user_agents: bool) -> list[str]:
    actions = []
    src = REPO / "agents" / "pliny-the-liberator.md"
    if not src.is_file():
        return [f"agent definition missing: {src}"]

    targets = [dst_root / ".claude" / "agents"]
    if user_agents:
        targets.append(Path.home() / ".claude" / "agents")

    for d in targets:
        dst = d / src.name
        actions.append(f"agent   {dst}")
        if not dry:
            d.mkdir(parents=True, exist_ok=True)
            if dst.exists() and not force:
                actions[-1] += "  (exists — kept)"
                continue
            shutil.copy2(src, dst)
    return actions


def main() -> int:
    p = argparse.ArgumentParser(prog="install_tools", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="overwrite workshop copies even if they are newer")
    p.add_argument("--user-agents", action="store_true",
                   help="also install into ~/.claude/agents (outside the checkout)")
    p.add_argument("--workshop", help="override the workshop directory")
    args = p.parse_args()

    shop = Path(args.workshop).expanduser() if args.workshop else workshop()
    dry = args.dry_run

    print(f"workshop: {shop}")
    if not dry:
        shop.mkdir(parents=True, exist_ok=True)
        for rel in SKELETON:
            (shop / rel).mkdir(parents=True, exist_ok=True)
        for name, body in SEED_FILES.items():
            target = shop / name
            if not target.exists():
                target.write_text(body, encoding="utf-8")

    actions = copy_tree(SRC_TOOLS, shop / "tools", args.force, dry)
    actions += install_agents(shop, args.force, dry, args.user_agents)

    # research-pipeline is resolved from the workshop at preset time, so it has
    # to be seeded there too.
    src_pipeline = REPO / "research-pipeline"
    if src_pipeline.is_dir():
        actions += copy_tree(src_pipeline, shop / "research-pipeline",
                             args.force, dry)

    # Marker: lets tools run with cwd=workshop still find the install dir.
    marker = shop / "tools" / ".pliny_home"
    if not dry:
        marker.write_text(str(REPO), encoding="utf-8")
    actions.append(f"marker  {marker} -> {REPO}")

    for line in actions:
        print("  " + line)

    copied = sum(1 for a in actions if a.startswith(("copy", "agent")))
    print(f"\n{'(dry run) ' if dry else ''}{copied} item(s) "
          f"{'would be ' if dry else ''}installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
