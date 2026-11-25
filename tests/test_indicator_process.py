import ast
from pathlib import Path
import threading
import types
import subprocess
import unittest
from unittest.mock import Mock, patch

import server
import indicator_process
import install_windows


class ShutdownCursorTests(unittest.TestCase):
    def test_shutdown_restores_cursors_after_terminating_hud(self):
        tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        handler = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == "shutdown_handler")
        process = Mock()
        process.poll.side_effect = lambda: 0 if process.terminate.called else None
        user = Mock()
        events = []
        process.wait.side_effect = lambda **kw: events.append("exited")
        user.SystemParametersInfoW.side_effect = lambda *args: events.append("restored") or 1
        scope = {**vars(server), "cu_indicator": process, "scheduler_stop_event": Mock(),
                 "active_sessions_lock": threading.Lock(), "active_sessions": {},
                 "sweep_leftovers": Mock(), "reconcile_stale_sessions": Mock(),
                 "server": Mock(), "sys": types.SimpleNamespace(exit=Mock()),
                 "atexit": Mock()}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), server.__file__, "exec"), scope)
        with patch("ctypes.WinDLL", return_value=user, create=True), \
                patch.object(indicator_process.os, "name", "nt"):
            scope["shutdown_handler"](None, None)
        process.terminate.assert_called_once()
        user.SystemParametersInfoW.assert_called_once_with(0x57, 0, None, 0)
        self.assertEqual(events, ["exited", "restored"])


class IndicatorProcessTests(unittest.TestCase):
    def test_stop_kill_timeout_and_already_exited_restore_only_after_exit(self):
        for mode in ("terminate", "kill", "timeout", "exited"):
            with self.subTest(mode=mode):
                events = []
                exited = [mode == "exited"]
                process = Mock()
                process.poll.side_effect = lambda: 0 if exited[0] else None
                def wait(**kwargs):
                    if mode == "timeout" and not process.kill.called:
                        raise subprocess.TimeoutExpired("hud", 2)
                    exited[0] = True
                    events.append("exited")
                process.wait.side_effect = wait
                def restore():
                    self.assertTrue(exited[0])
                    events.append("restored")
                    return True
                with patch.object(indicator_process.threading, "Thread"), \
                        patch.object(indicator_process, "restore_system_cursors", side_effect=restore) as recovery:
                    managed = indicator_process.IndicatorProcess(process)
                    (managed.kill if mode == "kill" else managed.terminate)()
                    self.assertEqual(events, ["exited", "restored"])
                    managed._watch()
                    recovery.assert_called_once()
                if mode == "exited":
                    process.terminate.assert_not_called()
                elif mode == "kill":
                    process.kill.assert_called_once()
                elif mode == "timeout":
                    process.terminate.assert_called_once()
                    process.kill.assert_called_once()

    def test_unexpected_exit_is_recovered_without_a_stop_request(self):
        with patch.object(indicator_process.threading, "Thread"), \
                patch.object(indicator_process, "restore_system_cursors", return_value=True) as recovery:
            process = Mock()
            managed = indicator_process.IndicatorProcess(process)
            recovery.assert_not_called()
            managed._watch()
            process.wait.assert_called_once_with()
            recovery.assert_called_once()
            process.terminate.assert_not_called()

    def test_missing_indicator_does_not_reset_another_huds_cursors(self):
        with patch.object(indicator_process, "restore_system_cursors") as recovery:
            indicator_process.stop_indicator(None)
            recovery.assert_not_called()

    def test_failed_restore_is_retried_on_stop(self):
        with patch.object(indicator_process.threading, "Thread"), \
                patch.object(indicator_process, "restore_system_cursors", side_effect=[False, True]) as recovery:
            process = Mock()
            process.poll.return_value = 0
            managed = indicator_process.IndicatorProcess(process)
            managed._watch()
            managed.stop()
            self.assertEqual(recovery.call_count, 2)

    def test_native_restore_retries_and_logs_failure(self):
        user = Mock()
        with patch.object(indicator_process.os, "name", "nt"), \
                patch("ctypes.WinDLL", return_value=user, create=True), \
                patch.object(indicator_process.time, "sleep"):
            user.SystemParametersInfoW.side_effect = [0, 1]
            self.assertTrue(indicator_process.restore_system_cursors())
            self.assertEqual(user.SystemParametersInfoW.call_count, 2)
            user.SystemParametersInfoW.side_effect = None
            user.SystemParametersInfoW.return_value = 0
            with self.assertLogs(level="WARNING"):
                self.assertFalse(indicator_process.restore_system_cursors())

    def test_posix_does_not_load_windows_libraries(self):
        with patch.object(indicator_process.os, "name", "posix"), \
                patch("ctypes.WinDLL", create=True) as load:
            self.assertTrue(indicator_process.restore_system_cursors())
            load.assert_not_called()

    def test_launcher_restart_restores_after_forced_tree_stop(self):
        root = Path(".")
        events = []
        with patch.object(install_windows.os, "name", "nt"), \
                patch.object(install_windows, "windows_project_server_pids", return_value={123}), \
                patch.object(install_windows, "signal_process_group", side_effect=lambda *a, **kw: events.append("terminated")), \
                patch.object(install_windows, "process_group_exists", return_value=False), \
                patch.object(install_windows, "restore_system_cursors", side_effect=lambda: events.append("restored")):
            install_windows.restart_existing_server(root)
        self.assertEqual(events, ["terminated", "restored"])
