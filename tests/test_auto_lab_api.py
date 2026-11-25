"""Unit tests for the autonomous attacker bridge (auto_lab_api).

Same contract as test_pyrit_lab_api: no network, no provider keys, no
autonomous_attacker import. Everything worth testing is the contract around
the subprocess — payload validation, the argv the sidecar receives, the key
scoping, the run directory layout, and how status/attempts are projected for
the cockpit.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auto_lab_api as api


class FakeProc:
    """Stand-in for a live subprocess."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False

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


class TempRoots:
    """Point the module's run/trophy roots at a temp dir for one test."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._patches = [
            mock.patch.object(api, "RUNS_ROOT", root / "auto"),
            mock.patch.object(api, "TROPHY_ROOT", root / "trophies"),
        ]
        for patcher in self._patches:
            patcher.start()
        api.RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        api.TROPHY_ROOT.mkdir(parents=True, exist_ok=True)
        return root

    def __exit__(self, *exc):
        for patcher in self._patches:
            patcher.stop()
        self._tmp.cleanup()
        return False


def base_payload(**overrides) -> dict:
    payload = {
        "target": "opencode-go/glm-5.3-flash",
        "attacker": "opencode-go/deepseek-v4.1-flash",
        "behavior": "explicit_nsfw",
        "max_turns": 4,
        "reps": 2,
        "spell_intensity": "heavy",
        "stop_on_crack": True,
        "timeout": 120,
    }
    payload.update(overrides)
    return payload


class ValidationTests(unittest.TestCase):
    def test_target_needs_slash(self) -> None:
        with self.assertRaises(ValueError):
            api.start_run(base_payload(target="just-a-model"))

    def test_attacker_needs_slash(self) -> None:
        with self.assertRaises(ValueError):
            api.start_run(base_payload(attacker="nope"))

    def test_behavior_or_prompt_required(self) -> None:
        with self.assertRaises(ValueError):
            api.start_run(base_payload(behavior="", prompt=""))

    def test_bad_intensity(self) -> None:
        with self.assertRaises(ValueError):
            api.start_run(base_payload(spell_intensity="extreme"))


class StartRunTests(unittest.TestCase):
    def test_argv_config_and_key_scope(self) -> None:
        with TempRoots() as root, \
                mock.patch.object(api.subprocess, "Popen", return_value=FakeProc()) as popen, \
                mock.patch.object(api.threading, "Thread", FakeThread), \
                mock.patch.object(api.agent_secrets, "provider_key_names",
                                  side_effect=lambda p: ["OPENAI_API_KEY"] if "openai" in p else []), \
                mock.patch.object(api.agent_secrets, "write_session_key_file",
                                  return_value=root / "keys" / "k.json") as write_keys, \
                mock.patch.object(api.agent_secrets, "filtered_env",
                                  return_value={"PATH": "x"}) as filtered_env:
            result = api.start_run(base_payload(judge=True))

            self.assertEqual(result["status"], "running")
            argv = popen.call_args[0][0]
            self.assertIn(str(api.AUTO_SCRIPT), argv)
            self.assertIn("run", argv[4])
            for flag, value in [
                ("--target", "opencode-go/glm-5.3-flash"),
                ("--attacker", "opencode-go/deepseek-v4.1-flash"),
                ("--behavior", "explicit_nsfw"),
                ("--max-turns", "4"),
                ("--reps", "2"),
                ("--spell-intensity", "heavy"),
                ("--stop-on-crack", None),
                ("--timeout", "120"),
            ]:
                self.assertIn(flag, argv)
                if value is not None:
                    self.assertEqual(argv[argv.index(flag) + 1], value)
            self.assertNotIn("--no-judge", argv)

            # opencode providers contribute no keys; the judge adds OpenRouter.
            write_keys.assert_called_once()
            _, kwargs = write_keys.call_args
            self.assertEqual(kwargs.get("names"), ["OPENROUTER_API_KEY"])

            env = popen.call_args[1]["env"]
            self.assertNotIn("PLINY_AGENT_TOKEN", env)
            self.assertIn(api.agent_secrets.KEY_FILE_ENV, env)
            extras = filtered_env.call_args[1]["extra"]
            self.assertEqual(extras["PLINY_LAB_SCOPED_ENV"], "1")

            run_id = result["run_id"]
            meta = json.loads(
                (api.RUNS_ROOT / run_id / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["kind"], "autonomous_attacker")
            self.assertEqual(meta["config"]["spell_intensity"], "heavy")
            self.assertEqual(meta["config"]["key_scope"], ["OPENROUTER_API_KEY"])

    def test_prompt_payload_written_and_passed(self) -> None:
        with TempRoots(), \
                mock.patch.object(api.subprocess, "Popen", return_value=FakeProc()) as popen, \
                mock.patch.object(api.threading, "Thread", FakeThread), \
                mock.patch.object(api.agent_secrets, "provider_key_names",
                                  return_value=[]), \
                mock.patch.object(api.agent_secrets, "write_session_key_file",
                                  return_value=None), \
                mock.patch.object(api.agent_secrets, "filtered_env",
                                  return_value={}):
            result = api.start_run(base_payload(behavior="", prompt="raw payload"))
            run_dir = api.RUNS_ROOT / result["run_id"]
            self.assertEqual(
                (run_dir / "payload.txt").read_text(encoding="utf-8"),
                "raw payload")
            argv = popen.call_args[0][0]
            self.assertIn("--prompt-file", argv)

    def test_no_judge_flag(self) -> None:
        with TempRoots(), \
                mock.patch.object(api.subprocess, "Popen", return_value=FakeProc()) as popen, \
                mock.patch.object(api.threading, "Thread", FakeThread), \
                mock.patch.object(api.agent_secrets, "provider_key_names",
                                  return_value=[]), \
                mock.patch.object(api.agent_secrets, "write_session_key_file",
                                  return_value=None), \
                mock.patch.object(api.agent_secrets, "filtered_env",
                                  return_value={}):
            api.start_run(base_payload(judge=False))
            argv = popen.call_args[0][0]
            self.assertIn("--no-judge", argv)

    def test_failed_spawn_cleans_key_file(self) -> None:
        with TempRoots(), \
                mock.patch.object(api.subprocess, "Popen",
                                  side_effect=OSError("boom")), \
                mock.patch.object(api.threading, "Thread", FakeThread), \
                mock.patch.object(api.agent_secrets, "provider_key_names",
                                  return_value=[]), \
                mock.patch.object(api.agent_secrets, "write_session_key_file",
                                  return_value=None), \
                mock.patch.object(api.agent_secrets, "filtered_env",
                                  return_value={}), \
                mock.patch.object(api.agent_secrets, "remove_session_key_file") as remove:
            with self.assertRaises(OSError):
                api.start_run(base_payload())
            remove.assert_called_once()


class StatusTests(unittest.TestCase):
    def _write_run(self, root: Path, run_id: str, attempts: list[dict]) -> Path:
        run_dir = root / "auto" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "meta.json").write_text(json.dumps({
            "run_id": run_id,
            "kind": "autonomous_attacker",
            "status": "running",
            "config": {"target": "opencode-go/glm-5.3-flash",
                       "max_turns": 6, "spell_intensity": "medium"},
        }), encoding="utf-8")
        (run_dir / "manifest.json").write_text(json.dumps({
            "kind": "autonomous_attacker",
            "target": {"provider": "opencode-go", "model": "glm-5.3-flash"},
            "attacker": {"provider": "opencode-go", "model": "deepseek-v4.1-flash"},
            "behavior": {"id": "explicit_nsfw", "name": "explicit_nsfw"},
        }), encoding="utf-8")
        with open(run_dir / "attempts.jsonl", "w", encoding="utf-8") as handle:
            for record in attempts:
                handle.write(json.dumps(record) + "\n")
        return run_dir

    def test_status_projects_attempts(self) -> None:
        with TempRoots() as root:
            self._write_run(root, "run-a", [
                {"n": 1001, "campaign": "run-a-r1", "rep": 1, "turn": 1,
                 "carrier": {"label": "persona_writer", "noop": False},
                 "prompt_changed": True, "judge": {"score": 0},
                 "verdict": "refused", "error": "", "response": "no",
                 "latency_ms": 1200},
                {"n": 1002, "campaign": "run-a-r1", "rep": 1, "turn": 2,
                 "carrier": {"label": "push", "noop": False},
                 "prompt_changed": True, "judge": {"score": 2},
                 "verdict": "compliant", "error": "", "response": "ok",
                 "latency_ms": 800},
            ])
            status = api.run_status("run-a")
            self.assertIsNotNone(status)
            self.assertEqual(status["status"], "finished")  # proc gone, attempts exist
            self.assertEqual(len(status["attempts"]), 2)
            self.assertEqual(status["attempts"][1]["carrier"], "push")
            self.assertEqual(status["attempts"][1]["verdict"], "compliant")
            self.assertEqual(status["attempts"][1]["turn"], 2)

    def test_attempt_detail_by_n(self) -> None:
        with TempRoots() as root:
            self._write_run(root, "run-b", [
                {"n": 1001, "turn": 1, "prompt": "P", "response": "R",
                 "attacker": {"raw_choice": '{"carrier": "plain"}'}},
            ])
            record = api.attempt_detail("run-b", 1001)
            self.assertIsNotNone(record)
            self.assertEqual(record["response"], "R")
            self.assertIsNone(api.attempt_detail("run-b", 9999))

    def test_list_runs(self) -> None:
        with TempRoots() as root:
            self._write_run(root, "run-c", [
                {"n": 1001, "verdict": "compliant", "carrier": {"label": "x"},
                 "judge": {"score": 2}},
            ])
            rows = api.list_runs()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "run-c")
            self.assertEqual(rows[0]["cracks"], 1)
            self.assertEqual(rows[0]["behavior"], "explicit_nsfw")

    def test_stop_without_proc(self) -> None:
        with TempRoots():
            result = api.stop_run("run-z")
            self.assertEqual(result["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
