import time
import unittest
from unittest.mock import Mock, patch

from computer_use_indicator import ComputerUseIndicator
from desktop_native_cursor import NativeCursor, PointerDisplay, STANDARD_CURSORS
from windows_desktop import WindowsDesktop
import server


class NativeCursorTests(unittest.TestCase):
    def native(self):
        user = Mock()
        user.CreateCursor.side_effect = range(1, 100)
        user.SetSystemCursor.return_value = 1
        user.SystemParametersInfoW.return_value = 1
        native = NativeCursor(user)
        self.addCleanup(native.close)
        return native, user

    def test_startup_restores_then_hide_replaces_every_standard_shape(self):
        native, user = self.native()
        user.SystemParametersInfoW.assert_called_once_with(0x57, 0, None, 0)
        self.assertTrue(native.hide())
        self.assertTrue(native.hidden)
        self.assertEqual([c.args[1] for c in user.SetSystemCursor.call_args_list], list(STANDARD_CURSORS))
        for call in user.CreateCursor.call_args_list:
            self.assertEqual(call.args[1:5], (0, 0, 32, 32))
            self.assertEqual(call.args[5].raw[:128], b"\xff"*128)
            self.assertEqual(call.args[6].raw[:128], b"\x00"*128)
        native.hide()
        self.assertEqual(user.SetSystemCursor.call_count, len(STANDARD_CURSORS))
        native.restore()
        self.assertFalse(native.hidden)
        self.assertEqual(user.SystemParametersInfoW.call_count, 2)

    def test_partial_hide_failure_restores_scheme_and_frees_unconsumed_cursor(self):
        native, user = self.native()
        user.SetSystemCursor.side_effect = [1, 0]
        with self.assertLogs(level="ERROR"):
            self.assertFalse(native.hide())
        self.assertFalse(native.hidden)
        user.DestroyCursor.assert_called_once_with(2)
        self.assertEqual(user.SystemParametersInfoW.call_count, 2)

    def test_failed_startup_recovery_is_retried_while_idle(self):
        user = Mock()
        user.SystemParametersInfoW.return_value = 0
        with self.assertLogs(level="WARNING"):
            native = NativeCursor(user)
        self.addCleanup(native.close)
        self.assertTrue(native._restore_pending)
        self.assertFalse(native.hidden)
        user.SystemParametersInfoW.return_value = 1
        self.assertTrue(native.restore())
        self.assertFalse(native.hidden)

    def test_restore_failure_remains_pending_for_retry(self):
        native, user = self.native()
        native.hide()
        user.SystemParametersInfoW.side_effect = [0, 1]
        with self.assertLogs(level="WARNING"):
            self.assertFalse(native.restore())
        self.assertTrue(native.hidden)
        self.assertTrue(native.restore())
        self.assertFalse(native.hidden)
        user.SystemParametersInfoW.side_effect = None


class PointerDisplayTests(unittest.TestCase):
    def setUp(self):
        self.user = Mock()
        self.position = [100, 100]
        def position(ptr):
            ptr._obj.x, ptr._obj.y = self.position
            return 1
        self.user.GetCursorPos.side_effect = position
        self.native = NativeCursor(self.user)
        self.addCleanup(self.native.close)
        self.cursor = Mock(visible=False)
        self.cursor.hide.side_effect = lambda: setattr(self.cursor, "visible", False)
        self.cursor.update.side_effect = lambda: setattr(self.cursor, "visible", True)
        self.display = PointerDisplay(self.cursor, self.native, self.user)
        self.state = {"active": True, "mode": "desktop", "paused": False, "pointer": {}}
        self.clock = self.enterContext(patch("desktop_native_cursor.time.monotonic", return_value=100))

    def activity(self, running=False, serial=1, stamp=100):
        self.state["pointer"] = {"serial": serial, "owner": "test", "running": running,
                                 "updated_at": stamp, "position": (100, 100)}

    def test_mission_active_but_idle_never_replaces_cursor(self):
        self.display.update(self.state)
        self.assertFalse(self.cursor.visible)
        self.user.SetSystemCursor.assert_not_called()

    def test_running_and_linger_then_restore(self):
        self.activity(running=True)
        self.position[:] = 120, 120
        self.display.update(self.state)
        self.assertTrue(self.cursor.visible)
        self.assertTrue(self.native.hidden)
        self.activity(running=False)
        self.position[:] = 100, 100
        self.clock.return_value = 101.4
        self.display.update(self.state)
        self.assertTrue(self.cursor.visible)
        self.clock.return_value = 101.6
        self.display.update(self.state)
        self.assertFalse(self.cursor.visible)
        self.assertFalse(self.native.hidden)

    def test_operator_move_cancels_linger_until_a_new_action(self):
        self.activity()
        self.display.update(self.state)
        self.position[:] = 110, 100
        self.display.update(self.state)
        self.assertFalse(self.cursor.visible)
        self.assertFalse(self.native.hidden)
        self.position[:] = 100, 100
        self.display.update(self.state)
        self.assertFalse(self.cursor.visible)
        self.activity(serial=2)
        self.display.update(self.state)
        self.assertTrue(self.cursor.visible)

    def test_pause_release_and_shutdown_restore_native_cursor(self):
        for stop in ("pause", "release", "shutdown"):
            with self.subTest(stop=stop):
                self.state.update(active=True, paused=False)
                self.activity()
                self.display.update(self.state)
                self.assertTrue(self.native.hidden)
                if stop == "pause":
                    self.state["paused"] = True
                    self.display.update(self.state)
                elif stop == "release":
                    self.state["active"] = False
                    self.display.update(self.state)
                else:
                    self.display.close()
                self.assertFalse(self.cursor.visible)
                self.assertFalse(self.native.hidden)

    def test_escape_restores_without_waiting_for_http_poll(self):
        self.activity()
        self.display.update(self.state)
        indicator = ComputerUseIndicator(lambda: self.state, Mock())
        indicator._armed = True
        indicator._pointer_display = self.display
        indicator.physical_escape(0x1B, 0)
        self.assertFalse(self.native.hidden)
        self.assertFalse(self.cursor.visible)

    def test_browser_or_missing_activity_never_shows_cursor(self):
        self.activity()
        self.state["mode"] = "browser"
        self.display.update(self.state)
        self.assertFalse(self.cursor.visible)
        self.user.SetSystemCursor.assert_not_called()


class PointerActivityTests(unittest.TestCase):
    def setUp(self):
        self.control = WindowsDesktop()
        self.control.user = Mock()
        self.control._interactive = Mock()
        self.control.owner, self.control.paused = "test", False
        self.position = [100, 100]
        def position(ptr):
            ptr._obj.x, ptr._obj.y = self.position
            return 1
        def move(x, y):
            self.position[:] = x, y
            return 1
        self.control.user.GetCursorPos.side_effect = position
        self.control.user.SetCursorPos.side_effect = move
        self.control.user.SendInput.return_value = 1
        self.control._frame_for = Mock(return_value={})
        self.control.point = Mock(return_value=(500, 500))
        self.control._check_target = Mock()

    def test_snapshot_is_running_during_real_action_and_finishes_at_last_position(self):
        snapshots = []
        with patch("windows_desktop.time.sleep", side_effect=lambda _: snapshots.append(self.control.pointer_activity())):
            self.control.action("click", {"x": 10, "y": 10}, "test")
        self.assertTrue(all(s["running"] for s in snapshots))
        final = self.control.pointer_activity()
        self.assertFalse(final["running"])
        self.assertEqual(final["position"], (500, 500))
        self.assertEqual(final["owner"], "test")
        self.assertTrue(snapshots[0]["running"])
        self.control.action("clear_target", {}, "test")
        self.assertEqual(self.control.pointer_activity(), final)

    def test_pause_and_release_clear_activity(self):
        with patch("windows_desktop.time.sleep"):
            self.control.action("move", {"x": 10, "y": 10}, "test")
        self.control.pause(True)
        self.assertEqual(self.control.pointer_activity(), {})
        self.control._pointer_state = {"running": False}
        self.control._clear_captures = Mock()
        self.control.session_dir = Mock(return_value=None)
        self.control.release("test")
        self.assertEqual(self.control.pointer_activity(), {})

    def test_server_indicator_exposes_pointer_snapshot_without_action_lock(self):
        session = Mock(session_id="test", computer_use=True, status="running", cu_mode="desktop",
                       _last_desktop_action_at=0.0, _desktop_action_in_progress=False)
        snapshot = {"serial": 1, "running": True, "position": (500, 500), "updated_at": time.monotonic()}
        with patch.object(server, "active_sessions", {"test": session}), \
                patch.object(server, "_cu_active_session_id", "test"), \
                patch.object(server, "_indicator_monitor_key", ("test", "desktop", None)), \
                patch.object(server.desktop, "monitor_id", None), \
                patch.object(server.desktop, "pointer_activity", return_value=snapshot):
            self.assertEqual(server.computer_use_indicator_state()["pointer"], snapshot)
