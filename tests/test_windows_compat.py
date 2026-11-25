"""Regression checks for native Windows setup and background services."""

from contextlib import closing
from http.server import HTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.request

import fixer
import install_windows
import process_control
import pw_browser
import server


class WindowsSetupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native cmd launcher")
    def test_launcher_handles_spaces_and_unicode_without_starting_real_server(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş launch ") as tmp:
            root = Path(tmp)
            shutil.copy2(Path(__file__).resolve().parents[1] / "start-windows.cmd", root)
            (root / "install_windows.py").write_text(
                "import os, sys\nfrom pathlib import Path\n"
                "assert sys.argv[1:] == ['--run', '--restart']\n"
                "assert sys.flags.utf8_mode == 1\n"
                "assert os.environ['PLINY_AGENT_RUNTIME'] == 'codex'\n"
                "assert Path(os.environ['PLINY_WORKSHOP']) == Path.cwd() / 'state' / 'workshop'\n"
                "print('launcher smoke passed')\n", encoding="utf-8")
            env = dict(os.environ)
            env.pop("PLINY_AGENT_RUNTIME", None)
            env.pop("PLINY_WORKSHOP", None)
            result = subprocess.run(["cmd.exe", "/d", "/c", str(root / "start-windows.cmd")],
                                    cwd=root, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("launcher smoke passed", result.stdout)

    def test_setup_seeds_missing_files_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş setup ") as tmp:
            root = Path(tmp)
            seed = root / "setup" / "starter_rt_library" / "gauntlets" / "presets"
            seed.mkdir(parents=True)
            (seed / "new.yml").write_text("name: starter", encoding="utf-8")
            template = root / "setup" / ".env.example"
            template.write_text("EXAMPLE=placeholder", encoding="utf-8")
            install_windows.initialize_files(root)
            preset = root / "rt_library" / "gauntlets" / "presets" / "new.yml"
            self.assertEqual(preset.read_text(encoding="utf-8"), "name: starter")
            preset.write_text("name: customized", encoding="utf-8")
            (root / ".env").write_text("EXAMPLE=keep", encoding="utf-8")
            (root / "state" / "existing.json").write_text("{}", encoding="utf-8")
            install_windows.initialize_files(root)
            self.assertEqual(preset.read_text(encoding="utf-8"), "name: customized")
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "EXAMPLE=keep")
            self.assertTrue((root / "state" / "existing.json").exists())
            self.assertTrue((root / "logs").is_dir())
            self.assertTrue((root / "sessions").is_dir())


@unittest.skipUnless(os.name == "nt", "native Windows process checks")
class WindowsServiceTests(unittest.TestCase):
    def test_watchdog_liveness_check_leaves_real_process_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with patch.object(fixer.os, "kill", side_effect=AssertionError("must not send a signal")):
                self.assertTrue(fixer._pid_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(fixer._pid_alive(child.pid))
        self.assertFalse(fixer._pid_alive(0))

    def test_watchdog_keeps_lock_when_liveness_query_fails(self):
        with patch.object(fixer, "process_group_exists", side_effect=subprocess.TimeoutExpired("tasklist", 5)):
            self.assertTrue(fixer._pid_alive(123))

    def test_browser_process_matching_excludes_other_repos_and_path_mentions(self):
        script = str(Path(r"C:\Pliny Ş project\pw_browser.py"))
        rows = [
            (10, 1, "python.exe", f'"C:\\Python\\python.exe" -X utf8 -u "{script}" --port 8787'),
            (11, 1, "python.exe", 'python.exe "C:\\other\\pw_browser.py"'),
            (12, 1, "python.exe", f'python.exe -c "print(\'{script}\')"'),
            (13, 1, "python.exe", f'python.exe helper.py "{script}"'),
            (14, 1, "powershell.exe", f'powershell.exe "{script}"'),
            (15, 1, "pythonw.exe", f'pythonw.exe "{script.replace(chr(92), "/")}"'),
            (16, 1, "python.exe", f'python.exe "{script}.backup"'),
        ]
        with patch.object(process_control, "_process_snapshot", return_value=rows):
            self.assertEqual(process_control.windows_python_script_pids(script), {10, 15})

    def test_process_discovery_reads_real_unicode_script_path(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş process ") as tmp:
            script = Path(tmp) / "fixture_browser.py"
            script.write_text("import time; time.sleep(30)", encoding="utf-8")
            child = subprocess.Popen([sys._base_executable, "-X", "utf8", "-u", str(script)],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.assertEqual(process_control.windows_python_script_pids(str(script)), {child.pid})
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_browser_watchdog_restarts_without_unix_commands(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        with patch.object(server, "scheduler_stop_event", stop), \
                patch.object(server.time, "sleep"), \
                patch.object(server.time, "time", return_value=1000), \
                patch("urllib.request.urlopen", side_effect=OSError("offline")), \
                patch.object(server, "windows_python_script_pids", return_value={1234}), \
                patch.object(server, "signal_process_group") as terminate, \
                patch.object(server, "launch_pw_browser") as launch, \
                patch.object(server, "broadcast_sse"), \
                patch.dict(os.environ, {"PW_BROWSER_NO_AUTO_RESTART": "0"}):
            server.pw_browser_health_loop()
        terminate.assert_called_once_with(1234, force=True)
        launch.assert_called_once_with()

    def test_legacy_browser_endpoints_explain_the_windows_alternative(self):
        handler = server.Handler.__new__(server.Handler)
        handler._send_json = Mock()
        handler._api_agent_browser_provision()
        self.assertEqual(handler._send_json.call_args.args[1], 501)
        self.assertIn("Browser mode", handler._send_json.call_args.args[0]["error"])

    def test_browser_command_shortcuts_use_control_on_windows(self):
        page = Mock()
        with patch.object(pw_browser, "_page", page):
            self.assertTrue(pw_browser.press_key("Meta+Enter")["ok"])
        page.keyboard.press.assert_called_once_with("Control+Enter")
        self.assertEqual(pw_browser.SUBMIT_SHORTCUT, "Control+Enter")

    def test_reply_instructions_do_not_depend_on_mac_clipboard(self):
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = Mock(return_value=json.dumps({"id": "fixture"}))
        handler._send_json = Mock()
        reply = {"reply_text": "Hello", "reply_to": "fixture", "context": "test",
                 "tweet_url": "https://example.test/fixture"}
        with patch.dict(server._pending_replies, {"fixture": reply}, clear=True), \
                patch.object(server, "load_json", return_value=None), \
                patch.object(server, "save_json"), patch.object(server, "broadcast_sse"), \
                patch.object(server, "launch_session", return_value={"id": "fixture-session"}) as launch, \
                patch.object(server.subprocess, "Popen", side_effect=AssertionError("no pbcopy")):
            handler._api_social_reply_approve()
        prompt = launch.call_args.args[0]
        self.assertIn("Control+Return", prompt)
        self.assertIn("Type the following reply text exactly", prompt)
        self.assertNotIn("Cmd+", prompt)


class BrowserCompatibilityTests(unittest.TestCase):
    def test_dashboards_are_served_on_windows_without_starting_agents(self):
        httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/", "/redteam", "/arcade"):
                with self.subTest(path=path), urllib.request.urlopen(
                        f"http://127.0.0.1:{httpd.server_port}{path}", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"<!doctype html>", response.read().lower())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_state_reads_utf8_text_with_non_ascii_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_bytes('["Çağrı 世界 🐉"]'.encode("utf-8"))
            self.assertEqual(server.load_json(path), ["Çağrı 世界 🐉"])

    def test_cookie_snapshot_includes_wal_without_sqlite_executable(self):
        with tempfile.TemporaryDirectory(prefix="Firefox Ş profile ") as tmp:
            profile = Path(tmp)
            with closing(sqlite3.connect(profile / "cookies.sqlite")) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA wal_autocheckpoint=0")
                db.execute("CREATE TABLE moz_cookies (host, name, value, path, expiry, isSecure, isHttpOnly, sameSite)")
                db.execute("INSERT INTO moz_cookies VALUES ('.example.test', 'fixture', 'wal-value', '/', 0, 1, 1, 1)")
                db.commit()
                self.assertTrue((profile / "cookies.sqlite-wal").exists())
                with patch.object(pw_browser.subprocess, "run", side_effect=AssertionError("no external sqlite3")):
                    cookies = pw_browser.extract_firefox_cookies(profile)
                self.assertEqual([cookie["value"] for cookie in cookies], ["wal-value"])

    def test_browser_launch_logs_errors_without_an_unread_pipe(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş project ") as tmp:
            with patch.object(server, "BASE_DIR", Path(tmp)), patch.object(server.subprocess, "Popen") as launch:
                server.launch_pw_browser()
            args, options = launch.call_args
            self.assertEqual(args[0][1:3], ["-X", "utf8"])
            self.assertEqual(options["stderr"], subprocess.STDOUT)
            self.assertNotEqual(options["stdout"], subprocess.PIPE)
            self.assertEqual(options["env"]["PYTHONUTF8"], "1")
            self.assertTrue((Path(tmp) / "logs" / "browser.log").exists())

    def test_render_uses_platform_temp_directory(self):
        # Use harmless fixture text only, without any model/provider calls.
        with tempfile.TemporaryDirectory(prefix="Pliny Ş output ") as tmp:
            target = str(Path(tmp) / "proof.png")
            with patch.object(server, "temp_path", return_value=target), \
                    patch.object(server, "_load_rt_results", return_value=[{
                        "prompt": "Hello", "response": "Hello", "outcome": "refused",
                        "confidence": 0, "attempt": 1,
                    }]):
                self.assertTrue(server.render_redteam_screenshot())
            self.assertEqual(Path(target).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
