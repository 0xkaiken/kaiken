"""Audit: every /api/ path the UI calls must exist in server.py.

Dead panels and 404s in the dashboard are invisible until a session needs them,
which is the worst possible time to find out. This walks the inline JS/HTML in
index.html (and the side pages) and reports any API leaf the server never
mentions, plus any path that is called but only handled in a method the caller
does not use.

Run: python state/workshop/tools/api_surface_audit.py
"""
import re
import sys
from pathlib import Path

# This file is installed into <workshop>/tools/ as well as living in the repo's
# tools/. parents[3] is only the install dir from one of those two; _paths
# resolves it from PLINY_HOME, the .pliny_home marker, or a server.py search.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import pliny_home  # noqa: E402

BASE = pliny_home()
PAGES = ["index.html", "redteam.html", "arcade.html", "lab.html"]
# Routes live in several modules (server.py, lab_api.py, computer_use.py ...),
# so the audit reads the whole Python surface, not just the main server.
SERVER = "\n".join(
    p.read_text(encoding="utf-8", errors="replace")
    for p in sorted(BASE.glob("*.py"))
)


def leaves(page: Path):
    text = page.read_text(encoding="utf-8", errors="replace")
    raw = set(re.findall(r"/api/[A-Za-z0-9_./{}-]+", text))
    out = {}
    for path in raw:
        clean = path.split("?")[0].rstrip("/")
        leaf = clean[len("/api/"):]
        # template literals / concatenations cannot be resolved statically
        if not leaf or "${" in leaf or leaf.endswith("/"):
            continue
        out.setdefault(leaf, path)
    return out


def main():
    server = SERVER
    # Prefix routes: `path.startswith("/api/desktop/")` registers a whole
    # subtree, so a leaf under it is served even though the literal string
    # never appears. Without this the audit cries wolf on every multiplexed
    # family of endpoints.
    prefixes = set(re.findall(r"startswith\(\s*[\"'](/api/[A-Za-z0-9_./-]+)", server))
    total_missing = 0
    for name in PAGES:
        page = BASE / name
        if not page.exists():
            continue
        called = leaves(page)
        missing = []
        for leaf in called:
            if leaf in server:
                continue
            path = "/api/" + leaf
            if any(path.startswith(p) for p in prefixes):
                continue
            missing.append(leaf)
        missing = sorted(missing)
        print("%-14s %3d api leaves, %d missing%s" % (
            name, len(called), len(missing),
            ("  (prefix routes: %d)" % len(prefixes)) if prefixes else ""))
        for leaf in missing:
            print("   MISSING  /api/%s" % leaf)
        total_missing += len(missing)
    print("\ntotal missing: %d" % total_missing)
    return 1 if total_missing else 0


if __name__ == "__main__":
    sys.exit(main())
