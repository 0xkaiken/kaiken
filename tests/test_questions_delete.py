import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class QuestionDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "20261104T100000_first.md").write_text("---\nid: first\n---\n", encoding="utf-8")
        (self.dir / "20261104T110000_second.md").write_text("---\nid: second\n---\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def delete(self, qid=None, params=None, authorized=True):
        handler = Mock()
        # Mock swallows attribute lookups; bind the real validator so the
        # traversal check runs against production logic.
        handler._safe_qid = server.Handler._safe_qid
        with patch.object(server, "QUESTIONS_DIR", self.dir), \
                patch.object(server, "_require_auth", return_value=authorized), \
                patch.object(server, "broadcast_sse") as broadcast:
            server.Handler._api_questions_delete(handler, qid, params or {})
        return handler, broadcast

    def test_delete_one(self):
        handler, _ = self.delete("20261104T100000_first")
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True, "deleted": 1})
        self.assertEqual(sorted(p.name for p in self.dir.glob("*.md")),
                         ["20261104T110000_second.md"])

    def test_delete_all(self):
        handler, _ = self.delete(None, {"all": ["1"]})
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True, "deleted": 2})
        self.assertEqual(list(self.dir.glob("*.md")), [])

    def test_rejects_traversal_and_missing(self):
        handler, _ = self.delete("../secret")
        self.assertEqual(handler._send_json.call_args.args[1], 400)
        handler, _ = self.delete("20240101T000000_missing")
        self.assertEqual(handler._send_json.call_args.args[1], 404)

    def test_requires_auth(self):
        handler, broadcast = self.delete(None, {"all": ["1"]}, authorized=False)
        handler._send_json.assert_not_called()
        broadcast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
