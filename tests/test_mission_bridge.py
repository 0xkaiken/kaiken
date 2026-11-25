import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class MissionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "missions.json"
        self.path.write_text("[]", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, payload):
        handler = Mock()
        handler._read_body.return_value = json.dumps(payload)
        with patch.object(server, "MISSIONS_FILE", self.path), \
                patch.object(server, "broadcast_sse"):
            server.Handler._api_missions_save(handler)
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_mission_stores_preset_and_seed(self):
        rows = self.save({"prompt": "build X", "title": "Ship X",
                          "preset": "ship", "seed": "Use technique Y"})
        self.assertEqual(rows[0]["preset"], "SHIP")
        self.assertEqual(rows[0]["seed"], "Use technique Y")

    def test_editing_keeps_preset_and_seed_when_not_sent(self):
        self.save({"prompt": "build X", "title": "Ship X",
                   "preset": "SHIP", "seed": "seed"})
        mission_id = json.loads(self.path.read_text(encoding="utf-8"))[0]["id"]
        rows = self.save({"id": mission_id, "prompt": "build X v2", "title": "Ship X"})
        self.assertEqual(rows[0]["preset"], "SHIP")
        self.assertEqual(rows[0]["seed"], "seed")

    def test_launch_next_carries_the_preset(self):
        self.save({"prompt": "build X", "title": "Ship X", "preset": "SHIP"})
        handler = Mock()
        with patch.object(server, "MISSIONS_FILE", self.path), \
                patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "broadcast_sse"), \
                patch.object(server, "launch_session",
                             return_value={"id": "s1", "status": "running"}) as launch:
            server.Handler._api_missions_launch_next(handler)
        self.assertEqual(launch.call_args.kwargs["preset"], "SHIP")
        self.assertEqual(launch.call_args.kwargs["mission_id"], handler._send_json.call_args.args[0]["mission"]["id"])


if __name__ == "__main__":
    unittest.main()
