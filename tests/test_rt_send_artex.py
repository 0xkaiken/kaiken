import io
import json
import unittest
from unittest.mock import patch

import rt_send


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class RequestIdPropagationTests(unittest.TestCase):
    def test_prompt_and_response_carry_the_same_request_id(self):
        bodies = []

        def urlopen(request, timeout):
            body = json.loads(request.data)
            bodies.append(body)
            return _Response(json.dumps({
                "ok": True, "request_permitted": True,
                "campaign_id": body.get("campaign_id"),
                "request_id": body.get("request_id"),
            }).encode())

        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_REQUEST_ID", "req-fixture"), \
                patch.object(rt_send.urllib.request, "urlopen",
                             side_effect=urlopen):
            rt_send.post_to_dashboard({"type": "prompt", "text": "probe",
                                       "attempt": 1})
            rt_send.post_to_dashboard({"type": "response", "text": "answer",
                                       "attempt": 1})
        self.assertEqual("req-fixture", bodies[0]["request_id"])
        self.assertEqual("req-fixture", bodies[1]["request_id"])
        self.assertEqual("artex-fixture", bodies[0]["campaign_id"])
        self.assertEqual("artex-fixture", bodies[1]["campaign_id"])

    def test_free_form_prompt_still_mints_a_fresh_request_id(self):
        bodies = []

        def urlopen(request, timeout):
            bodies.append(json.loads(request.data))
            return _Response(json.dumps({"ok": True,
                                         "request_permitted": True}).encode())

        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", ""), \
                patch.object(rt_send.urllib.request, "urlopen",
                             side_effect=urlopen):
            rt_send.post_to_dashboard({"type": "prompt", "text": "one",
                                       "attempt": 1})
            rt_send.post_to_dashboard({"type": "prompt", "text": "two",
                                       "attempt": 1})
        self.assertNotEqual(bodies[0]["request_id"], bodies[1]["request_id"])
        self.assertNotIn("campaign_id", bodies[0])


class StructuredAuthorizationTests(unittest.TestCase):
    def test_structured_post_without_session_fails_before_network(self):
        with patch.object(rt_send, "_PLINY_SESSION_ID", ""), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(SystemExit):
                rt_send.post_to_dashboard({"type": "prompt", "text": "probe"})
        self.assertFalse(urlopen.called)

    def test_permission_reply_must_name_the_campaign_and_request(self):
        def urlopen(_request, timeout):
            return _Response(json.dumps({
                "ok": True, "request_permitted": True,
                "campaign_id": "other-campaign", "request_id": "other-request",
            }).encode())

        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_REQUEST_ID", "req-fixture"), \
                patch.object(rt_send.urllib.request, "urlopen",
                             side_effect=urlopen):
            with self.assertRaises(SystemExit):
                rt_send.post_to_dashboard({"type": "prompt", "text": "probe"})

    def test_main_requires_locked_target_environment(self):
        args = type("Args", (), {"attempt": 1, "provider": "opencode",
                                 "model_id": "fixture", "prompt": "probe",
                                 "timeout": None})()
        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_PLINY_ARTEX_PROVIDER", ""), \
                patch.object(rt_send, "_PLINY_ARTEX_MODEL", ""), \
                patch.object(rt_send, "parse_prompt_request", return_value=args), \
                patch.object(rt_send, "send_to_target") as send:
            with self.assertRaises(SystemExit):
                rt_send.main([])
        self.assertFalse(send.called)

    def test_main_rejects_a_cli_target_mismatch(self):
        args = type("Args", (), {"attempt": 1, "provider": "opencode",
                                 "model_id": "other-model", "prompt": "probe",
                                 "timeout": None})()
        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_PLINY_ARTEX_PROVIDER", "opencode"), \
                patch.object(rt_send, "_PLINY_ARTEX_MODEL", "fixture"), \
                patch.object(rt_send, "parse_prompt_request", return_value=args), \
                patch.object(rt_send, "send_to_target") as send:
            with self.assertRaises(SystemExit):
                rt_send.main([])
        self.assertFalse(send.called)

    def test_denied_permission_prevents_target_dispatch(self):
        args = type("Args", (), {"attempt": 1, "provider": "opencode",
                                 "model_id": "fixture", "prompt": "probe",
                                 "timeout": None})()

        def denied(_request, timeout):
            raise rt_send.urllib.error.HTTPError(
                "http://localhost", 409, "denied", {}, io.BytesIO(
                    b'{"error":"evaluation_pending"}'))

        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_PLINY_ARTEX_PROVIDER", "opencode"), \
                patch.object(rt_send, "_PLINY_ARTEX_MODEL", "fixture"), \
                patch.object(rt_send, "parse_prompt_request", return_value=args), \
                patch.object(rt_send, "send_to_target") as send, \
                patch.object(rt_send.urllib.request, "urlopen",
                             side_effect=denied):
            with self.assertRaises(SystemExit):
                rt_send.main([])
        self.assertFalse(send.called)


class MainRequestIdTests(unittest.TestCase):
    def test_each_probe_run_gets_a_fresh_request_id(self):
        bodies = []

        def urlopen(request, timeout):
            body = json.loads(request.data)
            bodies.append(body)
            return _Response(json.dumps({
                "ok": True, "request_permitted": True,
                "campaign_id": body.get("campaign_id"),
                "request_id": body.get("request_id"),
            }).encode())

        args = type("Args", (), {"attempt": 1, "provider": "opencode",
                                 "model_id": "fixture", "prompt": "probe",
                                 "timeout": None})()
        with patch.object(rt_send, "_PLINY_SESSION_ID", "s1"), \
                patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-fixture"), \
                patch.object(rt_send, "_PLINY_ARTEX_PROVIDER", "opencode"), \
                patch.object(rt_send, "_PLINY_ARTEX_MODEL", "fixture"), \
                patch.object(rt_send, "parse_prompt_request", return_value=args), \
                patch.object(rt_send, "send_to_target", return_value="answer"), \
                patch.object(rt_send, "fetch_receipt", return_value={}), \
                patch.object(rt_send.urllib.request, "urlopen",
                             side_effect=urlopen), \
                patch("builtins.print"):
            rt_send.main([])
            rt_send.main([])
        prompts = [body for body in bodies if body["type"] == "prompt"]
        self.assertEqual(2, len(prompts))
        self.assertNotEqual(prompts[0]["request_id"],
                            prompts[1]["request_id"])


class ReceiptCommandTests(unittest.TestCase):
    def test_receipt_defaults_to_the_launch_campaign(self):
        with patch.object(rt_send, "_PLINY_CAMPAIGN_ID", "artex-env"):
            args = rt_send.parse_receipt_request(["--receipt", "req-1"])
        self.assertEqual("artex-env", args.campaign)
        self.assertEqual("req-1", args.receipt)

    def test_main_receipt_mode_prints_json_only(self):
        receipt = {"request_id": "req-1", "next_action": "wait",
                   "waiting": True, "campaign_state": "evaluating"}
        out = io.StringIO()
        with patch.object(rt_send, "fetch_receipt", return_value=receipt), \
                patch("sys.stdout", out):
            rt_send.main(["--receipt", "req-1", "--campaign", "artex-1",
                          "--wait", "300"])
        payload = json.loads(out.getvalue())
        self.assertEqual(receipt, payload)

    def test_fetch_receipt_returns_when_not_waiting(self):
        replies = [
            {"ok": True, "receipt": {"waiting": True, "next_action": "wait"}},
            {"ok": True, "receipt": {"waiting": False,
                                     "next_action": "stop_confirmed"}},
        ]

        def urlopen(_url, timeout):
            return _Response(json.dumps(replies.pop(0)).encode())

        with patch.object(rt_send.urllib.request, "urlopen",
                          side_effect=urlopen), \
                patch("time.sleep"):
            receipt = rt_send.fetch_receipt("artex-1", "req-1", wait=30)
        self.assertEqual("stop_confirmed", receipt["next_action"])


if __name__ == "__main__":
    unittest.main()
