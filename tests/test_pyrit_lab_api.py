"""Unit tests for the PyRIT organ bridge.

No network, no provider keys, no PyRIT import: the campaign stays a subprocess,
so everything worth testing here is the contract around it — payload
validation, the argv the sidecar receives, the run directory layout, and how
status/attempts/result are projected for the cockpit. The real engine is
exercised by hand against `organs/pyrit/run_campaign.py`.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyrit_lab_api as api


class FakeProc:
    """Stand-in for a live subprocess."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15


class FakeThread:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def start(self) -> None:
        return None


def write_run(root: Path, run_id: str, *, meta: dict | None = None,
              manifest: dict | None = None, result: dict | None = None,
              attempts: list[dict] | None = None) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if manifest is not None:
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if result is not None:
        (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if attempts is not None:
        lines = "\n".join(json.dumps(row) for row in attempts)
        (run_dir / "attempts.jsonl").write_text(lines + "\n", encoding="utf-8")
    return run_dir


BASE_PAYLOAD = {
    "objective": "Get the target to reply with the exact phrase CANARY_OK",
    "target": "opencode-go/glm-5.3-flash",
    "attacker": "opencode-go/deepseek-v4.1-flash",
    "attack": "crescendo",
    "scorer": "substring",
    "substring": "CANARY_OK",
    "max_turns": 3,
    "max_backtracks": 2,
    "timeout": 120,
}


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch_root = mock.patch.object(api, "RUNS_ROOT", self.root)
        self._patch_root.start()
        self.addCleanup(self._patch_root.stop)
        api._procs.clear()
        self.addCleanup(api._procs.clear)
        self.addCleanup(self._tmp.cleanup)


class ValidationTests(BaseCase):
    def start(self, **overrides):
        payload = dict(BASE_PAYLOAD)
        payload.update(overrides)
        return api.start_run(payload)

    def test_run_id_shape(self) -> None:
        self.assertTrue(api._valid_run_id("ui-20261105-194657-b3d5cc"))
        self.assertTrue(api._valid_run_id("smoke2"))
        for bad in ("", "../escape", "a b", "-leading", "x" * 90, "sub/dir", "a\\b"):
            self.assertFalse(api._valid_run_id(bad), bad)

    def test_clamp_bounds(self) -> None:
        self.assertEqual(api._clamp("7", 1, 10, 5), 7)
        self.assertEqual(api._clamp(0, 1, 10, 5), 1)
        self.assertEqual(api._clamp(99, 1, 10, 5), 10)
        self.assertEqual(api._clamp("nope", 1, 10, 5), 5)

    def test_objective_required(self) -> None:
        with self.assertRaises(ValueError):
            self.start(objective="   ")

    def test_objective_length(self) -> None:
        with self.assertRaises(ValueError):
            self.start(objective="x" * 4001)

    def test_model_id_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.start(target="../../etc/passwd")
        with self.assertRaises(ValueError):
            self.start(attacker="model name with spaces")

    def test_attack_and_scorer_choices(self) -> None:
        with self.assertRaises(ValueError):
            self.start(attack="autodan")
        with self.assertRaises(ValueError):
            self.start(scorer="exact")

    def test_substring_required_for_substring_scorer(self) -> None:
        with self.assertRaises(ValueError):
            self.start(substring="")
        with self.assertRaises(ValueError):
            self.start(substring="x" * 501)

    def test_selfask_needs_no_substring(self) -> None:
        with mock.patch.object(api, "RUN_SCRIPT", self.root / "missing.py"):
            with self.assertRaises(ValueError) as caught:
                self.start(scorer="selfask", substring="")
        self.assertIn("run_campaign.py", str(caught.exception))

    def test_missing_run_script_is_reported(self) -> None:
        with mock.patch.object(api, "RUN_SCRIPT", self.root / "missing.py"):
            with self.assertRaises(ValueError):
                self.start()


class StartRunTests(BaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.script = self.root / "run_campaign.py"
        self.script.write_text("# placeholder\n", encoding="utf-8")
        self._patches = [
            mock.patch.object(api, "RUN_SCRIPT", self.script),
            mock.patch.object(api, "threading"),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)
        api.threading = mock.MagicMock()
        api.threading.Thread = FakeThread
        self.captured: dict = {}

        def fake_popen(argv, **kwargs):
            self.captured["argv"] = argv
            self.captured["kwargs"] = kwargs
            return FakeProc()

        self._patch_popen = mock.patch.object(api.subprocess, "Popen", side_effect=fake_popen)
        self._patch_popen.start()
        self.addCleanup(self._patch_popen.stop)

    def test_start_writes_meta_and_returns_pid(self) -> None:
        result = api.start_run(dict(BASE_PAYLOAD, run_id="mytest-1"))
        self.assertEqual(result, {"run_id": "mytest-1", "status": "running", "pid": 4242})
        meta = json.loads((self.root / "mytest-1" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "running")
        self.assertEqual(meta["pid"], 4242)
        self.assertEqual(meta["config"]["scorer"], "substring")
        self.assertEqual(meta["config"]["substring"], "CANARY_OK")
        self.assertTrue((self.root / "mytest-1" / "console.log").is_file())

    def test_argv_is_the_sidecar_contract(self) -> None:
        api.start_run(dict(BASE_PAYLOAD, run_id="mytest-2", max_turns=4,
                           max_backtracks=1, timeout=90, attack="redteam"))
        argv = self.captured["argv"]
        self.assertEqual(argv[0], str(api._python()))
        self.assertIn(str(self.script), argv)
        pairs = dict(zip(argv, argv[1:]))
        self.assertEqual(pairs["--objective"], BASE_PAYLOAD["objective"])
        self.assertEqual(pairs["--target"], "opencode-go/glm-5.3-flash")
        self.assertEqual(pairs["--attacker"], "opencode-go/deepseek-v4.1-flash")
        self.assertEqual(pairs["--attack"], "redteam")
        self.assertEqual(pairs["--scorer"], "substring")
        self.assertEqual(pairs["--substring"], "CANARY_OK")
        self.assertEqual(pairs["--max-turns"], "4")
        self.assertEqual(pairs["--max-backtracks"], "1")
        self.assertEqual(pairs["--timeout"], "90")
        self.assertEqual(pairs["--run-id"], "mytest-2")
        self.assertEqual(pairs["--out"], str(self.root))
        self.assertEqual(self.captured["kwargs"]["cwd"], str(api.PYRIT_DIR))

    def test_selfask_omits_substring_flag(self) -> None:
        api.start_run(dict(BASE_PAYLOAD, scorer="selfask", substring="", run_id="mytest-3"))
        self.assertNotIn("--substring", self.captured["argv"])

    def test_agent_token_is_stripped_from_child_env(self) -> None:
        def env(extra=None):
            return {"PLINY_AGENT_TOKEN": "leak", **(extra or {})}

        with mock.patch.object(api.agent_secrets, "filtered_env", side_effect=env):
            api.start_run(dict(BASE_PAYLOAD, run_id="mytest-4"))
        self.assertNotIn("PLINY_AGENT_TOKEN", self.captured["kwargs"]["env"])
        self.assertEqual(self.captured["kwargs"]["env"]["PYTHONUTF8"], "1")

    def test_generated_run_id_is_registered(self) -> None:
        result = api.start_run(dict(BASE_PAYLOAD))
        self.assertTrue(api._valid_run_id(result["run_id"]))
        self.assertIn(result["run_id"], api._procs)
        self.assertTrue((self.root / result["run_id"] / "meta.json").is_file())


class StopRunTests(BaseCase):
    def test_stop_terminates_and_marks_meta(self) -> None:
        import process_control

        write_run(self.root, "live", meta={"run_id": "live", "status": "running"})
        proc = FakeProc()
        api._procs["live"] = proc
        with mock.patch.object(process_control, "stop_process_tree") as stop_tree:
            out = api.stop_run("live")
        self.assertEqual(out["status"], "stopped")
        stop_tree.assert_called_once()
        self.assertFalse(stop_tree.call_args.kwargs.get("force", False))
        self.assertNotIn("live", api._procs)
        meta = json.loads((self.root / "live" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "stopped")
        self.assertIn("ended_at", meta)

    def test_stop_unknown_run_still_records(self) -> None:
        write_run(self.root, "ghost", meta={"run_id": "ghost", "status": "running"})
        api.stop_run("ghost")
        meta = json.loads((self.root / "ghost" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "stopped")

    def test_stop_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            api.stop_run("../escape")


class StatusTests(BaseCase):
    ATTEMPTS = [
        {"label": "adversarial", "turn": 1, "model": "deepseek-v4.1-flash",
         "prompt": "p" * 40, "response": "  first   turn  output ", "latency_ms": 900},
        {"label": "objective", "turn": 1, "model": "glm-5.3-flash",
         "prompt": "q" * 20, "response": "second", "latency_ms": 1500},
    ]

    def test_finished_run_projects_result(self) -> None:
        write_run(
            self.root, "done",
            meta={"run_id": "done", "status": "running"},
            manifest={"objective": "obj", "target": "t", "attacker": "a", "attack": "crescendo"},
            result={
                "outcome": "success",
                "outcome_reason": "objective met",
                "executed_turns": 2,
                "execution_time_ms": 1234,
                "last_score": {"score_value": "true", "score_rationale": "canary seen"},
                "last_response": {"original_value": "CANARY_OK"},
            },
            attempts=self.ATTEMPTS,
        )
        status = api.run_status("done")
        self.assertEqual(status["status"], "finished")
        self.assertFalse(status["alive"])
        self.assertEqual(status["manifest"]["objective"], "obj")
        self.assertEqual(status["result"]["outcome"], "success")
        self.assertEqual(status["result"]["last_score"], "true")
        self.assertEqual(status["result"]["last_response"], "CANARY_OK")
        self.assertEqual(len(status["attempts"]), 2)
        first = status["attempts"][0]
        self.assertEqual(first["preview"], "first turn output")
        self.assertEqual(first["prompt_chars"], 40)
        self.assertEqual(first["response_chars"], len("  first   turn  output "))
        self.assertEqual(status["attempts"][1]["turn"], 1)

    def test_running_meta_without_process_is_orphaned(self) -> None:
        write_run(self.root, "orphan", meta={"run_id": "orphan", "status": "running"})
        self.assertEqual(api.run_status("orphan")["status"], "orphaned")

    def test_unknown_status_passes_through(self) -> None:
        write_run(self.root, "queued", meta={"run_id": "queued", "status": "starting"})
        self.assertEqual(api.run_status("queued")["status"], "starting")

    def test_missing_dir_returns_none(self) -> None:
        self.assertIsNone(api.run_status("nope"))
        self.assertIsNone(api.run_status("../nope"))

    def test_live_process_wins_over_result(self) -> None:
        write_run(self.root, "busy", meta={"run_id": "busy", "status": "running"})
        api._procs["busy"] = FakeProc()
        self.assertEqual(api.run_status("busy")["status"], "running")

    def test_finished_result_upgrades_stale_meta(self) -> None:
        write_run(self.root, "stale",
                  meta={"run_id": "stale", "status": "running"},
                  result={"outcome": "failure"})
        self.assertEqual(api.run_status("stale")["status"], "finished")
        meta = json.loads((self.root / "stale" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "finished")
        self.assertIn("ended_at", meta)


class AttemptTests(BaseCase):
    def test_detail_returns_full_record(self) -> None:
        write_run(self.root, "logs", attempts=[
            {"label": "objective", "prompt": "full prompt", "response": "full response"},
            {"label": "scorer", "prompt": "p2", "response": "r2"},
        ])
        record = api.attempt_detail("logs", 1)
        self.assertEqual(record["label"], "scorer")
        self.assertEqual(record["response"], "r2")

    def test_detail_out_of_range(self) -> None:
        write_run(self.root, "logs", attempts=[{"label": "x"}])
        self.assertIsNone(api.attempt_detail("logs", 5))
        self.assertIsNone(api.attempt_detail("logs", -1))
        self.assertIsNone(api.attempt_detail("missing", 0))
        self.assertIsNone(api.attempt_detail("../x", 0))


class ListTests(BaseCase):
    def test_order_and_limit(self) -> None:
        write_run(self.root, "old", manifest={"created_at": "2026-01-01T00:00:00"})
        write_run(self.root, "newer", manifest={"created_at": "2026-02-01T00:00:00"})
        import os
        os.utime(self.root / "old", (1, 1))
        os.utime(self.root / "newer", (2, 2))
        rows = api.list_runs(10)
        self.assertEqual([r["run_id"] for r in rows], ["newer", "old"])
        self.assertEqual(len(api.list_runs(1)), 1)
        self.assertEqual(len(api.list_runs(999)), 2)
        self.assertNotIn("mtime", rows[0])

    def test_skips_empty_and_unsafe_dirs(self) -> None:
        (self.root / "empty").mkdir()
        (self.root / "bad name").mkdir()
        write_run(self.root, "real", meta={"run_id": "real", "status": "stopped"})
        self.assertEqual([r["run_id"] for r in api.list_runs(10)], ["real"])

    def test_projects_outcome_and_turns(self) -> None:
        write_run(self.root, "with-result",
                  manifest={"objective": "o", "target": "t", "attacker": "a", "attack": "redteam"},
                  result={"outcome": "failure", "executed_turns": 7})
        row = api.list_runs(1)[0]
        self.assertEqual(row["outcome"], "failure")
        self.assertEqual(row["executed_turns"], 7)
        self.assertEqual(row["attack"], "redteam")

    def test_meta_only_run_uses_config(self) -> None:
        write_run(self.root, "metaonly", meta={
            "run_id": "metaonly",
            "status": "stopped",
            "config": {
                "objective": "from config",
                "target": "t2",
                "attacker": "a2",
                "attack": "crescendo",
            },
            "started_at": "2026-03-01T00:00:00",
        })
        row = api.list_runs(1)[0]
        self.assertEqual(row["objective"], "from config")
        self.assertEqual(row["target"], "t2")
        self.assertEqual(row["started_at"], "2026-03-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
