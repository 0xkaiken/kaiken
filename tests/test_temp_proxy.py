"""Regression tests for the OpenCode Go temperature pin (go_temp_proxy).

Evidence base for why this exists:
- probes/temp_pin_probe.py + probes/maxtoken_probe.py + probes/variant_body_probe.py
  + probes/model_body_probe.py: config/placement params never reach the provider.
- probes/baseurl_oracle.py: provider options.baseURL IS honored.
- probes/proxy_divergence_probe.py: through the proxy, temperature belatedly
  controls sampling (0.0 converges, 2.0 diverges) and auth survives.
"""
import http.server
import json
import threading
import unittest

from opencode_runner import complete_text
from go_temp_proxy import _QuietServer, GoTempProxy, UPSTREAM


class StubUpstream(http.server.BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        StubUpstream.seen = {
            "path": self.path,
            "body": json.loads(self.rfile.read(length) or b"{}"),
            "auth": self.headers.get("Authorization"),
            "session": self.headers.get("x-opencode-session"),
        }
        payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ProxyBodyPinningTests(unittest.TestCase):
    """The proxy edits the body, forwards auth/routing headers, streams back."""

    def setUp(self):
        self.server = _QuietServer(("127.0.0.1", 0), StubUpstream)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._original = UPSTREAM

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        import go_temp_proxy
        go_temp_proxy.UPSTREAM = self._original

    def test_pin_reaches_upstream_and_auth_survives(self):
        import go_temp_proxy
        go_temp_proxy.UPSTREAM = "http://127.0.0.1:%d" % self.server.server_address[1]
        import urllib.request
        with GoTempProxy(pinned={"temperature": 0.0}) as proxy:
            body = json.dumps({"model": "m", "temperature": 1.0, "messages": []}).encode()
            req = urllib.request.Request(
                proxy.url + "/chat/completions", body,
                {"Content-Type": "application/json", "Authorization": "Bearer test-token",
                 "x-opencode-session": "ses_test"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=10)
            self.assertEqual(resp.read(), b'{"ok": true}')
            resp.close()
        seen = StubUpstream.seen
        self.assertEqual(seen["path"], "/chat/completions")
        self.assertEqual(seen["body"]["temperature"], 0.0)  # pinned over 1.0
        self.assertEqual(seen["body"]["messages"], [])     # body intact
        self.assertEqual(seen["auth"], "Bearer test-token")  # auth passed through
        self.assertEqual(seen["session"], "ses_test")        # routing header through


class CompleteTextPinGuardTests(unittest.TestCase):
    """Unknown provider + missing proxy module fail loudly, never silently."""

    def test_pin_rejects_non_go_provider(self):
        with self.assertRaises(ValueError):
            complete_text("hello", model="openai/gpt-6-luna", pin_body={"temperature": 0.0})

    def test_plain_call_builds_same_commits(self):
        # no pin -> env unchanged, no proxy imported; assert by config content key
        env = __import__("opencode_runner").config_env({"permission": "deny"},
                                                       filter_env=False)
        merged = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("options", merged.get("provider", {}).get("opencode-go", {}))


if __name__ == "__main__":
    unittest.main()
