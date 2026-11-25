import unittest
from unittest.mock import Mock, patch

from windows_desktop import ActionCancelled, DesktopUnavailable, TargetMismatch, WindowsDesktop


class PointerGlideTests(unittest.TestCase):
    def control(self):
        control = WindowsDesktop()
        control.user = Mock()
        control.paused = False
        def position(ptr):
            ptr._obj.x, ptr._obj.y = 50, 60
            return 1
        control.user.GetCursorPos.side_effect = position
        return control

    def test_path_is_eased_monotonic_bounded_and_exact(self):
        control = self.control()
        with patch.dict("os.environ", {"PLINY_CU_POINTER_GLIDE": "1"}), patch("windows_desktop.time.sleep") as sleep:
            control._glide_pointer(1050, -940)
        points = [call.args for call in control.user.SetCursorPos.call_args_list]
        self.assertGreater(len(points), 2)
        self.assertLessEqual(len(points), 15)
        self.assertLess(points[0][0]-50, 30)
        self.assertEqual(points[-1], (1050, -940))
        self.assertEqual([x for x, _ in points], sorted(x for x, _ in points))
        self.assertEqual([y for _, y in points], sorted((y for _, y in points), reverse=True))
        self.assertLessEqual(sum(c.args[0] for c in sleep.call_args_list), 0.25)

    def test_disable_and_tiny_distance_are_instant(self):
        for env, target in (("0", (1000, 2000)), ("1", (51, 60)), ("1", (50, 60))):
            control = self.control()
            with patch.dict("os.environ", {"PLINY_CU_POINTER_GLIDE": env}), patch("windows_desktop.time.sleep") as sleep:
                control._glide_pointer(*target)
            control.user.SetCursorPos.assert_called_once_with(*target)
            sleep.assert_not_called()

    def test_checkpoint_cancellation_pause_and_deadline_interrupt(self):
        for error in (ActionCancelled("stop"), PermissionError("pause"), TimeoutError("deadline")):
            control = self.control()
            control._checkpoint = Mock(side_effect=[None, error])
            with patch("windows_desktop.time.sleep"), self.assertRaises(type(error)):
                control._glide_pointer(1000, 1000, 123)
            self.assertEqual(control.user.SetCursorPos.call_count, 1)
            self.assertNotEqual(control.user.SetCursorPos.call_args.args, (1000, 1000))
            control._checkpoint.assert_called_with(123, "pointer glide")

    def test_api_errors_are_reported(self):
        control = self.control()
        control.user.SetCursorPos.return_value = 0
        with self.assertRaises(DesktopUnavailable):
            control._glide_pointer(100, 100)
        control.user.GetCursorPos.side_effect = None
        control.user.GetCursorPos.return_value = 0
        with self.assertRaises(DesktopUnavailable):
            control._glide_pointer(100, 100)

    def test_target_is_rechecked_after_glide_before_button_press(self):
        control = self.control()
        control._interactive = Mock()
        control.owner = "test"
        control._frame_for = Mock(return_value={})
        control.point = Mock(return_value=(100, 100))
        control._send = Mock()
        control._check_target = Mock(side_effect=[None, TargetMismatch("covered during glide")])
        with patch("windows_desktop.time.sleep"), self.assertRaises(TargetMismatch):
            control.action("click", {"x": 100, "y": 100}, "test")
        control._send.assert_not_called()
        control.user.SetCursorPos.assert_called_with(100, 100)

    def test_crossing_origin_does_not_trigger_operator_pause(self):
        control = self.control()
        with patch("windows_desktop.time.sleep"):
            control._glide_pointer(-50, -60)
        self.assertFalse(control.paused)
        control.user.SetCursorPos.assert_called_with(-50, -60)
