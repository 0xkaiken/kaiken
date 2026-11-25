from http.server import HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

import server
from computer_use_indicator import ComputerUseIndicator, badge_content
from windows_desktop import WindowsDesktop
from desktop_glow import edge_glow_image


class ComputerUseIndicatorTests(unittest.TestCase):
    def test_edge_glow_fades_inward_without_tinting_application_center(self):
        image = edge_glow_image(800, 600)
        self.assertEqual(image.getpixel((400, 300))[3], 0)
        self.assertGreater(image.getpixel((0, 300))[3], image.getpixel((30, 300))[3])
        self.assertGreater(image.getpixel((30, 300))[3], 0)
        self.assertGreater(image.getpixel((799, 599))[3], 0)
        working = edge_glow_image(800, 600, interacting=False)
        self.assertLess(working.getpixel((0, 300))[3], image.getpixel((0, 300))[3])
        paused = edge_glow_image(800, 600, paused=True)
        r, g, b, a = paused.getpixel((400, 0))
        self.assertGreater(r, b)

    def test_badge_distinguishes_active_and_operator_control(self):
        title, detail, color = badge_content({"mode": "desktop", "paused": False})
        self.assertEqual(title, "PLINY USING COMPUTER USE")
        self.assertIn("ESC TO PAUSE", detail)
        self.assertEqual(color, "#39ff14")
        title, detail, color = badge_content({"mode": "desktop", "paused": True})
        self.assertEqual(title, "PLINY PAUSED")
        self.assertIn("YOU HAVE CONTROL", detail)
        self.assertEqual(color, "#ffb000")
        title, detail, color = badge_content({"mode": "desktop", "paused": False, "interacting": False})
        self.assertEqual(title, "PLINY WORKING")
        self.assertIn("DESKTOP READY", detail)
        self.assertEqual(color, "#40c8ff")

    def test_escape_only_pauses_when_armed_and_physical(self):
        pause = Mock()
        indicator = ComputerUseIndicator(Mock(), pause)
        self.assertFalse(indicator.physical_escape(0x1B, 0))
        indicator._armed = True
        self.assertFalse(indicator.physical_escape(0x1B, 0x10))
        self.assertFalse(indicator.physical_escape(0x41, 0))
        self.assertTrue(indicator.physical_escape(0x1B, 0))
        self.assertFalse(indicator.physical_escape(0x1B, 0))
        pause.assert_called_once()

    def test_escape_interrupts_an_ongoing_unicode_typing_operation(self):
        control = WindowsDesktop()
        control.user = Mock()
        control._interactive = Mock()
        control.owner = "test"
        control.paused = False
        control.user.GetForegroundWindow.return_value = 123
        def cursor(ptr):
            ptr._obj.x, ptr._obj.y = 100, 100
        control.user.GetCursorPos.side_effect = cursor
        calls = []
        def send(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                control.emergency_pause()
        control._send = send
        with self.assertRaises(PermissionError):
            control.action("type", {"window_id": 123, "text": "Türkçe"}, "test")
        self.assertEqual(len(calls), 2)
        self.assertTrue(control.operator_paused)

    def test_indicator_tracks_active_pause_and_end_without_stale_badge(self):
        session = Mock(session_id="test", computer_use=True, status="running", cu_mode="desktop", _last_desktop_action_at=0.0, _desktop_action_in_progress=False)
        monitor = {"id": "laptop", "primary": False, "left": 1920, "top": -149, "width": 1920, "height": 1080}
        with patch.object(server, "active_sessions", {"test": session}), patch.object(server, "_cu_active_session_id", "test"), patch.object(server, "_indicator_monitor_key", None), patch.object(server.desktop, "monitor_id", "laptop"), patch.object(server.desktop, "paused", False), patch.object(server.desktop, "monitors", return_value=[monitor]):
            self.assertEqual(server.computer_use_indicator_state()["bounds"], (1920, -149, 1920, 1080))
            self.assertFalse(server.computer_use_indicator_state()["interacting"])
            session._last_desktop_action_at = time.monotonic()
            self.assertTrue(server.computer_use_indicator_state()["interacting"])
            session._last_desktop_action_at -= 10
            self.assertFalse(server.computer_use_indicator_state()["interacting"])
            server.desktop.paused = True
            self.assertTrue(server.computer_use_indicator_state()["paused"])
            session.status = "completed"
            self.assertEqual(server.computer_use_indicator_state(), {"active": False})

    def test_start_indicator_passes_filtered_env_with_agent_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server.os, "name", "nt"), \
                    patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-secret"}, clear=False), \
                    patch.object(server, "AGENT_TOKEN", "agent-token-fixture"), \
                    patch("computer_use_indicator.subprocess.Popen") as popen, \
                    patch.object(server, "IndicatorProcess"):
                server.start_indicator(tmp, 8888)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["PLINY_AGENT_TOKEN"], "agent-token-fixture")
            self.assertEqual(env["PYTHONUTF8"], "1")
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertTrue((Path(tmp) / "logs" / "indicator.log").exists())

    def test_indicator_http_endpoint_accepts_agent_token_and_pauses_without_auth_token_env(self):
        httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}/api/computer-use/indicator"
        try:
            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(server, "AGENT_TOKEN", "agent-token-fixture"):
                os.environ.pop("PLINY_AUTH_TOKEN", None)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base, timeout=5)
                self.assertEqual(denied.exception.code, 403)
                req = urllib.request.Request(base, headers={"Authorization": "Bearer agent-token-fixture"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    payload = json.load(resp)
                self.assertEqual(payload["active"], False)
                self.assertEqual(payload["server_pid"], os.getpid())
                with patch.object(server, "pause_computer_use_from_escape") as pause:
                    post = urllib.request.Request(
                        base, data=b"{}",
                        headers={"Authorization": "Bearer agent-token-fixture", "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(post, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                pause.assert_called_once()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
