import ctypes
import unittest
from unittest.mock import Mock, patch

from desktop_cursor import AgentCursor, HOTSPOT, compose_frame, cursor_image
from desktop_glow import ScreenEdgeGlow, exclude_from_capture, WDA_EXCLUDEFROMCAPTURE


class CursorImageTests(unittest.TestCase):
    def test_arrow_size_hotspot_and_paused_palette(self):
        for paused in (False, True):
            image = cursor_image(paused)
            self.assertGreater(image.width, 32)
            self.assertGreater(image.height, 32)
            self.assertEqual(image.getpixel(HOTSPOT)[3], 255)
            self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(cursor_image(True).getpixel(HOTSPOT), (255, 176, 0, 255))

    def test_empty_frame_is_small_and_tip_is_at_screen_position(self):
        frame, (left, top) = compose_frame((-100, 200), [], [], 1)
        self.assertLess(frame.width, 100)
        self.assertLess(frame.height, 100)
        self.assertEqual(frame.getpixel((-100-left, 200-top))[3], 255)

    def test_trail_fades_with_age_and_expires(self):
        def alpha(age):
            frame, (left, top) = compose_frame((200, 200), [(20, 20, 1-age)], [], 1)
            return frame.getpixel((20-left, 20-top))[3]
        self.assertGreater(alpha(0.05), alpha(0.3))
        expired = compose_frame((200, 200), [(20, 20, 0)], [], 1)
        empty = compose_frame((200, 200), [], [], 1)
        self.assertEqual(expired[0].tobytes(), empty[0].tobytes())
        self.assertEqual(expired[1], empty[1])

    def test_ripple_grows_and_fades_then_expires(self):
        def ring(age):
            frame, (left, top) = compose_frame((200, 200), [], [(20, 20, 1-age)], 1)
            pixels = [(x+left, a) for y in range(55) for x in range(min(frame.width, 90))
                      if (a := frame.getpixel((x, y))[3])]
            return max(x for x, _ in pixels), max(a for _, a in pixels)
        early_radius, early_alpha = ring(0.05)
        late_radius, late_alpha = ring(0.35)
        self.assertGreater(late_radius, early_radius)
        self.assertLess(late_alpha, early_alpha)
        self.assertEqual(compose_frame((0, 0), [], [(20, 20, 0)], 1)[0].tobytes(),
                         compose_frame((0, 0), [], [], 1)[0].tobytes())

    def test_capture_opt_in_skips_affinity_for_all_hud_users(self):
        user = Mock()
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "1"}):
            self.assertFalse(exclude_from_capture(user, 123))
        user.SetWindowDisplayAffinity.assert_not_called()
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "0"}):
            self.assertTrue(exclude_from_capture(user, 123))
        user.SetWindowDisplayAffinity.assert_called_once_with(123, WDA_EXCLUDEFROMCAPTURE)


class CursorUpdateTests(unittest.TestCase):
    def cursor(self):
        cursor = AgentCursor.__new__(AgentCursor)
        cursor.user = Mock()
        def position(ptr):
            ptr._obj.x, ptr._obj.y = 100, 100
            return True
        cursor.user.GetCursorPos.side_effect = position
        cursor.user.GetAsyncKeyState.return_value = 0
        cursor.hwnd, cursor.visible = 123, True
        cursor.points, cursor.ripples = [], []
        cursor.position, cursor.buttons, cursor.paused = (100, 100), (False,)*3, False
        cursor._paint = Mock()
        cursor._ensure_capture_excluded = Mock()
        return cursor

    def test_idle_skips_paint_and_short_injected_click_starts_ripple(self):
        cursor = self.cursor()
        cursor.update()
        cursor._paint.assert_not_called()
        cursor._ensure_capture_excluded.assert_called_once()
        cursor.user.GetAsyncKeyState.side_effect = [1, 0, 0]
        cursor.update()
        self.assertEqual(len(cursor.ripples), 1)
        cursor._paint.assert_called_once()

    def test_failed_position_read_does_not_paint(self):
        cursor = self.cursor()
        cursor.user.GetCursorPos.side_effect = None
        cursor.user.GetCursorPos.return_value = 0
        cursor.update()
        cursor._paint.assert_not_called()

    def test_hide_clears_history_and_expiry_repaints_once(self):
        cursor = self.cursor()
        cursor.points = [(80, 80, 0)]
        cursor.update()
        cursor._paint.assert_called_once()
        cursor.update()
        cursor._paint.assert_called_once()
        cursor.ripples = [(100, 100, 0)]
        cursor.hide()
        self.assertFalse(cursor.visible)
        self.assertEqual(cursor.ripples, [])
        self.assertIsNone(cursor.position)


class LayeredCaptureTests(unittest.TestCase):
    def test_constructor_sets_affinity_before_enabling_layered_surface(self):
        user = Mock()
        user.CreateWindowExW.return_value = 123
        user.GetWindowLongPtrW.return_value = 0x080000a8
        user.GetWindowLongW.return_value = 0x080000a8
        with patch("desktop_glow.ctypes.WinDLL", create=True), \
                patch("desktop_glow.ctypes.WINFUNCTYPE", ctypes.CFUNCTYPE, create=True), \
                patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "0"}):
            ScreenEdgeGlow(user, 1, 0)
        self.assertEqual(user.CreateWindowExW.call_args.args[0] & 0x80000, 0)
        calls = [c[0] for c in user.mock_calls]
        setter = "SetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "SetWindowLongW"
        self.assertLess(calls.index("SetWindowDisplayAffinity"), calls.index(setter))
        self.assertTrue(getattr(user, setter).call_args.args[2] & 0x80000)

    def glow(self, values):
        glow = ScreenEdgeGlow.__new__(ScreenEdgeGlow)
        glow.user = Mock()
        glow.hwnd, glow.visible = 123, False
        glow.signature = None
        glow._capture_warning = False
        glow._paint = Mock()
        values = iter(values)
        def affinity(hwnd, ptr):
            value = next(values)
            if value is None:
                return 0
            ptr._obj.value = value
            return 1
        glow.user.GetWindowDisplayAffinity.side_effect = affinity
        return glow

    def test_show_verifies_after_paint_and_show_and_reapplies_missing_affinity(self):
        glow = self.glow([0, 0x11])
        order = Mock()
        order.attach_mock(glow._paint, "paint")
        order.attach_mock(glow.user.ShowWindow, "show")
        order.attach_mock(glow.user.GetWindowDisplayAffinity, "verify")
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "0"}), \
                patch("desktop_glow.ctypes.get_last_error", return_value=0, create=True):
            glow.show((0, 0, 320, 240), False, True)
        self.assertEqual([c[0] for c in order.mock_calls], ["paint", "show", "verify", "verify"])
        glow.user.SetWindowDisplayAffinity.assert_called_once_with(123, 0x11)
        self.assertTrue(glow.capture_excluded)

    def test_valid_affinity_is_not_reapplied(self):
        glow = self.glow([0x11])
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "0"}):
            glow._ensure_capture_excluded()
        glow.user.SetWindowDisplayAffinity.assert_not_called()
        self.assertTrue(glow.capture_excluded)

    def test_failed_or_wrong_affinity_warns_once_and_retries(self):
        glow = self.glow([None, 0, 0, 0, 0, 0x11])
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "0"}), patch("desktop_glow.logging.warning") as warning, \
                patch("desktop_glow.ctypes.get_last_error", return_value=8, create=True):
            glow._ensure_capture_excluded()
            self.assertFalse(glow.capture_excluded)
            glow._ensure_capture_excluded()
            warning.assert_called_once()
            glow._ensure_capture_excluded()
        self.assertTrue(glow.capture_excluded)
        self.assertEqual(glow.user.SetWindowDisplayAffinity.call_count, 3)

    def test_demo_opt_out_never_reads_or_sets_affinity(self):
        glow = self.glow([])
        with patch.dict("os.environ", {"PLINY_CU_HUD_CAPTURE": "1"}):
            glow._ensure_capture_excluded()
        glow.user.GetWindowDisplayAffinity.assert_not_called()
        glow.user.SetWindowDisplayAffinity.assert_not_called()
        self.assertFalse(glow.capture_excluded)
