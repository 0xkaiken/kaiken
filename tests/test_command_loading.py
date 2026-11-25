import json
from http.server import HTTPServer
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import server


@unittest.skipUnless(os.name == "nt", "Windows port ownership")
class ServerBindingTests(unittest.TestCase):
    def test_second_server_cannot_share_a_running_servers_port(self):
        with patch.object(server.ThreadedHTTPServer, "address_family", socket.AF_INET):
            first = server.ThreadedHTTPServer(("127.0.0.1", 0), server.Handler)
            try:
                with self.assertRaises(OSError):
                    second = server.ThreadedHTTPServer(first.server_address, server.Handler)
                    second.server_close()
            finally:
                first.server_close()


class SessionOutputTests(unittest.TestCase):
    def test_output_alias_returns_history_and_preserves_desktop_access_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fixture.log").write_text(
                "Session: fixture\nPrompt: Merhaba Dünya ğüşiöç\nStarted: today\nStatus: completed\n"
                "============================================================\nSaved successfully\n",
                encoding="utf-8",
            )
            with patch.object(server, "SESSIONS_DIR", root), \
                    patch.object(server, "active_sessions", {}), \
                    patch.object(server, "load_json", return_value=[]), \
                    patch.object(server, "desktop_request_allowed", return_value=False):
                httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    results = []
                    for endpoint in ("log", "output"):
                        with urllib.request.urlopen(base + f"/api/session/{endpoint}?id=fixture", timeout=5) as r:
                            results.append(json.load(r))
                    self.assertEqual(results[0], results[1])
                    self.assertEqual(results[1]["prompt"], "Merhaba Dünya ğüşiöç")
                    self.assertEqual(results[1]["lines"][-1], "Saved successfully")
                    with patch.object(server, "load_json", return_value=[{"id": "fixture", "cu_mode": "desktop"}]):
                        for endpoint in ("log", "output"):
                            with self.subTest(endpoint=endpoint), self.assertRaises(urllib.error.HTTPError) as error:
                                urllib.request.urlopen(base + f"/api/session/{endpoint}?id=fixture", timeout=5)
                            self.assertEqual(error.exception.code, 403)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
