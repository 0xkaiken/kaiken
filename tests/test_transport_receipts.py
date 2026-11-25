"""Transport receipts: a ledger response is tied to the rt_send call that
produced it, and model_routed is filled from the provider's own answer."""
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import gauntlet
import rt_send
import transport_receipts


class DigestTests(unittest.TestCase):
    def test_round_trip_through_stdout_and_file_keeps_the_digest(self):
        raw = "line one\nline two"
        self.assertEqual(transport_receipts.response_digest(raw),
                         transport_receipts.response_digest("line one\r\nline two\r\n"))

    def test_one_edited_character_breaks_the_match(self):
        self.assertNotEqual(transport_receipts.response_digest("I can't help"),
                            transport_receipts.response_digest("I can help"))


class ReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        transport_receipts.clear()
        self.addCleanup(transport_receipts.clear)

    def test_stamp_hit_and_miss(self):
        transport_receipts.note("answer", target="openrouter/glm-5.3",
                                model_routed="z-ai/glm-5.3", request_id="r1")
        hit = transport_receipts.stamp("answer\n")
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["model_routed"], "z-ai/glm-5.3")
        self.assertEqual(hit["target"], "openrouter/glm-5.3")
        self.assertFalse(transport_receipts.stamp("answer, edited")["matched"])

    def test_claimed_digest_is_not_the_key(self):
        r = transport_receipts.note("real text", claimed_digest="0" * 64)
        self.assertFalse(r["digest_agrees"])
        self.assertTrue(transport_receipts.stamp("real text")["matched"])

    def test_store_is_bounded(self):
        with patch.object(transport_receipts, "MAX_RECEIPTS", 3):
            for i in range(5):
                transport_receipts.note(f"r{i}")
            self.assertFalse(transport_receipts.stamp("r0")["matched"])
            self.assertTrue(transport_receipts.stamp("r4")["matched"])


class MetaSidechannelTests(unittest.TestCase):
    def test_rt_send_meta_carries_model_routed_into_attempt_kwargs(self):
        rt_send._ROUTED["model"] = ""
        buf = io.StringIO()
        with redirect_stderr(buf):
            rt_send._emit_meta("stop", {"completion_tokens": 5}, 2000,
                               routed="z-ai/glm-5.3 ")
        self.assertEqual(rt_send._ROUTED["model"], "z-ai/glm-5.3")
        kwargs = gauntlet._meta_to_attempt_kwargs(gauntlet._parse_meta(buf.getvalue()))
        self.assertEqual(kwargs["model_routed"], "z-ai/glm-5.3")
        self.assertEqual(kwargs["finish_reason"], "stop")

    def test_hidden_route_stays_empty(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            rt_send._emit_meta("stop", None, 0)
        kwargs = gauntlet._meta_to_attempt_kwargs(gauntlet._parse_meta(buf.getvalue()))
        self.assertNotIn("model_routed", kwargs)


class RecordAttemptTests(unittest.TestCase):
    """The entry record_attempt builds carries the receipt. The attacker-
    refusal branch is forced so the test stops before judge/network code."""

    def _record(self, response, **attempt):
        run_id = "receipt-test-run"
        gauntlet._gauntlet_runs[run_id] = {
            "id": run_id, "status": "running",
            "targets": [{"id": "t", "status": "running", "attempts": []}],
        }
        self.addCleanup(gauntlet._gauntlet_runs.pop, run_id, None)
        with patch.object(gauntlet, "is_run_sealed", return_value=False), \
             patch.object(gauntlet, "_is_attacker_refusal", return_value=True), \
             patch.object(gauntlet, "_save_run"), \
             patch("server.broadcast_sse", create=True):
            gauntlet.record_attempt(run_id, "t", dict(attempt, prompt="p",
                                                      response=response))
        return gauntlet._gauntlet_runs[run_id]["targets"][0]["attempts"][0]

    def setUp(self):
        transport_receipts.clear()
        self.addCleanup(transport_receipts.clear)

    def test_matched_response_gets_routed_model(self):
        transport_receipts.note("the verbatim answer", target="openrouter/glm-5.3",
                                model_routed="z-ai/glm-5.3")
        entry = self._record("the verbatim answer\r\n")
        self.assertTrue(entry["transport_receipt"]["matched"])
        self.assertEqual(entry["model_routed"], "z-ai/glm-5.3")

    def test_caller_supplied_route_wins(self):
        transport_receipts.note("x", model_routed="from-receipt")
        self.assertEqual(self._record("x", model_routed="from-caller")["model_routed"],
                         "from-caller")

    def test_unseen_response_is_marked_unmatched(self):
        entry = self._record("text no transport ever returned")
        self.assertFalse(entry["transport_receipt"]["matched"])
        self.assertEqual(entry["model_routed"], "")


if __name__ == "__main__":
    unittest.main()
