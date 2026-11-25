"""Tests for the persistent desktop_py worker protocol (no desktop contact)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import desktop_worker

WORKER = Path(__file__).resolve().parent.parent / "desktop_worker.py"


class DesktopWorkerTests(unittest.TestCase):
    def setUp(self):
        self.workshop = self.enterContext(tempfile.TemporaryDirectory())
        env = {**os.environ, "PLINY_WORKSHOP": self.workshop}
        self.process = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(WORKER), "--session", "test", "--port", "9"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env)
        self.enterContext(self.process)

    def call(self, code):
        self.process.stdin.write(json.dumps({"id": 1, "code": code}) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def test_namespace_persists_and_stdout_is_captured(self):
        first = self.call("x = 41\nprint('hello')")
        self.assertTrue(first["ok"])
        self.assertIn("hello", first["stdout"])
        second = self.call("print(x + 1)")
        self.assertIn("42", second["stdout"])

    def test_traceback_is_reported_without_killing_the_worker(self):
        failed = self.call("raise ValueError('boom')")
        self.assertFalse(failed["ok"])
        self.assertIn("ValueError: boom", failed["error"])
        after = self.call("print('still here')")
        self.assertTrue(after["ok"])
        self.assertIn("still here", after["stdout"])

    def test_display_text_and_help_are_available(self):
        result = self.call("display('note')\nprint('HELPERS' in globals())")
        self.assertIn("note", result["stdout"])
        self.assertIn("True", result["stdout"])

    def test_input_does_not_consume_the_protocol_stream(self):
        result = self.call("print(input() if False else 'safe')")
        self.assertTrue(result["ok"])
        self.assertIn("safe", result["stdout"])

    def test_notes_round_trip_inside_the_workshop(self):
        result = self.call("note('step one')\nprint(read_notes())")
        self.assertTrue(result["ok"])
        self.assertIn("step one", result["stdout"])
        path = Path(self.workshop) / "cu-notes-test.md"
        self.assertIn("step one", path.read_text(encoding="utf-8"))


class HelperLatencyTests(unittest.TestCase):
    def make(self):
        api = desktop_worker.DesktopAPI("s1", 8888)
        images = []
        return api, images, desktop_worker.build_helpers(api, images, "s1")

    @staticmethod
    def payload(urlopen):
        return json.loads(urlopen.call_args.args[0].data.decode("utf-8"))

    def test_intermediate_helpers_never_capture_or_settle(self):
        cases = (
            ("click", lambda h: h["click"](10, 20)),
            ("type_text", lambda h: h["type_text"]("hi", 123)),
            ("key", lambda h: h["key"]("Enter", 123)),
            ("activate", lambda h: h["activate"](123)),
            ("launch", lambda h: h["launch"]("C:/x/app.exe", [])),
            ("wait", lambda h: h["wait"](10)),
            ("focus_window", lambda h: h["focus_window"]("Nothing")),
            ("windows", lambda h: h["windows"]()),
            ("foreground", lambda h: h["foreground"]()),
        )
        for name, call in cases:
            with self.subTest(name=name), patch("desktop_worker.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
                _, _, helpers = self.make()
                call(helpers)
                body = self.payload(urlopen)
                self.assertFalse(body["screenshot"])
                self.assertEqual(body["settle_ms"], 0)

    def test_batch_sends_one_request_for_consecutive_actions(self):
        with patch("desktop_worker.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            _, _, helpers = self.make()
            helpers["batch"]([{"action": "click", "x": 1, "y": 2},
                              {"action": "press", "key": "Enter"}])
            self.assertEqual(urlopen.call_count, 1)
            body = self.payload(urlopen)
            self.assertEqual([step["action"] for step in body["actions"]], ["click", "press"])
            self.assertFalse(body["screenshot"])
            self.assertEqual(body["settle_ms"], 0)

    def test_shot_captures_the_frame_and_display_returns_it(self):
        with patch("desktop_worker.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = (
                b'{"ok": true, "image": "QQ==", "frame": {"frame_id": "f1"}}')
            api, images, helpers = self.make()
            value = helpers["shot"](windows=True)
            body = self.payload(urlopen)
            self.assertTrue(body["screenshot"])
            self.assertTrue(body["inline_image"])
            self.assertEqual(value["image"], "QQ==")
            self.assertEqual(api.last_frame_id, "f1")
            helpers["display"](value)
            self.assertEqual(images, ["QQ=="])

    def test_pointer_actions_reuse_the_last_seen_frame(self):
        with patch("desktop_worker.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            api, _, helpers = self.make()
            api.last_frame_id = "f7"
            helpers["click"](1, 2)
            self.assertEqual(self.payload(urlopen)["actions"][0]["frame_id"], "f7")
            helpers["act"]([{"action": "click", "x": 1, "y": 2, "frame_id": "explicit"}])
            self.assertEqual(self.payload(urlopen)["actions"][0]["frame_id"], "explicit")

    def test_must_propagates_errors(self):
        _, _, helpers = self.make()
        with self.assertRaises(RuntimeError):
            helpers["must"]({"ok": False, "error": "nope"})

    def test_session_capability_headers_are_preserved(self):
        with patch.dict(os.environ, {"PLINY_AGENT_TOKEN": "agent",
                                     "PLINY_CU_TOKEN": "cap"}, clear=False):
            api = desktop_worker.DesktopAPI("s1", 8888)
        self.assertEqual(api.headers["Authorization"], "Bearer agent")
        self.assertEqual(api.headers["X-Pliny-CU-Token"], "cap")


if __name__ == "__main__":
    unittest.main()
