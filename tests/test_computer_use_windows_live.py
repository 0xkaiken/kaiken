"""Real Windows checks for the desktop adapter. Reads the screen; sends NO input.

Runs only on Windows with an unlocked interactive desktop.
"""
import io
import os
import unittest

from PIL import Image

import windows_desktop
from windows_desktop import DesktopUnavailable, WindowsDesktop


@unittest.skipUnless(os.name == "nt", "real Windows desktop required")
class LiveWindowsDesktopTests(unittest.TestCase):
    def setUp(self):
        self.d = WindowsDesktop()
        try:
            self.d.enable("live-test-a", None)
        except DesktopUnavailable as error:
            self.skipTest(f"desktop not interactive: {error}")
        self.addCleanup(self.d.release, None)

    def test_monitors_capture_and_mapping_on_real_hardware(self):
        monitors = self.d.monitors()
        self.assertTrue(monitors)
        self.assertEqual(sum(1 for m in monitors if m["primary"]), 1)
        png = self.d.screenshot(remember=True)
        image = Image.open(io.BytesIO(png))
        frame = self.d.frame
        self.assertLessEqual(image.width, windows_desktop.FRAME_SIZE[0])
        self.assertEqual((image.width, image.height), (frame["width"], frame["height"]))
        selected = next(m for m in monitors if m["id"] == self.d.monitor_id)
        self.assertEqual((frame["native_width"], frame["native_height"]), (selected["width"], selected["height"]))
        self.assertEqual(self.d.point(0, 0), (selected["left"], selected["top"]))
        self.assertEqual(self.d._frame_for({"frame_id": frame["frame_id"]})["frame_id"], frame["frame_id"])

    def test_windows_and_foreground_are_real(self):
        listed = self.d.windows()
        self.assertTrue(listed)
        self.assertFalse(any(w["title"].startswith(windows_desktop.HUD_TITLE_PREFIX) for w in listed))
        foreground = self.d.foreground()
        self.assertIsInstance(foreground["id"], int)
        if foreground["id"]:
            self.assertTrue(self.d._belongs(foreground["id"], foreground["id"]))
            self.assertFalse(self.d._belongs(foreground["id"], foreground["id"] + 1))

    def test_session_capture_files_are_private_and_removed(self):
        self.d.capture()
        path_a = self.d.snapshot_path
        self.assertTrue(path_a.is_file())
        self.assertIn("live-test-a", str(path_a))
        self.d.release("live-test-a")
        self.assertFalse(path_a.parent.exists())
        self.d.enable("live-test-b", None)
        self.assertIsNone(self.d.frame)
        self.d.capture()
        self.assertNotEqual(self.d.snapshot_path.parent, path_a.parent)
        self.d.release("live-test-b")

    def test_paused_desktop_refuses_input_without_sending_it(self):
        self.d.capture()
        self.d.pause(True)
        result = self.d.batch([{"action": "move", "x": 1, "y": 1}], "live-test-a", settle_ms=0)
        self.assertEqual(result["results"][0]["code"], "desktop_paused")


if __name__ == "__main__":
    unittest.main()
