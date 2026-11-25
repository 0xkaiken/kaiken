import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

import rt_send


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body
        self.closed = False

    def read(self):
        return self.body

    def close(self):
        self.closed = True


class WireAndHealthTests(unittest.TestCase):
    def test_wire_urlopen_captures_provider_bytes(self):
        captured = FakeResponse(b'{"choices":[{"message":{"content":"hi"}}]}')
        req = rt_send.urllib.request.Request(
            "https://api.example.com/v1/chat/completions",
            data=b'{"model":"x"}',
            headers={"Authorization": "Bearer not-a-real-key"},
        )
        with patch.object(rt_send.urllib.request, "urlopen", return_value=captured) as opener:
            err = io.StringIO()
            with redirect_stderr(err):
                response = rt_send._wire_urlopen(req, timeout=90)
            opener.assert_called_once_with(req, timeout=90)
        self.assertEqual(captured.body, response.read())
        lines = [line for line in err.getvalue().splitlines() if line.startswith("[WIRE]")]
        self.assertEqual(2, len(lines))
        self.assertIn("side=request", lines[0])
        self.assertIn("side=response", lines[1])
        self.assertIn("status=200", lines[1])

    def test_wire_urlopen_ignores_localhost(self):
        captured = FakeResponse(b"dashboard")
        req = rt_send.urllib.request.Request(
            "http://localhost:8888/api/redteam/chat", data=b"{}")
        with patch.object(rt_send.urllib.request, "urlopen", return_value=captured):
            err = io.StringIO()
            with redirect_stderr(err):
                rt_send._wire_urlopen(req, timeout=3)
        self.assertNotIn("[WIRE]", err.getvalue())

    def test_open_breaker_skips_dispatch(self):
        with patch.object(rt_send.provider_health, "check", return_value="cooldown 42s"), \
                patch.object(rt_send, "_dispatch_target") as dispatch:
            result = rt_send.send_to_target("openai", "gpt-6-astra", "hi")
        dispatch.assert_not_called()
        self.assertIn("[RATELIMIT", result)
        self.assertIn("cooldown 42s", result)

    def test_outcome_is_recorded_to_shared_registry(self):
        with patch.object(rt_send.provider_health, "check", return_value=None), \
                patch.object(rt_send.provider_health, "note_result") as note, \
                patch.object(rt_send, "_dispatch_target", return_value="model answer"):
            result = rt_send.send_to_target("xai", "grok-3", "hi")
        self.assertEqual("model answer", result)
        note.assert_called_once_with("xai", "model answer")


if __name__ == "__main__":
    unittest.main()
