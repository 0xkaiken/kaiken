"""Computer Use zoom: native-resolution crops for dense UIs.

A 1280px frame of a 1920x1080 desktop shrinks a CAD ribbon to 8px labels, and
an agent that cannot read a control guesses at it, misses, and restarts the
document. These tests pin the crop geometry, the frame-size knob and the two
places the request crosses a process boundary.
"""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import desktop_worker
import server
import windows_desktop
from windows_desktop import DesktopUnavailable, WindowsDesktop


def frame_of(width, height, native_width, native_height):
    return {"frame_id": "f1-test", "width": width, "height": height,
            "native_width": native_width, "native_height": native_height,
            "left": 0, "top": 0, "monitor_id": "m1", "captured_at": 0.0}


def decode(value):
    return Image.open(io.BytesIO(base64.b64decode(value)))


class FrameSizeTests(unittest.TestCase):
    def test_default_is_the_documented_frame(self):
        self.assertEqual(windows_desktop._frame_size(None), (1280, 800))
        self.assertEqual(windows_desktop._frame_size(""), (1280, 800))

    def test_operator_can_raise_density_for_cad(self):
        self.assertEqual(windows_desktop._frame_size("1600x1000"), (1600, 1000))
        self.assertEqual(windows_desktop._frame_size(" 1920 * 1200 "), (1920, 1200))

    def test_a_bad_override_falls_back_instead_of_breaking_capture(self):
        for raw in ("big", "0x0", "100x100", "5000x5000", "1280", "-1280x800"):
            self.assertEqual(windows_desktop._frame_size(raw), (1280, 800), raw)


class CropTests(unittest.TestCase):
    def setUp(self):
        self.control = WindowsDesktop()
        self.control.frame = frame_of(1280, 720, 1920, 1080)
        self.control.last_native = Image.new("RGB", (1920, 1080), (10, 20, 30))

    def test_crop_reads_native_pixels_for_the_requested_frame_region(self):
        # 640..960 in a 1280-wide frame is 960..1440 on a 1920-wide screen.
        patch_image = decode(self.control.crop({"x": 640, "y": 360, "width": 320, "height": 180},
                                               scale=2.0))
        self.assertEqual(patch_image.size, (960, 540))

    def test_a_zoom_never_costs_more_than_one_frame(self):
        whole = self.control.crop({"x": 0, "y": 0, "width": 1280, "height": 720}, scale=8.0)
        decoded = decode(whole)
        self.assertLessEqual(decoded.width * decoded.height, windows_desktop.MAX_CROP_PIXELS)
        self.assertLessEqual(max(decoded.size), windows_desktop.MAX_CROP_SIDE)
        self.assertLess(decoded.width * decoded.height, 1280 * 720)

    def test_region_is_clamped_to_the_monitor(self):
        # x 1200..1280 of a 1280-wide frame is only 120 native pixels wide.
        patch_image = decode(self.control.crop({"x": 1200, "y": 680, "width": 400, "height": 400},
                                               scale=1.0))
        self.assertEqual(patch_image.size, (120, 60))

    def test_w_and_h_are_accepted_as_shorthands(self):
        patch_image = decode(self.control.crop({"x": 100, "y": 100, "w": 300, "h": 150}, scale=1.0))
        self.assertEqual(patch_image.size, (450, 225))

    def test_a_click_sized_box_is_rejected_rather_than_returning_noise(self):
        with self.assertRaises(ValueError):
            self.control.crop({"x": 10, "y": 10, "width": 4, "height": 4})

    def test_non_numeric_and_non_object_boxes_are_rejected(self):
        for box in (None, [], {"x": "left", "y": 0, "width": 100, "height": 100},
                    {"x": float("nan"), "y": 0, "width": 100, "height": 100}):
            with self.assertRaises((TypeError, ValueError)):
                self.control.crop(box)

    def test_zoom_without_a_capture_is_a_desktop_error(self):
        self.control.last_native = None
        with self.assertRaises(DesktopUnavailable):
            self.control.crop({"x": 0, "y": 0, "width": 100, "height": 100})


class WorkerPayloadTests(unittest.TestCase):
    def make_api(self):
        api = desktop_worker.DesktopAPI("mission-a", 8888)
        api.last_frame_id = "f1-test"
        return api

    def posted(self, api, **options):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("desktop_worker.urllib.request.urlopen", return_value=response) as call:
            api.batch([], **options)
        return json.loads(call.call_args.args[0].data)

    def test_shot_has_no_crop_unless_asked(self):
        payload = self.posted(self.make_api(), screenshot=True)
        self.assertNotIn("crop", payload)

    def test_shot_forwards_the_crop_box(self):
        payload = self.posted(self.make_api(), screenshot=True,
                              crop={"x": 1, "y": 2, "width": 3, "height": 4, "scale": 2})
        self.assertEqual(payload["crop"], {"x": 1, "y": 2, "width": 3, "height": 4, "scale": 2})

    def test_zoom_helper_sends_a_crop_and_keeps_the_frame(self):
        api = self.make_api()
        helpers = desktop_worker.build_helpers(api, [], "mission-a")
        sent = {}
        with patch.object(api, "batch", side_effect=lambda actions, **options: sent.update(options) or {"ok": True, "image": "abc"}):
            helpers["zoom"](100, 200, 300, 80, scale=2)
        self.assertEqual(sent["crop"], {"x": 100, "y": 200, "width": 300, "height": 80, "scale": 2})
        self.assertTrue(sent["screenshot"])


class BatchResponseTests(unittest.TestCase):
    def setUp(self):
        self.control = Mock()
        self.control.owner = "mission-a"
        self.control.frame = frame_of(1280, 720, 1920, 1080)
        self.control.snapshot_path = Path(tempfile.gettempdir()) / "cu-zoom-test.png"
        self.control.jpeg.return_value = "full-frame"
        self.control.crop.return_value = "zoomed"
        self.enterContext(patch.object(server, "desktop", self.control))
        self.enterContext(patch.object(server, "broadcast_sse"))
        self.session = Mock(session_id="mission-a")

    def test_no_crop_keeps_the_full_frame(self):
        outcome = server.desktop_batch_response(self.session, {"inline_image": True})
        self.assertEqual(outcome["image"], "full-frame")
        self.control.crop.assert_not_called()

    def test_crop_replaces_the_attached_pixels_but_not_the_frame(self):
        outcome = server.desktop_batch_response(
            self.session, {"inline_image": True, "crop": {"x": 10, "y": 20, "width": 30, "height": 40, "scale": 2}})
        self.assertEqual(outcome["image"], "zoomed")
        self.assertEqual(outcome["crop"], {"x": 10, "y": 20, "width": 30, "height": 40, "scale": 2})
        # Coordinates must stay valid after a zoom, so the frame is untouched.
        self.assertEqual(outcome["frame"]["width"], 1280)

    def test_scale_defaults_when_the_caller_omits_it(self):
        server.desktop_batch_response(
            self.session, {"inline_image": True, "crop": {"x": 10, "y": 20, "width": 30, "height": 40}})
        self.assertEqual(self.control.crop.call_args.kwargs["scale"], 2.0)


if __name__ == "__main__":
    unittest.main()
