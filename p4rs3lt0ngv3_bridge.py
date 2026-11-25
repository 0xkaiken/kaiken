#!/usr/bin/env python3
"""P4RS3LT0NGV3 sidecar bridge — the 4.0 transform catalogue, without a re-port.

The upstream project (github.com/elder-plinius/P4RS3LT0NGV3) ships 222 text
transforms as ES modules and a small Node CLI bridge.  This module runs that
bridge as a separate process and returns its JSON, so Libertarium gets the new
catalogue without vendoring AGPL source into this tree and without rewriting
222 transforms in Python.

The checkout is an *organ*: a separate git clone, updated with ``sync``.

    python p4rs3lt0ngv3_bridge.py status
    python p4rs3lt0ngv3_bridge.py sync            # clone into organs/p4rs3lt0ngv3
    python p4rs3lt0ngv3_bridge.py update          # git pull --ff-only
    python p4rs3lt0ngv3_bridge.py list --category concealment
    python p4rs3lt0ngv3_bridge.py encode acere_cipher --text "hello"
    python p4rs3lt0ngv3_bridge.py decode base64 --text "aGVsbG8="
    python p4rs3lt0ngv3_bridge.py auto-decode --text "aGVsbG8="

Resolution order for the checkout:

1. ``PLINY_P4RS3LT0NGV3_DIR`` (explicit operator override; invalid = unavailable)
2. ``<repo>/organs/p4rs3lt0ngv3``

``PLINY_P4RS_DISABLE=1`` turns the sidecar off even when a checkout is present.
The native 94 techniques in ``parseltongue.py`` never depend on this module.

Windows note: the upstream Python CLI uses ``subprocess.run(text=True)`` without
an encoding, which decodes Node's UTF-8 output as cp1254 on a Turkish Windows
console and crashes.  This bridge passes ``encoding="utf-8"`` explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = ROOT / "organs" / "p4rs3lt0ngv3"
REPO_URL = "https://github.com/elder-plinius/P4RS3LT0NGV3"
ENV_DIR = "PLINY_P4RS3LT0NGV3_DIR"
ENV_DISABLE = "PLINY_P4RS_DISABLE"
ENV_TIMEOUT = "PLINY_P4RS_NODE_TIMEOUT"
BRIDGE_REL = Path("scripts") / "cli_bridge.js"
MIN_NODE_MAJOR = 18


class BridgeError(RuntimeError):
    """The sidecar is missing, disabled, or the Node bridge failed."""


# ---------------------------------------------------------------------------
#  Discovery
# ---------------------------------------------------------------------------

def disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() in {"1", "true", "yes", "on"}


def candidate_roots() -> list[Path]:
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        # An explicit override is authoritative: never silently fall back to
        # the repo-local checkout when the operator pointed somewhere else.
        return [Path(env).expanduser()]
    return [DEFAULT_DIR]


def find_root() -> Path | None:
    if disabled():
        return None
    for candidate in candidate_roots():
        try:
            if (candidate / BRIDGE_REL).is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def node_binary() -> str | None:
    return shutil.which("node")


def node_version() -> str | None:
    node = node_binary()
    if not node:
        return None
    try:
        proc = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def node_major() -> int | None:
    version = node_version()
    if not version:
        return None
    try:
        return int(version.lstrip("v").split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def available() -> bool:
    if find_root() is None or node_binary() is None:
        return False
    major = node_major()
    return major is None or major >= MIN_NODE_MAJOR


def unavailable_reason() -> str:
    """A short, non-probing reason the sidecar cannot be used."""
    if disabled():
        return f"disabled by {ENV_DISABLE}"
    root = find_root()
    if root is None:
        env = os.environ.get(ENV_DIR, "").strip()
        where = env or str(DEFAULT_DIR)
        return f"checkout not found at {where} (run: python p4rs3lt0ngv3_bridge.py sync)"
    if node_binary() is None:
        return "node not found on PATH (Node 18+ required)"
    major = node_major()
    if major is not None and major < MIN_NODE_MAJOR:
        return f"node {major} is too old (need {MIN_NODE_MAJOR}+)"
    return ""


def _timeout() -> float:
    raw = os.environ.get(ENV_TIMEOUT, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return 30.0


# ---------------------------------------------------------------------------
#  Node bridge
# ---------------------------------------------------------------------------

def _run(payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    root = find_root()
    if root is None:
        raise BridgeError(unavailable_reason())
    node = node_binary()
    if node is None:
        raise BridgeError(unavailable_reason())

    try:
        proc = subprocess.run(
            [node, str(root / BRIDGE_REL)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(root),
            timeout=timeout or _timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Node bridge timed out after {exc.timeout}s") from exc
    except OSError as exc:
        raise BridgeError(f"could not start node: {exc}") from exc

    stdout = proc.stdout or ""
    stderr = (proc.stderr or "").strip()
    lines = [line for line in stdout.splitlines() if line.strip()]
    data: dict[str, Any] = {}
    if lines:
        try:
            parsed = json.loads(lines[-1])
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError as exc:
            raise BridgeError(
                f"Node bridge returned invalid JSON: {lines[-1][:300]}"
            ) from exc

    if proc.returncode != 0 or not data.get("ok"):
        message = str(data.get("error") or stderr or "Node bridge failed")
        raise BridgeError(message)
    return data


_catalog_cache: list[dict[str, Any]] | None = None


def clear_cache() -> None:
    global _catalog_cache
    _catalog_cache = None


def list_transforms(force: bool = False) -> list[dict[str, Any]]:
    """Return the upstream catalogue (cached per process)."""
    global _catalog_cache
    if _catalog_cache is not None and not force:
        return [dict(item) for item in _catalog_cache]

    data = _run({"command": "list"})
    items = data.get("transforms")
    if not isinstance(items, list):
        raise BridgeError("Node bridge returned no transform list")
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        cleaned.append({
            "key": key,
            "name": str(item.get("name") or key),
            "category": str(item.get("category") or "other"),
            "priority": item.get("priority", 0),
            "can_decode": bool(item.get("canDecode")),
            "description": str(item.get("description") or ""),
            "input_kind": str(item.get("inputKind") or "textarea"),
            "options": item.get("configurableOptions") or [],
        })
    cleaned.sort(key=lambda entry: (entry["category"], entry["key"]))
    _catalog_cache = cleaned
    return [dict(item) for item in cleaned]


def normalize_transform_key(name: str) -> str:
    """Accept the registry spelling (`p4_base64`) and upstream camelCase."""
    key = str(name or "").strip()
    if key.startswith(("p4_", "p4.")):
        key = key[3:]
    items = list_transforms()
    for item in items:
        if item["key"] == key:
            return key
    lowered = key.lower()
    for item in items:
        if item["key"].lower() == lowered:
            return item["key"]
    return key


def inspect_transform(transform_key: str) -> dict[str, Any]:
    wanted = normalize_transform_key(transform_key)
    for item in list_transforms():
        if item["key"] == wanted:
            return item
    raise BridgeError(f"Unknown transform: {transform_key}")


def run_transform(
    transform_key: str,
    text: str,
    action: str = "encode",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in {"encode", "decode", "preview"}:
        raise BridgeError(f"Unsupported action: {action}")
    key = normalize_transform_key(transform_key)
    data = _run({
        "command": "run",
        "action": action,
        "transform": key,
        "text": text,
        "options": options or {},
    })
    output = data.get("output")
    if not isinstance(output, str):
        output = "" if output is None else str(output)
    return {
        "action": data.get("action", action),
        "transform": data.get("transform", transform_key),
        "name": data.get("name", transform_key),
        "options": data.get("options") or {},
        "output": output,
    }


def auto_decode(text: str) -> dict[str, Any] | None:
    data = _run({"command": "auto-decode", "text": text})
    result = data.get("result")
    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
#  Sidecar lifecycle
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeError(f"git is unavailable: {exc}") from exc


def git_commit(root: Path | None = None) -> str | None:
    root = root or find_root()
    if root is None or not (root / ".git").exists():
        return None
    proc = _git(["-C", str(root), "rev-parse", "--short", "HEAD"])
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def package_version(root: Path | None = None) -> str | None:
    root = root or find_root()
    if root is None:
        return None
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    version = data.get("version")
    return str(version) if version else None


def _target_dir(directory: str | os.PathLike[str] | None = None) -> Path:
    if directory:
        return Path(directory).expanduser().resolve()
    env = os.environ.get(ENV_DIR, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DIR.resolve()


def sync(directory: str | os.PathLike[str] | None = None, update: bool = False) -> dict[str, Any]:
    """Clone the sidecar, or fast-forward an existing checkout.

    ``update=False`` is the install path: an existing checkout is left alone.
    ``update=True`` is the upgrade path: ``git pull --ff-only``.
    """
    target = _target_dir(directory)
    if target.exists():
        if not (target / ".git").is_dir():
            raise BridgeError(
                f"{target} exists but is not a git checkout; move it aside first"
            )
        if not update:
            if not (target / BRIDGE_REL).is_file():
                raise BridgeError(f"{target} is missing {BRIDGE_REL}")
            return {
                "action": "present",
                "root": str(target),
                "commit": git_commit(target),
                "version": package_version(target),
            }
        before = git_commit(target)
        proc = _git(["-C", str(target), "pull", "--ff-only"])
        if proc.returncode != 0:
            raise BridgeError((proc.stderr or proc.stdout or "git pull failed").strip())
        after = git_commit(target)
        clear_cache()
        return {
            "action": "updated",
            "root": str(target),
            "before": before,
            "after": after,
            "version": package_version(target),
            "changed": before != after,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    proc = _git(["clone", REPO_URL, str(target)])
    if proc.returncode != 0:
        raise BridgeError((proc.stderr or proc.stdout or "git clone failed").strip())
    if not (target / BRIDGE_REL).is_file():
        raise BridgeError(f"clone succeeded but {BRIDGE_REL} is missing")
    clear_cache()
    return {
        "action": "cloned",
        "root": str(target),
        "commit": git_commit(target),
        "version": package_version(target),
    }


def status(probe: bool = True) -> dict[str, Any]:
    root = find_root()
    node = node_binary()
    info: dict[str, Any] = {
        "available": False,
        "disabled": disabled(),
        "root": str(root) if root else None,
        "default_root": str(DEFAULT_DIR),
        "env_var": ENV_DIR,
        "node": node,
        "node_version": node_version(),
        "commit": git_commit(root) if root else None,
        "version": package_version(root) if root else None,
        "transform_count": None,
        "error": "",
    }
    if disabled():
        info["error"] = f"disabled by {ENV_DISABLE}"
        return info
    if root is None:
        info["error"] = unavailable_reason()
        return info
    if node is None:
        info["error"] = unavailable_reason()
        return info
    info["available"] = True
    if probe:
        try:
            info["transform_count"] = len(list_transforms(force=True))
        except BridgeError as exc:
            info["available"] = False
            info["error"] = str(exc)
    return info


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def _coerce(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_options(pairs: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise BridgeError(f"invalid option {pair!r}; expected key=value")
        key, value = pair.split("=", 1)
        options[key.strip()] = _coerce(value)
    return options


def _read_text(value: str | None) -> str:
    if value is not None:
        return value
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _emit(data: Any, as_json: bool) -> None:
    if as_json or not isinstance(data, str):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p4rs3lt0ngv3_bridge.py",
        description="Sidecar bridge to the P4RS3LT0NGV3 4.0 transform catalogue",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="Show checkout, Node and catalog status")
    status_parser.add_argument("--no-probe", action="store_true", help="Do not load the catalog")

    for name, help_text in (("sync", "Clone the sidecar if absent"), ("install", "Alias for sync")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--dir", help="Checkout directory (default: organs/p4rs3lt0ngv3)")
        cmd.add_argument("--json", action="store_true")

    update_parser = sub.add_parser("update", help="git pull --ff-only the sidecar")
    update_parser.add_argument("--dir", help="Checkout directory (default: organs/p4rs3lt0ngv3)")
    update_parser.add_argument("--json", action="store_true")

    list_parser = sub.add_parser("list", help="List transforms")
    list_parser.add_argument("--category")
    list_parser.add_argument("--json", action="store_true")

    inspect_parser = sub.add_parser("inspect", help="Inspect one transform")
    inspect_parser.add_argument("transform")
    inspect_parser.add_argument("--json", action="store_true")

    for name in ("encode", "decode", "preview"):
        cmd = sub.add_parser(name, help=f"{name.title()} text with one transform")
        cmd.add_argument("transform")
        cmd.add_argument("--text")
        cmd.add_argument("--option", action="append", default=[], help="key=value (repeatable)")
        cmd.add_argument("--json", action="store_true")

    auto_parser = sub.add_parser("auto-decode", help="Run the upstream universal decoder")
    auto_parser.add_argument("--text")
    auto_parser.add_argument("--json", action="store_true")

    return parser


def _utf8_console() -> None:
    """A Windows console defaults to cp1254 here; never crash on a transform."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)

    try:
        if args.command == "status":
            info = status(probe=not args.no_probe)
            _emit(info, True)
            return 0 if info["available"] else 1

        if args.command in {"sync", "install"}:
            result = sync(args.dir, update=False)
            _emit(result, args.json)
            return 0

        if args.command == "update":
            result = sync(args.dir, update=True)
            _emit(result, args.json)
            return 0

        if args.command == "list":
            items = list_transforms(force=True)
            if args.category:
                items = [item for item in items if item["category"] == args.category]
            if args.json:
                _emit(items, True)
            else:
                for item in items:
                    flag = "decode" if item["can_decode"] else "encode-only"
                    print(f"{item['key']:24} {item['category']:12} {flag:11} {item['name']}")
            return 0

        if args.command == "inspect":
            _emit(inspect_transform(args.transform), args.json)
            return 0

        if args.command in {"encode", "decode", "preview"}:
            text = _read_text(args.text)
            result = run_transform(
                args.transform,
                text,
                action=args.command,
                options=_parse_options(args.option),
            )
            _emit(result if args.json else result["output"], args.json)
            return 0

        if args.command == "auto-decode":
            text = _read_text(args.text)
            result = auto_decode(text)
            if result is None:
                print("no decoder matched", file=sys.stderr)
                return 1
            _emit(result, args.json)
            return 0

    except BridgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
