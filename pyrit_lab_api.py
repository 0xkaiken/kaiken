#!/usr/bin/env python3
"""pyrit_lab_api — dashboard bridge for the PyRIT sidecar organ.

Mirrors lab_api.py: the campaign stays a standalone CLI subprocess, and the
cockpit gets a small HTTP surface around it — start, stop, poll turns, read the
result. Nothing is imported from the sidecar's own venv here; it runs as a
process so a slow or crashed campaign cannot take the server down.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import agent_secrets

ROOT = Path(__file__).resolve().parent
PYRIT_DIR = ROOT / "organs" / "pyrit"
RUN_SCRIPT = PYRIT_DIR / "run_campaign.py"
VENV_PYTHON = PYRIT_DIR / ".venv" / "Scripts" / "python.exe"
RUNS_ROOT = ROOT / "state" / "jailbreak_lab" / "pyrit"

_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}

RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _python() -> Path:
    return VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id and _RUN_RE.fullmatch(run_id))


def _run_dir(run_id: str) -> Path:
    if not _valid_run_id(run_id):
        raise ValueError("invalid run id")
    return RUNS_ROOT / run_id


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _tail(path: Path, lines: int = 80, limit: int = 200_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(text) > limit:
        text = text[-limit:]
    return "\n".join(text.splitlines()[-lines:])


def _process_options() -> dict:
    try:
        import process_control
        return process_control.process_group_options()
    except Exception:
        return {}


def _new_run_id() -> str:
    return "ui-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except Exception:
        return fallback
    return max(low, min(number, high))


def _watch(run_id: str, proc: subprocess.Popen) -> None:
    try:
        returncode = proc.wait()
    except Exception:
        returncode = -1
    with _lock:
        _procs.pop(run_id, None)
    meta_path = _run_dir(run_id) / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({
        "status": "finished" if returncode == 0 else "error",
        "returncode": returncode,
        "ended_at": _now(),
    })
    _write_json(meta_path, meta)


def start_run(payload: dict) -> dict:
    objective = str(payload.get("objective") or "").strip()
    target = str(payload.get("target") or "opencode-go/glm-5.3-flash").strip()
    attacker = str(payload.get("attacker") or "opencode-go/deepseek-v4.1-flash").strip()
    attack = str(payload.get("attack") or "crescendo").strip().lower()
    scorer = str(payload.get("scorer") or "substring").strip().lower()
    substring = str(payload.get("substring") or "").strip()

    if not objective:
        raise ValueError("objective is required")
    if len(objective) > 4000:
        raise ValueError("objective is too long (max 4000 chars)")
    for label, value in (("target", target), ("attacker", attacker)):
        if not _MODEL_RE.fullmatch(value):
            raise ValueError(f"invalid {label} model id")
    if attack not in ("crescendo", "redteam"):
        raise ValueError("attack must be crescendo or redteam")
    if scorer not in ("substring", "selfask"):
        raise ValueError("scorer must be substring or selfask")
    if scorer == "substring" and not substring:
        raise ValueError("substring is required for the substring scorer")
    if len(substring) > 500:
        raise ValueError("substring is too long (max 500 chars)")

    max_turns = _clamp(payload.get("max_turns"), 1, 10, 5)
    max_backtracks = _clamp(payload.get("max_backtracks"), 0, 20, 5)
    timeout = _clamp(payload.get("timeout"), 30, 1800, 300)

    if not RUN_SCRIPT.is_file():
        raise ValueError("run_campaign.py not found in organs/pyrit")

    run_id = str(payload.get("run_id") or "").strip() or _new_run_id()
    if not _valid_run_id(run_id):
        raise ValueError("invalid run id")
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "objective": objective,
        "target": target,
        "attacker": attacker,
        "attack": attack,
        "scorer": scorer,
        "substring": substring,
        "max_turns": max_turns,
        "max_backtracks": max_backtracks,
        "timeout": timeout,
    }
    _write_json(run_dir / "meta.json", {
        "run_id": run_id,
        "status": "starting",
        "config": config,
        "pid": None,
        "started_at": _now(),
    })

    argv = [
        str(_python()), "-X", "utf8", str(RUN_SCRIPT),
        "--objective", objective,
        "--target", target,
        "--attacker", attacker,
        "--attack", attack,
        "--scorer", scorer,
        "--max-turns", str(max_turns),
        "--max-backtracks", str(max_backtracks),
        "--timeout", str(timeout),
        "--run-id", run_id,
        "--out", str(RUNS_ROOT),
    ]
    if scorer == "substring":
        argv += ["--substring", substring]

    env = agent_secrets.filtered_env(extra={
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("PLINY_AGENT_TOKEN", None)
    log_path = run_dir / "console.log"
    log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(PYRIT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **_process_options(),
        )
    finally:
        log_handle.close()

    with _lock:
        _procs[run_id] = proc

    meta_path = run_dir / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({"status": "running", "pid": proc.pid})
    _write_json(meta_path, meta)
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True,
                     name=f"pyrit-watch-{run_id}").start()

    return {"run_id": run_id, "status": "running", "pid": proc.pid}


def stop_run(run_id: str) -> dict:
    run_dir = _run_dir(run_id)
    with _lock:
        proc = _procs.get(run_id)
    if proc is not None and proc.poll() is None:
        try:
            import process_control
            process_control.stop_process_tree(proc, force=False)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process_control.stop_process_tree(proc, force=True)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        with _lock:
            _procs.pop(run_id, None)
    meta_path = run_dir / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({"status": "stopped", "ended_at": _now()})
    _write_json(meta_path, meta)
    return {"run_id": run_id, "status": "stopped"}


def _attempts(run_dir: Path) -> list[dict]:
    path = run_dir / "attempts.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                response = record.get("response") or ""
                prompt = record.get("prompt") or ""
                rows.append({
                    "index": index,
                    "label": record.get("label", ""),
                    "turn": record.get("turn", 0),
                    "model": record.get("model", ""),
                    "ts": record.get("ts", ""),
                    "latency_ms": record.get("latency_ms", 0),
                    "prompt_chars": len(prompt),
                    "response_chars": len(response),
                    "preview": " ".join(response.split())[:180],
                })
    except Exception:
        return rows
    return rows[-300:]


def _result(run_dir: Path) -> Optional[dict]:
    raw = _read_json(run_dir / "result.json", None)
    if not isinstance(raw, dict):
        return None
    score = raw.get("last_score") or {}
    last = raw.get("last_response") or {}
    return {
        "outcome": raw.get("outcome", ""),
        "outcome_reason": raw.get("outcome_reason", ""),
        "executed_turns": raw.get("executed_turns", 0),
        "execution_time_ms": raw.get("execution_time_ms", 0),
        "last_score": score.get("score_value"),
        "last_score_rationale": score.get("score_rationale", ""),
        "last_response": last.get("converted_value") or last.get("original_value") or "",
        "error_message": raw.get("error_message", ""),
    }


def _status_of(run_dir: Path, meta: dict) -> str:
    with _lock:
        proc = _procs.get(run_dir.name)
    if proc is not None and proc.poll() is None:
        return "running"
    if _result(run_dir) is not None:
        return "finished"
    status = str(meta.get("status") or "")
    if status == "running":
        return "orphaned"
    return status or "unknown"


def run_status(run_id: str) -> Optional[dict]:
    try:
        run_dir = _run_dir(run_id)
    except ValueError:
        return None
    if not run_dir.is_dir():
        return None
    meta = _read_json(run_dir / "meta.json", {}) or {}
    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    status = _status_of(run_dir, meta)
    if status == "finished" and meta.get("status") in ("starting", "running"):
        meta.update({"status": "finished", "ended_at": meta.get("ended_at") or _now()})
        _write_json(run_dir / "meta.json", meta)
    return {
        "run_id": run_id,
        "status": status,
        "alive": status == "running",
        "meta": meta,
        "config": meta.get("config") or {},
        "manifest": manifest,
        "attempts": _attempts(run_dir),
        "result": _result(run_dir),
        "console_tail": _tail(run_dir / "console.log", 80),
    }


def attempt_detail(run_id: str, index: int) -> Optional[dict]:
    try:
        run_dir = _run_dir(run_id)
    except ValueError:
        return None
    path = run_dir / "attempts.jsonl"
    if not path.is_file() or index < 0:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for position, line in enumerate(handle):
                if position != index:
                    continue
                line = line.strip()
                return json.loads(line) if line else None
    except Exception:
        return None
    return None


def list_runs(limit: int = 30) -> list[dict]:
    rows: list[dict] = []
    try:
        dirs = [p for p in RUNS_ROOT.iterdir() if p.is_dir() and _valid_run_id(p.name)]
    except Exception:
        return rows
    for run_dir in dirs:
        manifest = _read_json(run_dir / "manifest.json", {}) or {}
        meta = _read_json(run_dir / "meta.json", {}) or {}
        result = _result(run_dir)
        if not manifest and not meta:
            continue
        rows.append({
            "run_id": run_dir.name,
            "status": _status_of(run_dir, meta),
            "objective": manifest.get("objective") or (meta.get("config") or {}).get("objective", ""),
            "target": manifest.get("target") or (meta.get("config") or {}).get("target", ""),
            "attacker": manifest.get("attacker") or (meta.get("config") or {}).get("attacker", ""),
            "attack": manifest.get("attack") or (meta.get("config") or {}).get("attack", ""),
            "outcome": (result or {}).get("outcome", ""),
            "executed_turns": (result or {}).get("executed_turns", ""),
            "started_at": manifest.get("created_at") or meta.get("started_at", ""),
            "mtime": run_dir.stat().st_mtime,
        })
    rows.sort(key=lambda r: (r.get("mtime") or 0, r.get("started_at") or ""), reverse=True)
    for row in rows:
        row.pop("mtime", None)
    return rows[: max(1, min(limit, 100))]
