import unittest
import subprocess
import os
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import opencode_runner
from opencode_runner import DashboardEvents, run_command


class TextRequestContainmentTests(unittest.TestCase):
    def _command(self, child_pid_file, *, parent_wait):
        # The child holds both output handles open after the parent exits.
        # It performs no network calls and expires even if cleanup is broken.
        child = "import time; time.sleep(5)"
        parent = (
            "import json,subprocess,sys,time; from pathlib import Path; "
            "prompt=sys.stdin.read(); "
            "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdin=subprocess.DEVNULL); "
            "Path(sys.argv[2]).write_text(str(child.pid)); "
            "print(json.dumps({'type':'text','part':{'id':'fixture','text':prompt}}),flush=True); "
            "print('fixture stderr',file=sys.stderr,flush=True); "
            "time.sleep(float(sys.argv[3]))"
        )
        return [sys.executable, "-X", "utf8", "-c", parent, child, str(child_pid_file), str(parent_wait)]

    def _assert_child_stopped(self, pid_file):
        self.assertTrue(pid_file.is_file(), "fixture did not start its child")
        pid = int(pid_file.read_text())
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if handle:
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0,
                                     "request left a child process running")
                finally:
                    kernel.CloseHandle(handle)
            else:
                self.assertEqual(ctypes.get_last_error(), 87, "could not inspect the owned child")
        else:
            # A killed child may remain a zombie until init reaps it.
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                self.assertEqual(stat.read_text().split(") ", 1)[1][0], "Z")

    def test_timeout_does_not_wait_for_descendant_output_handles(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş timeout ") as tmp:
            pid_file = Path(tmp) / "child.pid"
            with patch.object(opencode_runner, "run_command", return_value=self._command(pid_file, parent_wait=10)), \
                    patch.object(opencode_runner, "config_env", return_value=dict(os.environ)):
                start = time.monotonic()
                with self.assertRaises(subprocess.TimeoutExpired):
                    opencode_runner.complete_text("Harmless timeout fixture", timeout=0.8)
                self.assertLess(time.monotonic() - start, 3,
                                "timeout waited for descendant-held pipes")
            self._assert_child_stopped(pid_file)

    def test_success_does_not_wait_for_descendants_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş success ") as tmp:
            pid_file = Path(tmp) / "child.pid"
            with patch.object(opencode_runner, "run_command", return_value=self._command(pid_file, parent_wait=0)), \
                    patch.object(opencode_runner, "config_env", return_value=dict(os.environ)):
                start = time.monotonic()
                answer = opencode_runner.complete_text("Zararsız İstanbul 你好", timeout=2)
                self.assertEqual(answer, "Zararsız İstanbul 你好")
                self.assertLess(time.monotonic() - start, 3)
            self._assert_child_stopped(pid_file)

    def test_failed_command_preserves_stderr(self):
        command = [sys.executable, "-c", "import sys; print('fixture failure',file=sys.stderr); sys.exit(7)"]
        with patch.object(opencode_runner, "run_command", return_value=command), \
                patch.object(opencode_runner, "config_env", return_value=dict(os.environ)):
            with self.assertRaisesRegex(RuntimeError, "exit code 7; stderr: fixture failure"):
                opencode_runner.complete_text("Harmless failure fixture", timeout=2)

    @unittest.skipUnless(os.name == "nt", "Windows process containment")
    def test_missing_containment_does_not_launch_an_unowned_process(self):
        with patch.object(opencode_runner, "run_command", return_value=[sys.executable]), \
                patch.object(opencode_runner, "config_env", return_value={}), \
                patch("process_control.create_kill_on_close_job", return_value=None), \
                patch("process_control.launch_agent_process") as launch:
            with self.assertRaisesRegex(OSError, "Cannot create process containment"):
                opencode_runner.complete_text("Harmless fixture", timeout=2)
        launch.assert_not_called()

    def test_target_cwd_reaches_the_child(self):
        """The workspace the model can read is a parameter, not an accident.

        Run 20261117-234316-5ee158 probes 5/6: fired from the repo, the target
        read AGENTS.md and answered as an agent that already knew it was being
        tested; from an empty directory it argued the ask on its merits. A
        target call must be able to run somewhere that documents nothing.
        """
        with tempfile.TemporaryDirectory(prefix="neutral target workspace ") as tmp:
            command = [sys.executable, "-X", "utf8", "-c",
                       "import json,sys; print(json.dumps({'type':'text','part':{'id':'a','text':sys.stdin.read()}}),flush=True)"]
            with patch.object(opencode_runner, "run_command", return_value=command), \
                    patch.object(opencode_runner, "config_env", return_value=dict(os.environ)):
                answer = opencode_runner.complete_text("cwd probe", timeout=10, cwd=tmp)
            self.assertEqual(answer, "cwd probe")


class OpenCodeStatusTests(unittest.TestCase):
    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    def test_status_bypasses_a_stale_background_service(self, _cli):
        def cli(command, **options):
            if command == ['opencode.exe', '--version']:
                return subprocess.CompletedProcess(command, 0, 'opencode v2\n')
            if command == ['opencode.exe', '--standalone', 'auth', 'list']:
                return subprocess.CompletedProcess(command, 0, 'OpenCode Go  OpenCode Go  stored\n')
            # Without --standalone the CLI needs its service, whose port may be taken.
            return subprocess.CompletedProcess(command, 1, '', 'Managed service port is already in use')

        with patch('opencode_runner.subprocess.run', side_effect=cli):
            status = opencode_runner._opencode_status_uncached()
        self.assertTrue(status['installed'] and status['configured'])


class OpenCodeCatalogTests(unittest.TestCase):
    def setUp(self):
        cache = patch.multiple(opencode_runner, _catalog_cache=None, _catalog_until=0.0)
        cache.start()
        self.addCleanup(cache.stop)

    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    def test_model_catalog_bypasses_unavailable_background_service(self, _cli):
        def cli_models(command, **options):
            if command == ['opencode.exe', '--standalone', 'models']:
                return subprocess.CompletedProcess(command, 0,
                    'opencode-go/model-b\nopenai/model-a\nopencode-go/model-b\n')
            raise subprocess.TimeoutExpired(command, 15)

        with patch('opencode_runner.subprocess.run', side_effect=cli_models):
            self.assertEqual(opencode_runner.opencode_models(),
                             ['openai/model-a', 'opencode-go/model-b'])

    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    def test_failed_refresh_preserves_models_and_recovers_soon(self, _cli):
        opencode_runner._catalog_cache = ['opencode-go/previous']
        with patch('opencode_runner.time.monotonic', return_value=100), \
                patch('opencode_runner.subprocess.run', side_effect=subprocess.TimeoutExpired('opencode', 15)):
            self.assertEqual(opencode_runner.opencode_models(), ['opencode-go/previous'])
        self.assertLessEqual(opencode_runner._catalog_until, 110)
        result = subprocess.CompletedProcess('opencode', 0, 'opencode-go/current\n')
        with patch('opencode_runner.time.monotonic', return_value=111), \
                patch('opencode_runner.subprocess.run', return_value=result):
            self.assertEqual(opencode_runner.opencode_models(), ['opencode-go/current'])

    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    def test_empty_output_does_not_replace_a_successful_catalog(self, _cli):
        opencode_runner._catalog_cache = ['opencode-go/previous']
        with patch('opencode_runner.time.monotonic', return_value=100), \
                patch('opencode_runner.subprocess.run', return_value=subprocess.CompletedProcess('opencode', 0, '')):
            self.assertEqual(opencode_runner.opencode_models(), ['opencode-go/previous'])
        self.assertLessEqual(opencode_runner._catalog_until, 110)


class OpenCodeTests(unittest.TestCase):
    def test_session_and_text_events_preserve_resume_id_and_answer(self):
        stream = DashboardEvents()
        init = stream.convert({'type':'step_start', 'sessionID':'ses_test', 'part':{}})
        self.assertEqual(init[0]['session_id'], 'ses_test')
        answer = stream.convert({'type':'text', 'sessionID':'ses_test', 'part':{'id':'p1', 'text':'Merhaba'}})
        self.assertEqual(answer[0]['message']['content'][0]['text'], 'Merhaba')
        self.assertEqual(stream.answer, 'Merhaba')

    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    def test_go_is_pinned_and_resume_is_explicit(self, _cli):
        command = run_command('opencode-go/glm-5.3-flash', resume='ses_exact')
        self.assertIn('--standalone', command)
        self.assertEqual(command[command.index('--model') + 1], 'opencode-go/glm-5.3-flash')
        self.assertEqual(command[command.index('--session') + 1], 'ses_exact')
        self.assertNotIn('--continue', command)
        self.assertIn('openrouter/x/y', run_command('openrouter/x/y'))
        with self.assertRaises(ValueError):
            run_command('--agent')

    def test_errors_are_not_success(self):
        stream = DashboardEvents()
        events = stream.convert({'type':'error', 'error':{'data':{'message':'Quota exceeded'}}})
        self.assertTrue(stream.failed)
        self.assertTrue(events[0]['is_error'])
        self.assertEqual(events[0]['result'], 'Quota exceeded')


_GO_CATALOG = [
    "openai/gpt-6-luna", "opencode/gpt-6-luna", "opencode-go/gpt-6-luna",
    "opencode-go/glm-5.3-flash", "openai/gpt-6-luna-pro",
    "openrouter/openai/gpt-6-luna", "openrouter/openai/gpt-5.6-luna",
]


class RepairGoRouteTest(unittest.TestCase):
    @patch.dict(os.environ, {"PLINY_AGENT_PERMISSIONS": "workshop"})
    @patch('opencode_runner.opencode_cli', return_value='opencode.exe')
    @patch('opencode_runner.opencode_models', return_value=list(_GO_CATALOG))
    def test_connected_adapter_ids_repair_to_go(self, _models, _cli):
        # The operator pays for the Go pool; openai/* (stored ChatGPT cred,
        # 401s), opencode/* (console funds, 402s) and openrouter/openai/*
        # (OR key, 401s) must rewrite when Go serves the same model.
        self.assertEqual(
            run_command('openai/gpt-6-luna')[run_command('openai/gpt-6-luna').index('--model') + 1],
            'opencode-go/gpt-6-luna')

    @patch('opencode_runner.opencode_models', return_value=list(_GO_CATALOG))
    def test_all_repair_variants(self, _models):
        self.assertEqual(opencode_runner.repair_go_route('openai/gpt-6-luna'),
                         'opencode-go/gpt-6-luna')
        self.assertEqual(opencode_runner.repair_go_route('opencode/gpt-6-luna'),
                         'opencode-go/gpt-6-luna')
        self.assertEqual(opencode_runner.repair_go_route('openrouter/openai/gpt-6-luna'),
                         'opencode-go/gpt-6-luna')

    @patch('opencode_runner.opencode_models', return_value=list(_GO_CATALOG))
    def test_explicit_go_and_unknown_twins_pass_through(self, _models):
        self.assertEqual(opencode_runner.repair_go_route('opencode-go/glm-5.3-flash'),
                         'opencode-go/glm-5.3-flash')
        # No Go twin for this one -> adapter id stays untouched.
        self.assertEqual(opencode_runner.repair_go_route('openrouter/anthropic/claude-x'),
                         'openrouter/anthropic/claude-x')
        # Pro variant: only `-pro` exists under Go via luna-* — gpt-6-luna-pro
        # itself has no exact twin (fast/pro listed, not bare pro) -> untouched.
        self.assertEqual(opencode_runner.repair_go_route('openai/gpt-6-luna-pro'),
                         'openai/gpt-6-luna-pro')

    @patch('opencode_runner.opencode_models', return_value=list(_GO_CATALOG))
    def test_go_twin_decision_follows_catalog(self, _models):
        # gpt-6-luna exists under Go -> repaired.
        self.assertEqual(opencode_runner.repair_go_route('openai/gpt-6-luna'),
                         'opencode-go/gpt-6-luna')
        # gpt-6-luna-pro has NO Go twin in the live catalog -> untouched,
        # operator keeps the adapter they explicitly named.
        self.assertEqual(opencode_runner.repair_go_route('openai/gpt-6-luna-pro'),
                         'openai/gpt-6-luna-pro')

    @patch('opencode_runner.opencode_models', side_effect=RuntimeError("cli unavailable"))
    def test_catalog_failure_fails_open(self, _models):
        self.assertEqual(opencode_runner.repair_go_route('openai/gpt-6-luna'),
                         'openai/gpt-6-luna')

    @patch('opencode_runner.opencode_models', return_value=list(_GO_CATALOG))
    def test_invalid_model_bypasses_repair(self, _models):
        self.assertEqual(opencode_runner.repair_go_route('--agent'), '--agent')
        self.assertEqual(opencode_runner.repair_go_route(123), 123)


class UsageAccountingTests(unittest.TestCase):
    def test_step_finish_accumulates_tokens_and_cost(self):
        events = opencode_runner.DashboardEvents()
        events.convert({"type": "step_finish", "part": {
            "tokens": {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 900}},
            "cost": 0.02}})
        events.convert({"type": "step_finish", "part": {
            "tokens": {"input": 50, "output": 5, "reasoning": 2, "cache": {"read": 1000}},
            "cost": 0.01}})
        self.assertEqual(events.usage["input"], 150)
        self.assertEqual(events.usage["output"], 15)
        self.assertEqual(events.usage["reasoning"], 7)
        self.assertEqual(events.usage["cache_read"], 1900)
        self.assertAlmostEqual(events.usage["cost"], 0.03, places=6)

    def test_missing_fields_do_not_break_accounting(self):
        events = opencode_runner.DashboardEvents()
        events.convert({"type": "step_finish", "part": {}})
        self.assertEqual(events.usage, {"input": 0, "output": 0, "reasoning": 0,
                                        "cache_read": 0, "cost": 0.0})


class CompactionSettingsTests(unittest.TestCase):
    def test_cu_sessions_compact_below_the_window_limit(self):
        with patch.dict(os.environ, {"PLINY_CU_TOKEN": "cap"}, clear=False):
            config = json.loads(opencode_runner.config_env({})["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["compaction"]["buffer"], "20%")
        self.assertEqual(config["compaction"]["keep"]["tokens"], 15000)
        self.assertTrue(config["compaction"]["auto"])

    def test_plain_sessions_keep_the_default_compaction(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_CU_TOKEN", None)
            config = json.loads(opencode_runner.config_env({})["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("compaction", config)

    def test_cu_sessions_can_withhold_automation_mcp_servers(self):
        with patch.dict(os.environ, {"PLINY_CU_TOKEN": "cap",
                                     "PLINY_CU_BLOCK_MCP": "solidworks, burp"}, clear=False):
            config = json.loads(opencode_runner.config_env({})["OPENCODE_CONFIG_CONTENT"])
        self.assertFalse(config["mcp"]["solidworks"]["enabled"])
        self.assertFalse(config["mcp"]["burp"]["enabled"])

    def test_mcp_block_requires_a_cu_session(self):
        with patch.dict(os.environ, {"PLINY_CU_BLOCK_MCP": "solidworks"}, clear=False):
            os.environ.pop("PLINY_CU_TOKEN", None)
            config = json.loads(opencode_runner.config_env({})["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("mcp", config)


if __name__ == "__main__":
    unittest.main()
