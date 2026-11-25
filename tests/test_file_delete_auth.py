"""Endpoint-level authorization tests for DELETE /api/file.

These drive a real HTTP server on a loopback port instead of calling the
handler method directly, because the bug they pin lives in the dispatch: the
route reached _api_file_delete before any operator check, so anything that
could speak HTTP to localhost could unlink files in the workshop.

No provider keys, no agent launches, no outbound network.
"""

import http.server
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402

WORKSHOP = server.WORKSHOP_DIR.resolve()


def _quiet_log(*args, **kwargs):
    """The handler logs every request to stderr; tests do not need that."""


class FileDeleteAuthzTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            # Keep the paired-device store out of the operator's real state.
            mock.patch.object(server, "PAIRED_DEVICES_FILE",
                              pathlib.Path(self._tmp.name) / "paired_devices.json"),
            mock.patch.object(server, "AGENT_TOKEN", "agent-fixture-token"),
            mock.patch.object(server.Handler, "log_message", _quiet_log),
        ]
        for patch in self._patches:
            patch.start()
        self.victim = WORKSHOP / "__authz_delete_test__.tmp"
        self.victim.write_text("temporary", encoding="utf-8")

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()
        if self.victim.exists():
            self.victim.unlink()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _delete(self, path, headers=None):
        query = urllib.parse.urlencode({"path": str(path)})
        url = f"http://127.0.0.1:{self.port}/api/file?{query}"
        request = urllib.request.Request(url, method="DELETE", headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, self._json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, self._json(error.read())

    @staticmethod
    def _json(raw):
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return {}

    def _operator_headers(self):
        return {"Authorization": f"Bearer {server._operator_secret()}"}

    def _agent_headers(self):
        return {"Authorization": f"Bearer {server.AGENT_TOKEN}"}

    def _paired_cookie_headers(self):
        token, _device_id = server._register_device("authz-test")
        return {"Cookie": f"{server.PAIR_COOKIE}={token}"}

    # ── the boundary ─────────────────────────────────────────────────────────

    def test_anonymous_delete_is_rejected(self):
        code, body = self._delete(self.victim)
        self.assertEqual(code, 403, body)
        self.assertTrue(self.victim.exists(), "anonymous request deleted a workshop file")

    def test_agent_token_delete_is_rejected(self):
        code, body = self._delete(self.victim, self._agent_headers())
        self.assertEqual(code, 403, body)
        self.assertTrue(self.victim.exists(), "scoped agent token deleted a workshop file")

    def test_operator_token_can_delete(self):
        code, body = self._delete(self.victim, self._operator_headers())
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("ok"), body)
        self.assertFalse(self.victim.exists())

    def test_paired_cookie_can_delete(self):
        code, body = self._delete(self.victim, self._paired_cookie_headers())
        self.assertEqual(code, 200, body)
        self.assertFalse(self.victim.exists())

    def test_operator_cannot_escape_the_workshop(self):
        outside = pathlib.Path(self._tmp.name) / "outside.txt"
        outside.write_text("keep me", encoding="utf-8")
        code, body = self._delete(outside, self._operator_headers())
        self.assertEqual(code, 403, body)
        self.assertTrue(outside.exists())

    def test_operator_cannot_delete_a_directory(self):
        victim_dir = WORKSHOP / "__authz_delete_dir__"
        victim_dir.mkdir(exist_ok=True)
        try:
            code, body = self._delete(victim_dir, self._operator_headers())
            self.assertEqual(code, 400, body)
            self.assertTrue(victim_dir.exists())
        finally:
            if victim_dir.exists():
                victim_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
