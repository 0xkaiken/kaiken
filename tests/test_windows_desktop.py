import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from PIL import Image

import server
from windows_desktop import WindowsDesktop, DesktopUnavailable, DesktopFocusChanged


class WindowsDesktopTests(unittest.TestCase):
    def controller(self):
        control = WindowsDesktop()
        control.user = Mock()
        control._interactive = Mock()
        control.owner = "desktop-test"
        control.paused = False
        control.snapshot_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "screen.png"
        control.user.GetForegroundWindow.return_value = 123
        def cursor(ptr):
            ptr._obj.x = 50
            ptr._obj.y = 50
            return 1
        control.user.GetCursorPos.side_effect = cursor
        return control

    def test_selected_monitor_capture_and_coordinate_mapping(self):
        control = self.controller()
        control.monitor_id = "laptop"
        control.monitors = Mock(return_value=[{"id": "laptop", "left": -2000, "top": -100, "width": 2000, "height": 1000}])
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (2000, 1000))) as grab:
            image = control.screenshot()
        grab.assert_called_once_with(bbox=(-2000, -100, 0, 900), all_screens=True, include_layered_windows=True)
        self.assertEqual(Image.open(io.BytesIO(image)).size, (1280, 640))
        self.assertEqual(control.snapshot_path.read_bytes(), image)
        self.assertEqual(control.point(0, 0), (-2000, -100))
        self.assertEqual(control.point(1279, 639), (-1, 899))
        with self.assertRaises(ValueError):
            control.point(1280, 0)

    def test_capture_follows_a_dialog_the_app_opened_on_the_other_monitor(self):
        control = self.controller()
        control.monitor_id = "m1"
        control.target_window = 123
        control.monitors = Mock(return_value=[
            {"id": "m1", "primary": True, "left": 0, "top": 0, "width": 1920, "height": 1080},
            {"id": "m2", "primary": False, "left": 1920, "top": -149, "width": 1920, "height": 1080},
        ])
        control.user.GetForegroundWindow.return_value = 456
        def pid(hwnd, ptr):
            ptr._obj.value = 100
            return 1
        def rect(hwnd, ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 2000, 100, 2400, 500
            return 1
        control.user.GetWindowThreadProcessId.side_effect = pid
        control.user.GetWindowRect.side_effect = rect
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (1920, 1080))) as grab:
            control.capture()
        self.assertEqual(control.monitor_id, "m2")
        self.assertEqual(grab.call_args.kwargs["bbox"], (1920, -149, 3840, 931))

    def test_an_unowned_dialog_from_the_same_process_counts_as_the_target(self):
        control = self.controller()
        control.user.GetAncestor.return_value = 0
        def pid(hwnd, ptr):
            ptr._obj.value = 100 if int(hwnd) in (123, 456) else 200
            return 1
        control.user.GetWindowThreadProcessId.side_effect = pid
        self.assertTrue(control._belongs(456, 123))
        self.assertFalse(control._belongs(789, 123))

    def test_unicode_input_and_foreground_guard(self):
        control = self.controller()
        control._send = Mock()
        text = "Türkçe 🐉"
        control.action("type", {"window_id": 123, "text": text}, "desktop-test")
        sent = [call.kwargs["keyboard"][1] for call in control._send.call_args_list[::2]]
        self.assertEqual(b"".join(u.to_bytes(2, "little") for u in sent).decode("utf-16-le"), text)
        control._send.reset_mock()
        with self.assertRaises(DesktopFocusChanged):
            control.action("type", {"window_id": 456, "text": "wrong window"}, "desktop-test")
        control._send.assert_not_called()

    def test_take_control_survives_resume_and_blocks_input(self):
        control = self.controller()
        control.monitors = Mock(return_value=[{"id": "laptop", "primary": True}])
        control.pause(True)
        control.enable("desktop-test", "laptop")
        with self.assertRaises(PermissionError):
            control.action("press", {"window_id": 123, "key": "Enter"}, "desktop-test")
        self.assertTrue(control.status()["paused"])
        control.pause(False)
        self.assertFalse(control.status()["paused"])

    def test_desktop_origin_host_and_localhost_boundary(self):
        h = Mock(client_address=("127.0.0.1", 40000), headers={"Host": "localhost:8888"})
        with patch.object(server, "_check_auth", return_value=True):
            self.assertTrue(server.desktop_request_allowed(h))
            h.headers["Origin"] = "https://unrelated.test"
            self.assertFalse(server.desktop_request_allowed(h))
            h.headers = {"Host": "rebind.test:8888"}
            self.assertFalse(server.desktop_request_allowed(h))
            h.headers = {"Host": "localhost:8888"}
            h.client_address = ("192.0.2.1", 40000)
            self.assertFalse(server.desktop_request_allowed(h))

    def test_desktop_actions_require_running_owner(self):
        h = server.Handler.__new__(server.Handler)
        h.headers = {"Content-Type": "application/json"}
        h._read_body = Mock(return_value=json.dumps({"session_id": "missing", "text": "test"}))
        h._send_json = Mock()
        with patch.object(server, "desktop_request_allowed", return_value=True), patch.object(server, "active_sessions", {}), patch.object(server.desktop, "action") as action:
            h._api_desktop("type", mutate=True)
            self.assertEqual(h._send_json.call_args.args[1], 409)
            action.assert_not_called()

    def test_private_desktop_frames_are_not_sent_to_cross_origin_clients(self):
        local, other = io.BytesIO(), io.BytesIO()
        clients = [{"wfile": local, "desktop_allowed": True}, {"wfile": other, "desktop_allowed": False}]
        with patch.object(server, "sse_clients", clients):
            server.broadcast_sse("cu_screenshot", {"cu_mode": "desktop", "data": "fixture"})
        self.assertIn(b"fixture", local.getvalue())
        self.assertEqual(other.getvalue(), b"")

    def test_successful_desktop_action_updates_indicator_activity(self):
        session = Mock(status="running", computer_use=True, cu_mode="desktop")
        h = server.Handler.__new__(server.Handler)
        h.headers = {"Content-Type": "application/json"}
        h._read_body = Mock(return_value=json.dumps({"session_id": "test", "window_id": 123, "key": "Enter"}))
        h._send_json = Mock()
        with patch.object(server, "desktop_request_allowed", return_value=True), patch.object(server, "_auth_kind", return_value="operator"), patch.object(server, "active_sessions", {"test": session}), patch.object(server, "_cu_active_session_id", "test"), patch.object(server.desktop, "action", return_value={"ok": True}), patch.object(server, "broadcast_sse"):
            h._api_desktop("press", mutate=True)
        self.assertGreater(session._last_desktop_action_at, 0)
        self.assertFalse(session._desktop_action_in_progress)

    def test_final_action_can_return_fresh_snapshot_for_direct_image_read(self):
        session = Mock(status="running", computer_use=True, cu_mode="desktop")
        h = server.Handler.__new__(server.Handler)
        h.headers = {"Content-Type": "application/json"}
        h._read_body = Mock(return_value=json.dumps({"session_id": "test", "window_id": 123, "key": "Ctrl+S", "screenshot": True}))
        h._send_json = Mock()
        with patch.object(server, "desktop_request_allowed", return_value=True), patch.object(server, "_auth_kind", return_value="operator"), patch.object(server, "active_sessions", {"test": session}), patch.object(server, "_cu_active_session_id", "test"), patch.object(server.desktop, "action", return_value={"ok": True}), patch.object(server.desktop, "capture") as capture, patch.object(server, "broadcast_sse"):
            h._api_desktop("press", mutate=True)
        capture.assert_called_once()
        self.assertEqual(h._send_json.call_args.args[0]["screenshot_path"], str(server.desktop.snapshot_path))

    def test_focus_loss_is_not_reported_as_operator_pause(self):
        cases = [(DesktopFocusChanged("focus moved"), False, 409, "focus_changed"),
                 (PermissionError("operator paused"), True, 423, "desktop_paused"),
                 (DesktopUnavailable("secure desktop"), False, 503, "desktop_unavailable")]
        for error, paused, status, code in cases:
            with self.subTest(code=code):
                session = Mock(status="running", computer_use=True, cu_mode="desktop")
                h = server.Handler.__new__(server.Handler)
                h.headers = {"Content-Type": "application/json"}
                h._read_body = Mock(return_value=json.dumps({"session_id": "test", "window_id": 123, "key": "Enter"}))
                h._send_json = Mock()
                with patch.object(server, "desktop_request_allowed", return_value=True), patch.object(server, "_auth_kind", return_value="operator"), patch.object(server, "active_sessions", {"test": session}), patch.object(server, "_cu_active_session_id", "test"), patch.object(server.desktop, "paused", paused), patch.object(server.desktop, "action", side_effect=error):
                    h._api_desktop("press", mutate=True)
                body, actual_status = h._send_json.call_args.args
                self.assertEqual(actual_status, status)
                self.assertEqual(body["code"], code)
                self.assertFalse(session._desktop_action_in_progress)

    def test_rejected_windows_input_is_reported_as_failure(self):
        control = self.controller()
        control.user.SendInput.return_value = 0
        with self.assertRaises(DesktopUnavailable):
            control._send(keyboard=(65, 0, 0))

    def test_focus_fallback_clicks_only_the_verified_target_window(self):
        for visible in (True, False):
            with self.subTest(visible=visible):
                control = self.controller()
                control._send = Mock()
                control.monitor_id = "laptop"
                control.monitors = Mock(return_value=[{"id": "laptop", "left": 0, "top": 0, "width": 1920, "height": 1080}])
                def rect(hwnd, ptr):
                    ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 100, 100, 500, 500
                    return 1
                control.user.GetWindowRect.side_effect = rect
                control.user.GetForegroundWindow.side_effect = [456, 123]
                control.user.GetAncestor.return_value = 123 if visible else 456
                if visible:
                    control.action("activate", {"window_id": 123}, "desktop-test")
                    self.assertEqual(control._send.call_count, 2)
                else:
                    with self.assertRaises(DesktopUnavailable):
                        control.action("activate", {"window_id": 123}, "desktop-test")
                    control._send.assert_not_called()

    def test_activation_maximizes_without_restoring_an_already_selected_window(self):
        for left in (100, 2200):
            with self.subTest(window_left=left):
                control = self.controller()
                control.monitor_id = "laptop"
                control.monitors = Mock(return_value=[{"id": "laptop", "left": 0, "top": 0, "width": 1920, "height": 1080}])
                def rect(hwnd, ptr):
                    ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = left, 100, left+400, 500
                    return 1
                control.user.GetWindowRect.side_effect = rect
                control.action("activate", {"window_id": 123}, "desktop-test")
                self.assertEqual(control.user.ShowWindow.call_args.args, (123, 3))
                if left == 100:
                    control.user.ShowWindow.assert_called_once_with(123, 3)
                else:
                    control.user.SetWindowPos.assert_called_once()

    def test_desktop_options_survive_history_resume(self):
        record = {"id": "desktop-resume", "status": "error", "computer_use": True,
                  "cu_mode": "desktop", "cu_monitor": "laptop", "runtime": "opencode",
                  "model": "opencode-go/glm-5.3-flash", "claude_session_id": "session-fixture"}
        h = server.Handler.__new__(server.Handler)
        h._read_body = Mock(return_value=json.dumps({"session_id": record["id"], "message": "Read the test window"}))
        h._send_json = Mock()
        for opts, expected in (({}, False), ({"cu_desktop_python": True}, True)):
            with self.subTest(opts=opts), patch.object(server, "load_json", return_value=[{**record, **opts}]), patch.object(server, "active_sessions", {}), patch.object(server, "desktop_request_allowed", return_value=True), patch.object(server.Session, "send_message"), patch.object(server, "broadcast_sse"), patch.object(server, "post_comms_message") as comms, patch.object(server, "_check_operator_auth", return_value=True):
                h._api_session_message()
                restored = server.active_sessions[record["id"]]
                self.assertEqual((restored.cu_mode, restored.cu_monitor), ("desktop", "laptop"))
                self.assertIs(restored.cu_desktop_python, expected)
                self.assertEqual(restored.claude_session_id, "session-fixture")
                self.assertTrue(h._send_json.call_args.args[0]["resumed"])
                comms.assert_called_once_with("operator", "Read the test window", channel="session:desktop-resume")

    def test_press_supports_extended_keys_and_punctuation_shortcuts(self):
        control = self.controller()
        control._send = Mock()
        control.target_window = 123
        control.action("press", {"key": "Ctrl+Left"}, "desktop-test")
        calls = [c.kwargs["keyboard"] for c in control._send.call_args_list]
        self.assertEqual(calls, [(0x11, 0, 0), (0x25, 0, 1), (0x25, 0, 3), (0x11, 0, 2)])
        control._send.reset_mock()
        for combo, expected_vk in (("Ctrl+-", 0xBD), ("Ctrl+=", 0xBB), ("Ctrl++", 0xBB)):
            control._send.reset_mock()
            control.action("press", {"key": combo}, "desktop-test")
            calls = [c.kwargs["keyboard"] for c in control._send.call_args_list]
            self.assertEqual(calls, [(0x11, 0, 0), (expected_vk, 0, 0), (expected_vk, 0, 2), (0x11, 0, 2)])

    def test_missing_executable_reports_invalid_action_not_unavailable(self):
        control = self.controller()
        with patch("windows_desktop.subprocess.Popen", side_effect=FileNotFoundError("no such exe")):
            result = control.batch([{"action": "launch", "app": "C:/Missing/app.exe"}], "desktop-test", settle_ms=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["code"], "invalid_action")
        self.assertIn("Application not found", result["results"][0]["error"])

    def test_launch_accepts_one_string_argument(self):
        # A URL passed as a bare string used to fail validation and burn a turn.
        control = self.controller()
        with patch("windows_desktop.subprocess.Popen") as popen:
            popen.return_value = Mock(pid=42)
            result = control.action("launch", {"app": "C:/Tools/app.exe",
                                               "args": "https://example.com"}, "desktop-test")
        self.assertTrue(result["ok"])
        popen.assert_called_once_with(["C:/Tools/app.exe", "https://example.com"], shell=False)

    def test_wait_accepts_ten_seconds(self):
        # Agents routinely ask for 6s; rejecting it cost a whole step.
        control = self.controller()
        control._wait = Mock()
        result = control.batch([{"action": "wait", "ms": 6000}], "desktop-test", settle_ms=0)
        self.assertTrue(result["ok"])
        self.assertEqual(control._wait.call_args.args[0], 6000.0)

    def test_pointer_modifiers_are_held_across_the_click(self):
        # OpenAI's computer contract calls this `keys`; the SolidWorks agent
        # sent `modifiers`. Both must produce a real ctrl/shift-click.
        for field, value, vk in (("keys", ["CTRL"], 0x11), ("modifiers", ["Shift"], 0x10)):
            with self.subTest(field=field):
                control = self.controller()
                control._send = Mock()
                control.frame = {"frame_id": "f1", "width": 100, "height": 100, "left": 0, "top": 0,
                                 "native_width": 100, "native_height": 100}
                control.action("click", {"x": 10, "y": 20, field: value}, "desktop-test")
                calls = control._send.call_args_list
                self.assertEqual(calls[0].kwargs["keyboard"], (vk, 0, 0))
                self.assertEqual(calls[-1].kwargs["keyboard"], (vk, 0, 2))
                self.assertEqual(sum(1 for c in calls if "mouse" in c.kwargs), 2)

    def test_small_window_title_click_stays_inside_window_bounds(self):
        control = self.controller()
        control._send = Mock()
        control.monitor_id = "laptop"
        control.monitors = Mock(return_value=[{"id": "laptop", "left": 0, "top": 0, "width": 1920, "height": 1080}])
        def rect(hwnd, ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 100, 100, 300, 110
            return 1
        control.user.GetWindowRect.side_effect = rect
        control.user.GetForegroundWindow.side_effect = [456, 123]
        control.user.GetAncestor.return_value = 123
        control.action("activate", {"window_id": 123}, "desktop-test")
        control.user.SetCursorPos.assert_called_with(200, 105)
        self.assertGreater(control.user.SetCursorPos.call_count, 1)

    def test_desktop_start_failure_removes_session_from_active_sessions(self):
        session = server.Session("desktop-fail", "Open Notepad", runtime="codex", computer_use=True, cu_mode="desktop")
        server.active_sessions[session.session_id] = session
        self.addCleanup(server.active_sessions.pop, session.session_id, None)
        with patch.object(server, "_cu_acquire", return_value=True), \
                patch.object(server, "_cu_release") as release, \
                patch.object(server.desktop, "enable", side_effect=DesktopUnavailable("locked")), \
                patch.object(session, "_save_log"), patch.object(session, "_save_session_record"), \
                patch.object(session, "_initial_prompt", return_value="Open Notepad"), \
                patch.object(server, "broadcast_sse"):
            session._start_inner()
        self.assertEqual(session.status, "error")
        self.assertNotIn(session.session_id, server.active_sessions)
        release.assert_called_once_with("desktop-fail")

    def test_ouroboros_launch_forwards_desktop_mode_and_enforces_local_boundary(self):
        h = server.Handler.__new__(server.Handler)
        h._read_body = Mock(return_value=json.dumps({"goal": "Test", "computer_use": True, "cu_mode": "desktop", "cu_monitor": "laptop", "cu_desktop_python": True}))
        h._send_json = Mock()
        with patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "desktop_request_allowed", return_value=False):
            h._api_ouroboros_launch()
        self.assertEqual(h._send_json.call_args.args[1], 403)
        h._send_json.reset_mock()
        with patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "desktop_request_allowed", return_value=True), \
                patch.object(server, "ouroboros_create_campaign", return_value={"id": "camp-1"}) as create:
            h._api_ouroboros_launch()
        self.assertEqual(create.call_args.kwargs["cu_mode"], "desktop")
        self.assertEqual(create.call_args.kwargs["cu_monitor"], "laptop")
        self.assertIs(create.call_args.kwargs["cu_desktop_python"], True)


if __name__ == "__main__":
    unittest.main()
