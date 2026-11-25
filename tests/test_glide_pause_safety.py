"""Mocked input only: regression cases for PR #7's post-glide safety checks."""
import threading
import time
import unittest
from unittest.mock import Mock, patch

from windows_desktop import StaleFrame, WindowsDesktop


class GlidePauseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.control = WindowsDesktop()
        self.control.user = Mock()
        self.control._interactive = Mock()
        self.control._send = Mock()
        self.control.owner = "test"
        self.control.paused = False
        self.control.target_window = 123
        self.control._belongs = Mock(return_value=True)
        self.position = [100, 100]
        self.rect = [0, 0, 1920, 1080]
        def get_cursor(ptr):
            ptr._obj.x, ptr._obj.y = self.position
            return 1
        def set_cursor(x, y):
            self.position[:] = x, y
            return 1
        def get_rect(hwnd, ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = self.rect
            return 1
        self.control.user.GetCursorPos.side_effect = get_cursor
        self.control.user.SetCursorPos.side_effect = set_cursor
        self.control.user.GetWindowRect.side_effect = get_rect
        self.control.user.GetForegroundWindow.return_value = 123
        self.control.user.WindowFromPoint.return_value = 123
        self.control.user.GetAncestor.return_value = 123
        self.control.monitor_id = "primary"
        self.control.monitors = Mock(return_value=[{"id": "primary", "left": 0, "top": 0, "width": 1920, "height": 1080}])
        self.control.frames["original"] = {
            "frame_id": "original", "width": 1920, "height": 1080,
            "native_width": 1920, "native_height": 1080, "left": 0, "top": 0,
            "monitor_id": "primary", "_mono": time.monotonic(),
            "_target": 123, "_target_rect": self.rect.copy(),
        }
        self.control.frame = self.control.frames["original"]
        self.enterContext(patch.dict("os.environ", {"PLINY_CU_POINTER_GLIDE": "1"}))

    def click(self):
        return self.control.action("click", {"x": 1700, "y": 900, "frame_id": "original"}, "test")

    def activate(self):
        self.control.user.GetForegroundWindow.side_effect = [456, 123]
        return self.control.action("activate", {"window_id": 123}, "test")

    def interrupt_at_first_sleep(self, interrupt):
        fired = False
        def sleep(_):
            nonlocal fired
            if not fired:
                fired = True
                interrupt()
        self.enterContext(patch("windows_desktop.time.sleep", side_effect=sleep))

    def test_operator_corner_during_glide_aborts_without_buttons(self):
        self.interrupt_at_first_sleep(lambda: self.position.__setitem__(slice(None), [0, 0]))
        with self.assertRaises(PermissionError):
            self.click()
        self.control._send.assert_not_called()
        self.assertTrue(self.control.operator_paused)

    def test_glides_own_origin_step_does_not_pause(self):
        self.position[:] = 150, 150
        with patch("windows_desktop.time.sleep"):
            self.control._glide_pointer(-150, -150)
        self.assertIn((0, 0), [c.args for c in self.control.user.SetCursorPos.call_args_list])
        self.assertFalse(self.control.paused)

    def test_click_at_own_origin_endpoint_does_not_pause(self):
        with patch("windows_desktop.time.sleep"):
            self.control.action("click", {"x": 0, "y": 0, "frame_id": "original"}, "test")
        self.assertFalse(self.control.paused)
        self.assertEqual(self.control._send.call_count, 2)

    def test_stable_frame_and_no_frame_id_clicks_still_work(self):
        with patch("windows_desktop.time.sleep"):
            self.click()
            self.control.action("click", {"x": 1500, "y": 800}, "test")
        self.assertEqual(self.control._send.call_count, 4)
        self.assertFalse(self.control.paused)

    def test_corner_after_glide_is_checked_again_before_button(self):
        checks = 0
        def target(*args):
            nonlocal checks
            checks += 1
            if checks == 2:
                self.position[:] = 0, 0
        self.control._check_target = target
        with patch("windows_desktop.time.sleep"), self.assertRaises(PermissionError):
            self.click()
        self.control._send.assert_not_called()

    def test_corner_between_double_clicks_prevents_second_button_down(self):
        def sleep(_):
            if self.control._send.call_count == 2:
                self.position[:] = 0, 0
        with patch("windows_desktop.time.sleep", side_effect=sleep), self.assertRaises(PermissionError):
            self.control.action("click", {"x": 1700, "y": 900, "count": 2}, "test")
        self.assertEqual(self.control._send.call_count, 2)
        self.assertTrue(self.control.paused)

    def test_original_frame_is_revalidated_not_replaced_by_latest(self):
        def change_frame():
            self.rect[2] = 1800
            self.control.frame = {**self.control.frame, "frame_id": "fresh", "_target_rect": self.rect.copy()}
            self.control.frames["fresh"] = self.control.frame
        self.interrupt_at_first_sleep(change_frame)
        with self.assertRaises(StaleFrame):
            self.click()
        self.control._send.assert_not_called()

    def test_scroll_revalidates_frame_before_wheel_input(self):
        self.interrupt_at_first_sleep(lambda: self.rect.__setitem__(2, 1800))
        with self.assertRaises(StaleFrame):
            self.control.action("scroll", {"x": 1700, "y": 900, "frame_id": "original"}, "test")
        self.control._send.assert_not_called()

    def test_drag_revalidates_frame_before_button_down(self):
        self.interrupt_at_first_sleep(lambda: self.rect.__setitem__(2, 1800))
        with self.assertRaises(StaleFrame):
            self.control.action("drag", {"x": 1700, "y": 900, "to_x": 1750, "to_y": 950, "frame_id": "original"}, "test")
        self.control._send.assert_not_called()

    def pause_during_glide(self, action):
        attempted = threading.Event()
        lock = self.control.lock
        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == "take-control":
                    attempted.set()
                lock.acquire()
            def __exit__(self, *args):
                lock.release()
        self.control.lock = ObservedLock()
        worker = threading.Thread(target=lambda: self.control.pause(True), name="take-control")
        def request_pause():
            worker.start()
            self.assertTrue(attempted.wait(2), "pause never attempted to acquire the action lock")
        self.interrupt_at_first_sleep(request_pause)
        try:
            try:
                action()
            except PermissionError:
                pass
        finally:
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.control._send.assert_not_called()
        self.assertTrue(self.control.operator_paused)

    def test_take_control_during_glide_prevents_pending_click(self):
        self.pause_during_glide(self.click)

    def test_moved_frame_after_glide_rejected_without_buttons(self):
        self.interrupt_at_first_sleep(lambda: self.rect.__setitem__(2, 1800))
        with self.assertRaises(StaleFrame):
            self.click()
        self.control._send.assert_not_called()

    def test_activate_corner_during_glide_aborts_without_buttons(self):
        self.interrupt_at_first_sleep(lambda: self.position.__setitem__(slice(None), [0, 0]))
        with self.assertRaises(PermissionError):
            self.activate()
        self.control._send.assert_not_called()

    def test_activate_take_control_during_glide_prevents_title_click(self):
        self.pause_during_glide(self.activate)

    def test_activate_moved_titlebar_rejected_without_buttons(self):
        self.interrupt_at_first_sleep(lambda: self.rect.__setitem__(2, 1800))
        with self.assertRaises(StaleFrame):
            self.activate()
        self.control._send.assert_not_called()
