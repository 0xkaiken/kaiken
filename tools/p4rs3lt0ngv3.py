#!/usr/bin/env python3
"""Workshop entry point for the P4RS3LT0NGV3 sidecar bridge.

The bridge module lives at the install root (``p4rs3lt0ngv3_bridge.py``)
because ``parseltongue`` and the lab import it directly.  This wrapper makes
the same CLI available from ``$PLINY_WORKSHOP/tools/``.

    python tools/p4rs3lt0ngv3.py status
    python tools/p4rs3lt0ngv3.py sync
    python tools/p4rs3lt0ngv3.py list --category concealment
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from _paths import pliny_home

    INSTALL_DIR = pliny_home()
except Exception:  # noqa: BLE001 - path bootstrap is best-effort
    INSTALL_DIR = HERE.parent

sys.path.insert(0, str(INSTALL_DIR))

try:
    from p4rs3lt0ngv3_bridge import main
except Exception as exc:  # noqa: BLE001 - report any import failure  # pragma: no cover
    print(f"error: cannot import p4rs3lt0ngv3_bridge from {INSTALL_DIR}: {exc}",
          file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
