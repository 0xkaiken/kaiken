#!/usr/bin/env python3
"""Shared path resolution for workshop tools.

Two directories matter and they are not the same:

``PLINY_HOME``      the installed repo — where ``server.py``, ``state/`` and
                    ``rt_library/`` live.
``PLINY_WORKSHOP``  the operator's writable surface — where ``tools/``, ``memory/``,
                    ``experiment/`` and the published archives live.

The dashboard runs job scripts with ``cwd`` set to the workshop, so
``Path(__file__).parent`` is *not* a reliable way to find the install dir. The
resolution order below is explicit, and ``install_tools.py`` leaves a
``.pliny_home`` marker in ``tools/`` so the indirection always resolves.

Environment always wins, so a relocated checkout can override everything.
"""
from __future__ import annotations

import os
from pathlib import Path

MARKER = ".pliny_home"


def workshop() -> Path:
    env = os.environ.get("PLINY_WORKSHOP")
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parent.parent
    return root / "state" / "workshop" if (root / "server.py").is_file() else root


def pliny_home() -> Path:
    env = os.environ.get("PLINY_HOME")
    if env:
        return Path(env).expanduser().resolve()

    here = Path(__file__).resolve().parent
    for marker_dir in (here, workshop() / "tools"):
        marker = marker_dir / MARKER
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if recorded and Path(recorded).is_dir():
            return Path(recorded).resolve()

    for candidate in (here.parent, here, Path.cwd()):
        if (candidate / "server.py").is_file():
            return candidate.resolve()
    return here.parent


def state_dir() -> Path:
    return pliny_home() / "state"


def library_dir() -> Path:
    return pliny_home() / "rt_library"
