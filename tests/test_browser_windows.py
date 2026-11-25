import io
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import pw_browser
import server


class BrowserWindowsTests(unittest.TestCase):
    def test_old_browser_session_is_recovered_from_actual_tool_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "curl http://localhost:8787/screenshot"}}]}}
            (Path(tmp) / "legacy.log").write_text(json.dumps(event), encoding="utf-8")
            with patch.object(server, "SESSIONS_DIR", Path(tmp)):
                self.assertTrue(server.saved_computer_use({"id": "legacy"}))
                self.assertFalse(server.saved_computer_use({"id": "legacy", "computer_use": False}))
                self.assertFalse(server.saved_computer_use({"id": "missing"}))

    def test_browser_flag_survives_save_and_follow_up_after_restart(self):
        session = server.Session("browser-test", "Read a page", computer_use=True, runtime="claude")
        session.claude_session_id = "conversation-test"
        session.status = "completed"
        with patch.object(server, "load_json", return_value=[]), patch.object(server, "save_json") as save:
            session._save_session_record()
        record = save.call_args.args[1][0]
        self.assertTrue(record["computer_use"])
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = Mock(return_value=json.dumps({"session_id": session.session_id, "message": "Read the next page"}))
        handler._send_json = Mock()
        # Reviving a Computer Use mission is an operator action.
        with patch.object(server, "load_json", return_value=[record]), patch.object(server, "active_sessions", {}), patch.object(server.Session, "send_message") as send, patch.object(server, "broadcast_sse"), patch.object(server, "post_comms_message"), patch.object(server, "_check_operator_auth", return_value=True):
            handler._api_session_message()
            restored = server.active_sessions[session.session_id]
            self.assertTrue(restored.computer_use)
            self.assertEqual(restored.claude_session_id, "conversation-test")
            send.assert_called_once_with("Read the next page")
            self.assertTrue(handler._send_json.call_args.args[0]["resumed"])

    def test_continuation_restarts_live_view_without_duplicate_pollers(self):
        session = server.Session("browser-test", "Read a page", computer_use=True, runtime="claude")
        session.claude_session_id = "conversation-test"
        with patch.object(server, "_cu_acquire", return_value=True), patch.object(server, "launch_agent_process"), patch.object(session, "_save_session_record"), patch.object(session, "_read_output"), patch.object(server, "broadcast_sse"), patch.object(server.threading, "Thread") as thread:
            session._send_continuation(["Read the next page"])
            thread.assert_called_once_with(target=session._screenshot_poller, daemon=True)
            thread.return_value.start.assert_called_once()
            thread.return_value.is_alive.return_value = True
            session._start_screenshot_poller()
            thread.return_value.start.assert_called_once()

    def test_preview_reads_browser_image_from_native_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
            (Path(tmp) / "pliny_pw_screen.png").write_bytes(image)
            handler = server.Handler.__new__(server.Handler)
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.wfile = io.BytesIO()
            with patch.object(server.tempfile, "gettempdir", return_value=tmp):
                handler._api_cu_screenshot()
            handler.send_response.assert_called_once_with(200)
            self.assertEqual(handler.wfile.getvalue(), image)

    def test_empty_old_profile_does_not_hide_logged_in_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.default-release").mkdir()
            current = root / "current.default-release"
            current.mkdir()
            (current / "cookies.sqlite").touch()
            with patch.object(pw_browser, "FIREFOX_PROFILES", root):
                self.assertEqual(pw_browser.find_firefox_profile(), current)

    def test_cookie_copy_uses_native_temp_without_sqlite_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            profile.mkdir()
            with closing(sqlite3.connect(profile / "cookies.sqlite")) as db:
                db.execute("CREATE TABLE moz_cookies (host, name, value, path, expiry, isSecure, isHttpOnly, sameSite)")
                db.execute("INSERT INTO moz_cookies VALUES ('.example.test', 'test', 'fixture', '/', 0, 1, 1, 1)")
                db.commit()
            with patch.object(pw_browser.tempfile, "gettempdir", return_value=tmp), patch("subprocess.run", side_effect=FileNotFoundError):
                cookies = pw_browser.extract_firefox_cookies(profile)
            self.assertEqual(len(cookies), 1)
            self.assertEqual(cookies[0]["domain"], ".example.test")
            self.assertFalse((Path(tmp) / "pliny_ff_cookies.sqlite").exists())

    def test_windows_firefox_version_and_user_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ff_dir = Path(tmp) / "Mozilla Firefox"
            ff_dir.mkdir()
            (ff_dir / "firefox.exe").write_text("", encoding="utf-8")
            (ff_dir / "application.ini").write_text("[App]\nVersion=150.0.1\n", encoding="utf-8")
            with patch.dict(pw_browser.os.environ, {"ProgramFiles": tmp, "ProgramFiles(x86)": "", "LOCALAPPDATA": ""}, clear=False), \
                    patch.object(pw_browser.shutil, "which", return_value=None):
                self.assertEqual(pw_browser._windows_firefox_version(), "150.0.1")
        ua = pw_browser._firefox_user_agent("150", windows=True)
        self.assertIn("Windows NT 10.0; Win64; x64", ua)
        self.assertNotIn("Macintosh", ua)
        with patch.object(pw_browser.os, "name", "nt"), \
                patch("subprocess.run", side_effect=AssertionError("no macOS osascript on Windows")):
            pw_browser.bring_firefox_to_front()

    def test_live_preview_param_fetches_sidecar_and_falls_back_to_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = b"\x89PNG\r\n\x1a\n" + b"f" * 600
            live = b"\x89PNG\r\n\x1a\n" + b"l" * 600
            (Path(tmp) / "pliny_pw_screen.png").write_bytes(fallback)
            for payload, expected in ((live, live), (OSError("offline"), fallback)):
                handler = server.Handler.__new__(server.Handler)
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.wfile = io.BytesIO()
                if isinstance(payload, Exception):
                    opener = patch.object(server.urllib.request, "urlopen", side_effect=payload)
                else:
                    resp = Mock()
                    resp.read.return_value = payload
                    cm = Mock()
                    cm.__enter__ = Mock(return_value=resp)
                    cm.__exit__ = Mock(return_value=False)
                    opener = patch.object(server.urllib.request, "urlopen", return_value=cm)
                with opener, patch.object(server.tempfile, "gettempdir", return_value=tmp):
                    handler._api_cu_screenshot(params={"live": ["1"]})
                self.assertEqual(handler.wfile.getvalue(), expected)


if __name__ == "__main__":
    unittest.main()
