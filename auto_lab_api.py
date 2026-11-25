#!/usr/bin/env python3
"""auto_lab_api — dashboard bridge for autonomous_attacker.py.

Same contract as lab_api / pyrit_lab_api: the cockpit gets a small HTTP
surface around a CLI tool; campaigns stay subprocesses so a hung target call
cannot take the server down. Operator-only start/stop (spending a target's
quota is an operator action); reads stay open to the dashboard.

Deliberately does not import autonomous_attacker: that module pulls in
jailbreak_lab, which replaces rt_send.post_to_dashboard with a no-op at
import time, and the server still needs the real one.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import agent_secrets

ROOT = Path(__file__).resolve().parent
AUTO_SCRIPT = ROOT / "autonomous_attacker.py"
RUNS_ROOT = ROOT / "state" / "jailbreak_lab" / "auto"
TROPHY_ROOT = ROOT / "state" / "jailbreak_lab" / "trophies"
BEHAVIORS_PRIMARY = ROOT / "rt_library" / "gauntlets" / "behaviors" / "north_stars.yml"
BEHAVIORS_FALLBACK = ROOT / "setup" / "starter_rt_library" / "gauntlets" / "behaviors" / "north_stars.yml"

_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")
_TROPHY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}\.md$")

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}

RUNS_ROOT.mkdir(parents=True, exist_ok=True)
TROPHY_ROOT.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4_hex()


def uuid4_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:6]


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(int(value), high))
    except Exception:
        return fallback


def _watch(run_id: str, proc: subprocess.Popen) -> None:
    try:
        returncode = proc.wait()
    except Exception:
        returncode = -1
    with _lock:
        _procs.pop(run_id, None)
    # The provider keys existed for this run only.
    agent_secrets.remove_session_key_file(run_id)
    meta_path = _run_dir(run_id) / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({
        "status": "finished" if returncode == 0 else "error",
        "returncode": returncode,
        "ended_at": _now(),
    })
    _write_json(meta_path, meta)


def start_run(payload: dict) -> dict:
    """Spawn one autonomous_attacker campaign as a scoped subprocess."""
    target = str(payload.get("target") or "").strip()
    attacker = str(payload.get("attacker") or "").strip()
    behavior = str(payload.get("behavior") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()

    if not _MODEL_RE.fullmatch(target):
        raise ValueError("target must be PROVIDER/MODEL")
    if not _MODEL_RE.fullmatch(attacker):
        raise ValueError("attacker must be PROVIDER/MODEL")
    target_provider, target_model = target.split("/", 1)
    attacker_provider, attacker_model = attacker.split("/", 1)
    if not behavior and not prompt:
        raise ValueError("behavior or prompt is required")

    max_turns = _clamp(payload.get("max_turns"), 1, 12, 6)
    reps = _clamp(payload.get("reps"), 1, 5, 1)
    timeout = _clamp(payload.get("timeout"), 30, 1800, 300)
    spell_intensity = str(payload.get("spell_intensity") or "medium").strip().lower()
    if spell_intensity not in ("light", "medium", "heavy"):
        raise ValueError("spell_intensity must be light|medium|heavy")
    stop_on_crack = bool(payload.get("stop_on_crack", True))
    stop_after_crack = bool(payload.get("stop_after_crack", False))
    no_judge = not bool(payload.get("judge", True))

    if not AUTO_SCRIPT.is_file():
        raise ValueError("autonomous_attacker.py not found")

    run_id = str(payload.get("run_id") or "").strip() or _new_run_id()
    if not _valid_run_id(run_id):
        raise ValueError("invalid run id")
    run_dir = _run_dir(run_id)
    (run_dir / "prompts").mkdir(parents=True, exist_ok=True)

    # The target and the attacker may bill different providers; the judge is
    # a separate transport (OpenRouter). One unioned 0600 key file covers all
    # three without dragging in the rest of the vault.
    key_names: list[str] = []
    for provider in (target_provider, attacker_provider):
        for name in agent_secrets.provider_key_names(provider):
            if name not in key_names:
                key_names.append(name)
    if not no_judge and "OPENROUTER_API_KEY" not in key_names:
        key_names.append("OPENROUTER_API_KEY")

    config = {
        "target": target,
        "attacker": attacker,
        "behavior": behavior,
        "prompt_chars": len(prompt),
        "max_turns": max_turns,
        "reps": reps,
        "spell_intensity": spell_intensity,
        "stop_on_crack": stop_on_crack,
        "stop_after_crack": stop_after_crack,
        "judge": not no_judge,
        "timeout": timeout,
        "key_scope": list(key_names),
    }
    _write_json(run_dir / "meta.json", {
        "run_id": run_id,
        "kind": "autonomous_attacker",
        "status": "starting",
        "config": config,
        "pid": None,
        "started_at": _now(),
    })

    argv = [
        sys.executable, "-X", "utf8", str(AUTO_SCRIPT), "run",
        "--target", target,
        "--attacker", attacker,
        "--behavior", behavior,
        "--max-turns", str(max_turns),
        "--reps", str(reps),
        "--spell-intensity", spell_intensity,
        "--run-id", run_id,
        "--timeout", str(timeout),
    ]
    if prompt:
        payload_path = run_dir / "payload.txt"
        payload_path.write_text(prompt, encoding="utf-8")
        argv += ["--prompt-file", str(payload_path)]
    if stop_on_crack:
        argv += ["--stop-on-crack"]
    if stop_after_crack:
        argv += ["--stop-after-crack"]
    if no_judge:
        argv += ["--no-judge"]

    # Scoped environment: configuration in, credentials out. The run's own
    # provider keys arrive through a per-run 0600 key file; the attacker
    # subprocess must not re-read .env on top of it (jailbreak_lab honours
    # PLINY_LAB_SCOPED_ENV) and must not get the agent token.
    key_path = agent_secrets.write_session_key_file(run_id, names=key_names)
    env = agent_secrets.filtered_env(extra={
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PLINY_LAB_SCOPED_ENV": "1",
    })
    env.pop("PLINY_AGENT_TOKEN", None)
    if key_path is not None:
        env[agent_secrets.KEY_FILE_ENV] = str(key_path)

    log_path = run_dir / "console.log"
    log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **_process_options(),
        )
    except Exception:
        # The run never started; do not leave its provider keys behind.
        agent_secrets.remove_session_key_file(run_id)
        raise
    finally:
        log_handle.close()

    with _lock:
        _procs[run_id] = proc

    meta_path = run_dir / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({"status": "running", "pid": proc.pid})
    _write_json(meta_path, meta)
    threading.Thread(target=_watch, args=(run_id, proc), daemon=True,
                     name=f"auto-watch-{run_id}").start()

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
    if run_dir.is_dir():
        meta_path = run_dir / "meta.json"
        meta = _read_json(meta_path, {}) or {}
        meta.update({"status": "stopped", "ended_at": _now()})
        _write_json(meta_path, meta)
    return {"run_id": run_id, "status": "stopped"}


def _compact_attempt(record: dict) -> dict:
    carrier = record.get("carrier") or {}
    judge = record.get("judge") or {}
    return {
        "n": record.get("n"),
        "campaign": record.get("campaign", ""),
        "rep": record.get("rep"),
        "turn": record.get("turn"),
        "ts": record.get("ts"),
        "carrier": carrier.get("label", ""),
        "noop": bool(carrier.get("noop")),
        "prompt_changed": bool(record.get("prompt_changed")),
        "score": judge.get("score"),
        "verdict": record.get("verdict", ""),
        "error": record.get("error", ""),
        "response_chars": len(record.get("response") or ""),
        "latency_ms": record.get("latency_ms", 0),
    }


def _attempts(run_dir: Path) -> list[dict]:
    path = run_dir / "attempts.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_compact_attempt(json.loads(line)))
                except Exception:
                    continue
    except Exception:
        return out
    return out[-200:]


def run_status(run_id: str) -> Optional[dict]:
    try:
        run_dir = _run_dir(run_id)
    except ValueError:
        return None
    if not run_dir.is_dir():
        return None

    meta = _read_json(run_dir / "meta.json", {}) or {}
    with _lock:
        proc = _procs.get(run_id)
    alive = bool(proc is not None and proc.poll() is None)
    status = meta.get("status", "unknown")
    if alive:
        status = "running"
    elif proc is not None and proc.returncode is not None and status in ("starting", "running"):
        status = "finished" if proc.returncode == 0 else "error"
        meta.update({
            "status": status,
            "returncode": proc.returncode,
            "ended_at": meta.get("ended_at") or _now(),
        })
        _write_json(run_dir / "meta.json", meta)
    elif status == "running":
        # The process handle is gone (server restart). Attempts on disk are
        # the honest signal: some turns ran, else the run is orphaned.
        status = "finished" if _attempts(run_dir) else "orphaned"
        meta["status"] = status
        _write_json(run_dir / "meta.json", meta)

    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    # CLI-launched campaigns have no meta.json at all; attempts on disk are
    # the honest signal that something ran.
    if status in ("unknown", "starting") and not alive:
        status = "finished" if _attempts(run_dir) else "unknown"
    trophies = sorted(
        [p.name for p in TROPHY_ROOT.glob(f"{run_id}_*.md")],
        reverse=True,
    )
    return {
        "run_id": run_id,
        "status": status,
        "alive": alive,
        "meta": meta,
        "config": meta.get("config") or {},
        "manifest": manifest,
        "attempts": _attempts(run_dir),
        "console_tail": _tail(run_dir / "console.log", 80),
        "trophies": trophies,
    }


def attempt_detail(run_id: str, n: int) -> Optional[dict]:
    try:
        run_dir = _run_dir(run_id)
    except ValueError:
        return None
    path = run_dir / "attempts.jsonl"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if int(record.get("n", -1)) == int(n):
                    return record
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
        meta = _read_json(run_dir / "meta.json", {}) or {}
        manifest = _read_json(run_dir / "manifest.json", {}) or {}
        if not meta and not manifest:
            continue
        target = manifest.get("target") or {}
        attacker = manifest.get("attacker") or {}
        behavior = manifest.get("behavior") or {}
        attempts = _attempts(run_dir)
        cracked = sum(1 for a in attempts if a.get("verdict") == "compliant")
        rows.append({
            "run_id": run_dir.name,
            "status": meta.get("status") or "unknown",
            "started_at": meta.get("started_at") or manifest.get("created", ""),
            "target": target.get("provider", "") + "/" + target.get("model", ""),
            "attacker": attacker.get("provider", "") + "/" + attacker.get("model", ""),
            "behavior": behavior.get("id") or (meta.get("config") or {}).get("behavior", ""),
            "attempts": len(attempts),
            "cracks": cracked,
            "trophies": len(list(TROPHY_ROOT.glob(f"{run_dir.name}_*.md"))),
            "mtime": run_dir.stat().st_mtime,
        })
    rows.sort(key=lambda r: (r.get("mtime") or 0, r.get("started_at") or ""), reverse=True)
    for row in rows:
        row.pop("mtime", None)
    return rows[: max(1, min(limit, 100))]
