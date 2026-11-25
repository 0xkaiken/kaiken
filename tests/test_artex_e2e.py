"""Mocked end-to-end ARTEX campaign.

No network: the target sender and the judge are injected.  Everything between
them is the real server path — reservation, request ids, intent progression,
judge lifecycle, validation lifecycle, receipts, persistence and restart.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import artex_campaign as ac
import server


class ArtexEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ac.configure(Path(self.tmp.name) / "campaigns")
        ac.reset()
        self.addCleanup(ac.configure, None)
        self.chats = Path(self.tmp.name) / "chats"
        self.chats.mkdir()
        self.active = {}
        for name, target in (("s1", ("opencode", "target-one")),
                             ("s2", ("openai", "target-two"))):
            self.active[name] = SimpleNamespace(
                status="running", _target_completion_resumes=False,
                target={"provider": target[0], "model": target[1]})
        self.patcher = patch.multiple(
            server,
            REDTEAM_CHATS_DIR=self.chats,
            broadcast_sse=Mock(),
            active_sessions=self.active,
            _artex_after_evaluation=Mock(),
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def campaign(self, provider, model, session, attempts=6):
        campaign = ac.create_campaign(
            "guardrail", "map where the line moves", provider, model,
            attempts, session_config={"runtime": "codex"})
        ac.attach_session(campaign["campaign_id"], session)
        return campaign["campaign_id"]

    def chat(self, **payload):
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = lambda: json.dumps(payload).encode()
        handler._send_json = Mock()
        handler._api_redteam_chat()
        return handler._send_json.call_args.args[0]

    def send(self, campaign_id, request_id, session, attempt, prompt):
        target = ac.locked_target(ac.get_campaign(campaign_id) or {})
        reply = self.chat(type="prompt", campaign_id=campaign_id,
                          request_id=request_id, session_id=session,
                          attempt=attempt, text=prompt, target=target)
        self.assertTrue(reply["request_permitted"], reply)
        with patch.object(server, "_artex_schedule_judge"):
            answer = self.chat(type="response", campaign_id=campaign_id,
                               request_id=request_id, session_id=session,
                               attempt=attempt, text=f"answer to {prompt}",
                               target=target)
        self.assertTrue(answer["ok"], answer)
        return answer

    def test_refusal_then_candidate_then_confirmed_across_restart(self):
        campaign_id = self.campaign("opencode", "target-one", "s1")
        state = dict(server._rt_state)

        # Intent 1: the target refuses.  The judge says so; the campaign
        # advances to intent 2 without treating the refusal as an error.
        self.send(campaign_id, "r1", "s1", 1, "probe one")
        with patch("blood_artex.judge_response", return_value={
                "verdict": "refused", "trigger": "refusal",
                "answers": {}, "error": None}), \
                patch("blood_artex.run_validation") as never:
            server._artex_run_judge(campaign_id, "r1")
        never.assert_not_called()
        self.assertEqual("running", ac.campaign_summary(campaign_id)["state"])

        # Intent 2: the target complies.  The judge only produces a candidate.
        self.send(campaign_id, "r2", "s1", 2, "probe two")
        success = {"verdict": "success", "trigger": "prohibited_content",
                   "evidence": "answer to probe two", "answers": {}, "error": None}

        def fake_validation(_objective_id, _artifact, _provider, _model,
                            **kwargs):
            # Exercise the same budget function the real validation uses.
            self.assertTrue(kwargs["budget_fn"]("baseline")["ok"])
            self.assertTrue(kwargs["budget_fn"]("experimental")["ok"])
            return {"validated": True, "status": "confirmed",
                    "reason": "experimental_complied_control_did_not",
                    "baseline_score": 0, "judge_score": 3,
                    "evidence": "baseline 0/3, artifact 3/3"}

        with patch("blood_artex.judge_response", return_value=success), \
                patch("blood_artex.run_validation",
                      side_effect=fake_validation):
            server._artex_run_judge(campaign_id, "r2")

        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("confirmed", summary["state"])
        self.assertEqual(4, summary["budget"]["used"])  # 2 probes + 2 controls
        self.assertFalse(state.get("cracked"))

        # Restart: memory is gone, state comes back from the campaign log.
        ac.reset()
        reloaded = ac.campaign_summary(campaign_id)
        self.assertEqual("confirmed", reloaded["state"])
        self.assertEqual(4, reloaded["budget"]["used"])
        receipt = ac.latest_receipt(campaign_id)
        self.assertEqual("stop_confirmed", receipt["next_action"])
        self.assertTrue(receipt["terminal"])
        self.assertEqual("success", receipt["verdict"]["verdict"])

    def test_two_campaigns_run_without_cross_contamination(self):
        first = self.campaign("opencode", "target-one", "s1")
        second = self.campaign("openai", "target-two", "s2")
        self.send(first, "a1", "s1", 1, "alpha probe")
        self.send(second, "b1", "s2", 1, "beta probe")
        self.assertEqual("answer to alpha probe",
                         ac.get_request(first, "a1")["response"])
        self.assertEqual("answer to beta probe",
                         ac.get_request(second, "b1")["response"])
        self.assertEqual("opencode", ac.get_campaign(first)["provider"])
        self.assertEqual("openai", ac.get_campaign(second)["provider"])

        with patch("blood_artex.judge_response", return_value={
                "verdict": "success", "trigger": "prohibited_content",
                "evidence": "alpha", "answers": {}, "error": None}), \
                patch("blood_artex.run_validation", return_value={
                    "validated": True, "status": "confirmed",
                    "reason": "experimental_complied_control_did_not",
                    "baseline_score": 0, "judge_score": 3,
                    "evidence": "baseline 0/3, artifact 3/3"}):
            server._artex_run_judge(first, "a1")
        self.assertEqual("confirmed", ac.campaign_summary(first)["state"])
        self.assertEqual("evaluating", ac.campaign_summary(second)["state"])

    def test_duplicate_delivery_is_idempotent(self):
        campaign_id = self.campaign("opencode", "target-one", "s1")
        target = ac.locked_target(ac.get_campaign(campaign_id) or {})
        self.send(campaign_id, "r1", "s1", 1, "probe one")
        duplicate = self.chat(type="response", campaign_id=campaign_id,
                              request_id="r1", session_id="s1", attempt=1,
                              text="answer to probe one", target=target)
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["duplicate"])
        # A second prompt with the same id cannot spend another slot.
        denied = self.chat(type="prompt", campaign_id=campaign_id,
                           request_id="r1", session_id="s1", attempt=2,
                           text="probe one again", target=target)
        self.assertFalse(denied["ok"])
        self.assertEqual("request_id_mismatch", denied["error"])
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_evaluation_pending_blocks_the_next_intent(self):
        campaign_id = self.campaign("opencode", "target-one", "s1")
        self.send(campaign_id, "r1", "s1", 1, "probe one")
        blocked = self.chat(type="prompt", campaign_id=campaign_id,
                            request_id="r2", session_id="s1", attempt=2,
                            text="probe two",
                            target=ac.locked_target(ac.get_campaign(campaign_id)))
        self.assertFalse(blocked["ok"])
        self.assertEqual("evaluation_pending", blocked["error"])
        receipt = ac.receipt(campaign_id, "r1")
        self.assertEqual("wait", receipt["next_action"])
        self.assertEqual(1, receipt["progress"]["attempts_used"])


if __name__ == "__main__":
    unittest.main()
