#!/usr/bin/env python3
"""lab_api — dashboard bridge for jailbreak_lab.py.

The lab stays a standalone CLI. This module gives the cockpit a small HTTP
surface around it: start a run, poll attempts, read the summary, open a trophy.
Runs are subprocesses, not in-process threads, so a crash or a long target call
cannot take the server down with it.

Deliberately does not import jailbreak_lab: that module replaces
rt_send.post_to_dashboard with a no-op at import time, and the server still
needs the real one.
"""
from __future__ import annotations

import json
import os
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
LAB_SCRIPT = ROOT / "jailbreak_lab.py"
RUNS_ROOT = ROOT / "state" / "jailbreak_lab" / "runs"
TROPHY_ROOT = ROOT / "state" / "jailbreak_lab" / "trophies"
BEHAVIORS_PRIMARY = ROOT / "rt_library" / "gauntlets" / "behaviors" / "north_stars.yml"
BEHAVIORS_FALLBACK = ROOT / "setup" / "starter_rt_library" / "gauntlets" / "behaviors" / "north_stars.yml"

_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_TROPHY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}\.md$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")

# `all-p4` expands to 178 names (~2.4k chars). The old 2k cap rejected the
# dashboard preset; the CLI argv limit is the real ceiling (~32k on Windows).
MAX_CARRIERS_CHARS = 20_000

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


def _write_json(path: Path, data: Any) -> None:
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


# ─── Catalog ─────────────────────────────────────────────────────────────────

def list_carriers(provider: str = "") -> dict:
    import parseltongue
    import spells

    categories = parseltongue.list_techniques()
    safe = set(parseltongue.safe_techniques(provider)) if provider else set()
    preferred = set(parseltongue.preferred_techniques(provider)) if provider else set()
    extended = set(parseltongue.extended_techniques(provider))
    extension = parseltongue.extension_info()
    spell_safe = set(spells.safe_techniques(provider))
    spell_preferred = set(spells.preferred_techniques(provider))
    seen: set[str] = set()
    techniques: list[dict] = [{
        "name": "plain",
        "category": "control",
        "safe": True,
        "preferred": False,
        "extended": False,
        "source": "native",
    }]
    for category, names in categories.items():
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            techniques.append({
                "name": name,
                "category": category,
                "safe": (
                    (not provider and not name.startswith(parseltongue.P4_PREFIX))
                    or name in safe
                ),
                "preferred": name in preferred,
                "extended": name in extended,
                "source": (
                    "p4rs3lt0ngv3"
                    if name.startswith(parseltongue.P4_PREFIX)
                    else "native"
                ),
            })
    # The spell family (persona/CoT/rebuttal scaffolds) is plain ASCII by
    # design, so it is safe for every provider.
    for name in spells.TECHNIQUES:
        if name in seen:
            continue
        seen.add(name)
        techniques.append({
            "name": name,
            "category": "spell",
            "safe": True,
            "preferred": name in spell_preferred,
            "extended": False,
            "source": "spell",
        })
    return {
        "provider": provider,
        "safe": sorted(safe | spell_safe),
        "preferred": sorted(preferred | spell_preferred),
        "extended": sorted(extended),
        "p4rs3lt0ngv3": {
            "available": bool(extension.get("count")),
            "count": int(extension.get("count") or 0),
            "safe": len(extended),
            "commit": extension.get("commit"),
            "version": extension.get("version"),
            "error": extension.get("error") or "",
        },
        "techniques": techniques,
    }


def autotune_preview(payload: dict) -> dict:
    """Read-only AutoTune computation for the dashboard.

    No provider call, no state written. ``history`` is an optional list of
    prior message strings or ``{content: ...}`` objects, capped at 20 turns.
    """
    import autotune

    query = str(payload.get("query") or payload.get("q") or "").strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > 20_000:
        raise ValueError("query too long")
    strategy = str(payload.get("strategy") or "adaptive").strip().lower()
    if strategy not in ("adaptive", *autotune.STRATEGY_PROFILES):
        raise ValueError("unknown strategy")

    raw_history = payload.get("history") or []
    if not isinstance(raw_history, list):
        raise ValueError("history must be a list")
    history = raw_history[-20:]
    return autotune.compute_params(query, history, strategy=strategy)


def list_behaviors() -> list[dict]:
    try:
        import yaml
    except Exception:
        return []
    for path in (BEHAVIORS_PRIMARY, BEHAVIORS_FALLBACK):
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            return data.get("behaviors") or []
        except Exception:
            continue
    return []


# ─── Run lifecycle ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _process_options() -> dict:
    try:
        import process_control
        return process_control.process_group_options()
    except Exception:
        return {}


def _watch(run_id: str, proc: subprocess.Popen) -> None:
    try:
        returncode = proc.wait()
    except Exception:
        returncode = -1
    with _lock:
        _procs.pop(run_id, None)
    # The provider key existed for this run only.
    agent_secrets.remove_session_key_file(run_id)
    meta_path = _run_dir(run_id) / "meta.json"
    meta = _read_json(meta_path, {}) or {}
    meta.update({
        "status": "finished" if returncode == 0 else "error",
        "returncode": returncode,
        "ended_at": _now(),
    })
    _write_json(meta_path, meta)


def _validate_carriers(raw: Any) -> str:
    carriers = str(raw or "plain").strip()
    if (not carriers
            or len(carriers) > MAX_CARRIERS_CHARS
            or any(ch in carriers for ch in "\r\n")):
        raise ValueError("invalid carriers")
    return carriers


def start_run(payload: dict) -> dict:
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    behavior = str(payload.get("behavior") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    carriers = _validate_carriers(payload.get("carriers"))

    if not _PROVIDER_RE.fullmatch(provider):
        raise ValueError("invalid provider")
    if not model or len(model) > 200 or any(ord(ch) < 32 for ch in model):
        raise ValueError("invalid model id")
    if not behavior and not prompt:
        raise ValueError("behavior or prompt is required")

    try:
        reps = int(payload.get("reps") or 1)
    except Exception:
        reps = 1
    reps = max(1, min(reps, 20))

    judge = bool(payload.get("judge", True))
    require_change = bool(payload.get("require_change", True))
    dry_run = bool(payload.get("dry_run", False))
    autotune_mode = str(payload.get("autotune") or "off").strip().lower()
    if autotune_mode not in ("off", "adaptive", "precise", "balanced", "creative", "chaotic"):
        raise ValueError("invalid autotune strategy")
    try:
        timeout = int(payload.get("timeout") or 240)
    except Exception:
        timeout = 240
    timeout = max(30, min(timeout, 1800))

    # The provider key the target needs, plus the judge's own key. The judge
    # is a separate transport (Hermes via OpenRouter) and cannot borrow the
    # target's credential, but it must not drag in the rest of the vault
    # either — hence an explicit name list rather than the whole set.
    key_names = list(agent_secrets.provider_key_names(provider))
    if judge and "OPENROUTER_API_KEY" not in key_names:
        key_names.append("OPENROUTER_API_KEY")

    run_id = str(payload.get("run_id") or "").strip() or _new_run_id()
    if not _valid_run_id(run_id):
        raise ValueError("invalid run id")

    run_dir = _run_dir(run_id)
    (run_dir / "prompts").mkdir(parents=True, exist_ok=True)

    config = {
        "provider": provider,
        "model": model,
        "behavior": behavior,
        "carriers": carriers,
        "reps": reps,
        "judge": judge,
        "require_change": require_change,
        "autotune": autotune_mode,
        "dry_run": dry_run,
        "timeout": timeout,
        "prompt_chars": len(prompt),
        "key_scope": list(key_names),
    }
    _write_json(run_dir / "meta.json", {
        "run_id": run_id,
        "status": "starting",
        "config": config,
        "pid": None,
        "started_at": _now(),
    })

    argv = [
        sys.executable, "-X", "utf8", str(LAB_SCRIPT), "ab",
        "--provider", provider,
        "--model", model,
        "--carriers", carriers,
        "--reps", str(reps),
        "--run-id", run_id,
        "--out", str(RUNS_ROOT),
        "--delay", "0.75",
        "--timeout", str(timeout),
    ]
    if prompt:
        payload_path = run_dir / "payload.txt"
        payload_path.write_text(prompt, encoding="utf-8")
        argv += ["--prompt-file", str(payload_path)]
    else:
        argv += ["--behavior", behavior]
    if not judge:
        argv += ["--no-judge"]
    if require_change:
        argv += ["--require-change"]
    if dry_run:
        argv += ["--dry-run"]
    if autotune_mode != "off":
        argv += ["--autotune", autotune_mode]

    # Scoped environment. The lab subprocess gets configuration but no
    # provider credentials from the server: its one provider key arrives
    # through a per-run 0600 key file that rt_send.resolve_key() reads, and
    # jailbreak_lab.py is told not to re-read .env on top of it.
    key_path = agent_secrets.write_session_key_file(run_id, names=key_names)
    env = agent_secrets.filtered_env(extra={
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PLINY_LAB_SCOPED_ENV": "1",
    })
    env.pop("PLINY_AGENT_TOKEN", None)  # the lab does not call the dashboard
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
        # The run never started; do not leave its provider key behind.
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
                     name=f"lab-watch-{run_id}").start()

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


def _compact_attempt(record: dict) -> dict:
    carrier = record.get("carrier") or {}
    judge = record.get("judge") or {}
    return {
        "n": record.get("n"),
        "ts": record.get("ts"),
        "carrier": carrier.get("label", ""),
        "noop": bool(carrier.get("noop")),
        "prompt_changed": bool(record.get("prompt_changed")),
        "score": judge.get("score"),
        "verdict": record.get("verdict", ""),
        "error": record.get("error", ""),
        "skipped": record.get("skipped", ""),
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
        status = "finished" if meta.get("returncode") == 0 else "error"
        meta["status"] = status
        _write_json(run_dir / "meta.json", meta)

    manifest = _read_json(run_dir / "manifest.json", {}) or {}
    summary = _read_json(run_dir / "summary.json", {}) or {}
    # Runs started from the CLI predate meta.json. A completed summary is the
    # honest signal that the run finished. A dashboard run whose process is
    # gone after a server restart is "orphaned" unless it wrote a summary.
    if status in ("unknown", "starting") and summary:
        status = "finished"
    if status == "running" and not alive:
        status = "finished" if summary else "orphaned"
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
        "summary": summary,
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
        summary = _read_json(run_dir / "summary.json", {}) or {}
        if not meta and not manifest:
            continue
        target = manifest.get("target") or {}
        overall = summary.get("overall") or {}
        rows.append({
            "run_id": run_dir.name,
            "status": meta.get("status") or ("finished" if summary else "unknown"),
            "started_at": meta.get("started_at") or manifest.get("started_at", ""),
            "provider": target.get("provider") or (meta.get("config") or {}).get("provider", ""),
            "model": target.get("model") or (meta.get("config") or {}).get("model", ""),
            "behavior": (manifest.get("behavior") or {}).get("id") or (meta.get("config") or {}).get("behavior", ""),
            "attempts": overall.get("attempts", len(_attempts(run_dir))),
            "cracks": overall.get("cracks", 0),
            "best_carrier": summary.get("best_carrier") or "",
            "trophies": len(list(TROPHY_ROOT.glob(f"{run_dir.name}_*.md"))),
            "mtime": run_dir.stat().st_mtime,
        })
    rows.sort(key=lambda r: (r.get("mtime") or 0, r.get("started_at") or ""), reverse=True)
    for row in rows:
        row.pop("mtime", None)
    return rows[: max(1, min(limit, 100))]


def list_trophies(limit: int = 50) -> list[dict]:
    rows: list[dict] = []
    try:
        files = [p for p in TROPHY_ROOT.glob("*.md") if _TROPHY_RE.fullmatch(p.name)]
    except Exception:
        return rows
    for path in files:
        try:
            title = ""
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                title = (handle.readline() or "").strip().lstrip("# ").strip()
            rows.append({
                "name": path.name,
                "title": title or path.stem,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("modified", ""), reverse=True)
    return rows[: max(1, min(limit, 200))]


def read_trophy(name: str) -> Optional[str]:
    if not _TROPHY_RE.fullmatch(name or ""):
        return None
    path = TROPHY_ROOT / name
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
    except Exception:
        return None
