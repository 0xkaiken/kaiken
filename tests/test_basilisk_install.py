import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class FakeHandler:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}
        self.sent = None

    def _read_body(self):
        return self.body

    def _send_json(self, data, status=200):
        self.sent = (status, data)


class BasiliskInstallTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "schedules.json"
        patcher = mock.patch.object(server, "SCHEDULES_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PLINY_AUTH_TOKEN", None)

    def install(self, **payload):
        # Privileged route: the operator proves itself. This is what
        # basilisk.py sends via agent_secrets.operator_headers().
        handler = FakeHandler(
            json.dumps(payload).encode(),
            {"Authorization": f"Bearer {server._operator_secret()}"},
        )
        server.Handler._api_basilisk_install(handler)
        return handler.sent

    def test_first_install_registers_a_schedule_the_scheduler_resolves(self):
        status, schedule = self.install(interval="1h", duration="5m")
        self.assertEqual(status, 200)
        self.assertTrue(schedule["id"].startswith("basilisk_"))
        self.assertTrue(schedule["basilisk"] and schedule["enabled"])
        self.assertEqual(schedule["type"], "interval")
        self.assertEqual(schedule["prompt"], "__BASILISK_CYCLE__")
        self.assertEqual((schedule["interval_seconds"], schedule["duration_seconds"]), (3600, 300))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [schedule])

    def test_second_install_updates_instead_of_duplicating(self):
        _, first = self.install(interval="1h", duration="5m")
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        saved[0]["enabled"] = False
        self.path.write_text(json.dumps(saved), encoding="utf-8")
        _, second = self.install(interval="4h", duration="10m")
        self.assertEqual(second["id"], first["id"])
        self.assertTrue(second["enabled"])
        self.assertEqual(second["interval_display"], "4h")
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 1)

    def test_invalid_interval_is_rejected_without_writing(self):
        status, _ = self.install(interval="soon", duration="5m")
        self.assertEqual(status, 400)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
