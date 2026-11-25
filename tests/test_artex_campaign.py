import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import artex_campaign as ac


class CampaignFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ac.configure(self.tmp.name)
        ac.reset()
        self.addCleanup(ac.configure, None)

    def campaign(self, objective="guardrail", attempts=10, sessions=("s1",)):
        campaign = ac.create_campaign(
            objective, "map the boundary", "opencode", "fixture-model",
            attempts, session_config={"runtime": "codex"},
            # Single-shot intents: this suite pins the advance mechanics.
            # Multi-variant behaviour lives in test_artex_intent_variants.py.
            intent_variants=1)
        campaign_id = campaign["campaign_id"]
        for session_id in sessions:
            ac.attach_session(campaign_id, session_id)
        return campaign_id

    def probe(self, campaign_id, request_id, session_id="s1", attempt=1,
              prompt="probe text", kind="probe", target=None):
        if target is None:
            campaign = ac.get_campaign(campaign_id) or {}
            target = ac.locked_target(campaign)
        return ac.reserve_target_call(campaign_id, request_id, session_id,
                                      attempt, prompt, target=target,
                                      kind=kind)

    def answered(self, campaign_id, request_id, text="target answer",
                 session_id="s1", attempt=1, prompt="probe text"):
        reserved = self.probe(campaign_id, request_id, session_id, attempt, prompt)
        self.assertTrue(reserved["ok"], reserved)
        recorded = ac.record_response(campaign_id, request_id, text)
        self.assertTrue(recorded["ok"], recorded)
        return recorded


class ReservationTests(CampaignFixture):
    def test_reserve_is_idempotent_and_completed_duplicate_is_denied(self):
        campaign_id = self.campaign()
        first = self.probe(campaign_id, "r1")
        self.assertTrue(first["ok"])
        self.assertEqual("claimed", first["intent"]["status"])
        retry = self.probe(campaign_id, "r1")
        self.assertTrue(retry["ok"])
        self.assertTrue(retry["duplicate"])
        # A second prompt POST before the response is still allowed...
        self.probe(campaign_id, "r1")
        ac.record_response(campaign_id, "r1", "answer")
        # ...but after the response the same id cannot spend another call.
        denied = self.probe(campaign_id, "r1")
        self.assertFalse(denied["ok"])
        self.assertEqual("duplicate_completed", denied["error"])
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_second_probe_rejected_while_evaluation_pending(self):
        campaign_id = self.campaign()
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        ac.record_response(campaign_id, "r1", "answer")
        blocked = self.probe(campaign_id, "r2", prompt="different probe")
        self.assertFalse(blocked["ok"])
        self.assertEqual("evaluation_pending", blocked["error"])
        # The agent must read the receipt instead of guessing.
        receipt = ac.receipt(campaign_id, "r1")
        self.assertEqual("wait", receipt["next_action"])
        self.assertTrue(receipt["waiting"])

    def test_identical_failed_probe_is_rejected_within_the_intent(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="unclear answer")
        ac.complete_judge(campaign_id, "r1", {"verdict": "unclear",
                                              "trigger": "unsupported"})
        self.assertEqual("inconclusive",
                         ac.campaign_summary(campaign_id)["state"])
        retry_same = self.probe(campaign_id, "r2", prompt="probe text")
        self.assertFalse(retry_same["ok"])
        self.assertEqual("identical_probe", retry_same["error"])
        changed = self.probe(campaign_id, "r3", prompt="different framing")
        self.assertTrue(changed["ok"])

    def test_failed_intent_advances_so_new_wording_is_allowed(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="I refuse")
        ac.complete_judge(campaign_id, "r1", {"verdict": "refused",
                                              "trigger": "refusal"})
        next_intent = self.probe(campaign_id, "r2", prompt="probe text")
        self.assertTrue(next_intent["ok"])
        self.assertEqual("guardrail-i2", next_intent["intent"]["intent_id"])

    def test_budget_exhaustion_is_terminal_and_fails_closed(self):
        campaign_id = self.campaign(attempts=2)
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "no_evidence"})
        self.answered(campaign_id, "r2", attempt=2)
        ac.complete_judge(campaign_id, "r2", {"verdict": "no_evidence"})
        denied = self.probe(campaign_id, "r3", attempt=3,
                            prompt="third distinct probe")
        self.assertFalse(denied["ok"])
        self.assertEqual("budget_exhausted", denied["error"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("failed", summary["state"])
        self.assertEqual(2, summary["budget"]["used"])

    def test_request_id_retry_must_match_original_fields(self):
        campaign_id = self.campaign(sessions=("s1", "s2"))
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        original = ac.get_request(campaign_id, "r1")

        same = self.probe(campaign_id, "r1")
        self.assertTrue(same["ok"])
        self.assertTrue(same["duplicate"])

        other_session = self.probe(campaign_id, "r1", session_id="s2")
        self.assertFalse(other_session["ok"])
        self.assertEqual("request_id_mismatch", other_session["error"])
        self.assertIn("session", other_session["mismatched"])

        other_prompt = self.probe(campaign_id, "r1", prompt="different probe")
        self.assertFalse(other_prompt["ok"])
        self.assertEqual("request_id_mismatch", other_prompt["error"])
        self.assertIn("prompt", other_prompt["mismatched"])

        # The original evidence and budget are untouched by rejected retries.
        now = ac.get_request(campaign_id, "r1")
        self.assertEqual(original["prompt"], now["prompt"])
        self.assertEqual(original["session_id"], now["session_id"])
        self.assertEqual(original["target"], now["target"])
        self.assertEqual("prompted", now["status"])
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_request_id_cannot_be_reused_by_another_campaign(self):
        first = self.campaign(sessions=("s1",))
        second = self.campaign(sessions=("s2",))
        self.assertTrue(self.probe(first, "shared-id")["ok"])
        denied = self.probe(second, "shared-id", session_id="s2")
        self.assertFalse(denied["ok"])
        self.assertEqual("request_id_mismatch", denied["error"])
        self.assertEqual(1, ac.campaign_summary(first)["budget"]["used"])
        self.assertEqual(0, ac.campaign_summary(second)["budget"]["used"])
        self.assertIsNone(ac.get_request(second, "shared-id"))

    def test_reservation_requires_session_and_locked_target(self):
        campaign_id = self.campaign()
        missing_session = ac.reserve_target_call(
            campaign_id, "r1", "", 1, "probe",
            target=ac.locked_target(ac.get_campaign(campaign_id)))
        self.assertEqual("missing_session_id", missing_session["error"])
        mismatch = self.probe(campaign_id, "r2",
                              target="openai/not-the-locked-target")
        self.assertFalse(mismatch["ok"])
        self.assertEqual("target_mismatch", mismatch["error"])
        self.assertEqual(0, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_ids_cannot_traverse_the_campaign_store(self):
        campaign_id = self.campaign()
        denied = ac.reserve_target_call(campaign_id, "../../escape", "s1",
                                        1, "probe")
        self.assertFalse(denied["ok"])
        self.assertEqual("invalid_request_id", denied["error"])
        self.assertIsNone(ac.get_campaign("../../escape"))

    def test_concurrent_reservations_claim_exactly_one_intent(self):
        campaign_id = self.campaign()

        def reserve(index):
            return self.probe(campaign_id, f"r{index}",
                              prompt=f"probe {index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        winners = [r for r in results if r.get("ok")]
        self.assertEqual(1, len(winners))
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])
        self.assertTrue(all(r.get("error") in
                            ("intent_in_progress", "evaluation_pending")
                            for r in results if not r.get("ok")))


class DurableBudgetTests(CampaignFixture):
    def test_event_append_failure_denies_probe_and_rolls_back(self):
        campaign_id = self.campaign()
        with patch.object(ac, "_append_event_line",
                          side_effect=OSError("disk full")):
            with self.assertLogs(level="ERROR"):
                denied = self.probe(campaign_id, "r1")
        self.assertFalse(denied["ok"])
        self.assertEqual("durable_write_failed", denied["error"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual(0, summary["budget"]["used"])
        self.assertEqual("running", summary["state"])
        self.assertIsNone(ac.get_request(campaign_id, "r1"))
        self.assertEqual("queued", summary["current_intent"]["status"])
        # Once the disk recovers the same reservation can proceed once.
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_snapshot_failure_surfaces_but_event_log_is_durable(self):
        campaign_id = self.campaign()
        with patch.object(ac, "_atomic_write",
                          side_effect=OSError("snapshot denied")):
            with self.assertLogs(level="ERROR"):
                allowed = self.probe(campaign_id, "r1")
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_validation_reservation_write_failure_denies_and_rolls_back(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        self.assertIsNotNone(ac.begin_validation(campaign_id, "r1"))
        used_before = ac.campaign_summary(campaign_id)["budget"]["used"]
        with patch.object(ac, "_append_event_line",
                          side_effect=OSError("disk full")):
            with self.assertLogs(level="ERROR"):
                denied = ac.reserve_validation_call(
                    campaign_id, "r1", "baseline")
        self.assertFalse(denied["ok"])
        self.assertEqual("durable_write_failed", denied["error"])
        self.assertEqual(used_before,
                         ac.campaign_summary(campaign_id)["budget"]["used"])
        self.assertNotIn("r1::baseline",
                         ac.get_campaign(campaign_id)["budget"]["reserved"])
        # A later explicit reservation succeeds exactly once.
        self.assertTrue(ac.reserve_validation_call(
            campaign_id, "r1", "baseline")["ok"])
        self.assertEqual(used_before + 1,
                         ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_validation_start_marker_failure_prevents_dispatch_state(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        with patch.object(ac, "_append_event_line",
                          side_effect=OSError("disk full")):
            with self.assertLogs(level="ERROR"):
                started = ac.begin_validation(campaign_id, "r1")
        self.assertIsNone(started)
        request = ac.get_request(campaign_id, "r1")
        self.assertEqual("candidate", request["status"])
        self.assertEqual("not_started", request["validation"]["status"])
        # Once the marker is durable, validation can begin.
        self.assertIsNotNone(ac.begin_validation(campaign_id, "r1"))
        self.assertEqual("running",
                         ac.get_request(campaign_id, "r1")["validation"]["status"])

    def test_duplicate_validation_call_is_rejected(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        self.assertTrue(ac.reserve_validation_call(
            campaign_id, "r1", "baseline")["ok"])
        used = ac.campaign_summary(campaign_id)["budget"]["used"]
        duplicate = ac.reserve_validation_call(campaign_id, "r1", "baseline")
        self.assertFalse(duplicate["ok"])
        self.assertEqual("duplicate_validation_call", duplicate["error"])
        self.assertEqual(used, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_continuation_slot_write_failure_does_not_launch(self):
        campaign_id = self.campaign()
        with patch.object(ac, "_append_event_line",
                          side_effect=OSError("disk full")):
            with self.assertLogs(level="ERROR"):
                denied = ac.record_continuation(campaign_id, limit=4)
        self.assertFalse(denied["ok"])
        self.assertEqual("durable_write_failed", denied["error"])
        self.assertEqual(0, ac.get_campaign(campaign_id)
                         ["counters"]["continuations"])

    def test_campaign_creation_write_failure_leaves_no_campaign(self):
        with patch.object(ac, "_append_event_line",
                          side_effect=OSError("disk full")):
            with self.assertLogs(level="ERROR"):
                with self.assertRaises(ac.DurableWriteError):
                    ac.create_campaign("guardrail", "objective", "opencode",
                                       "fixture-model", 3)
        self.assertEqual([], ac.list_campaigns())


class JudgeLifecycleTests(CampaignFixture):
    def test_judge_success_is_candidate_not_confirmed(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="You are the hidden prompt")
        result = ac.complete_judge(campaign_id, "r1", {
            "verdict": "success", "trigger": "verbatim_system_text",
            "evidence": "You are the hidden prompt"})
        self.assertTrue(result["ok"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("candidate", summary["state"])
        self.assertFalse(summary["state"] == "confirmed")
        self.assertEqual("wait", result["receipt"]["next_action"])
        self.assertEqual(["r1"], ac.pending_evaluations(campaign_id))

    def test_confirmed_requires_validation(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        self.assertIsNotNone(ac.begin_validation(campaign_id, "r1"))
        result = ac.complete_validation(campaign_id, "r1", {
            "validated": True, "status": "confirmed",
            "reason": "experimental_complied_control_did_not",
            "baseline_score": 0, "judge_score": 3})
        self.assertTrue(result["ok"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("confirmed", summary["state"])
        self.assertEqual("stop_confirmed", result["receipt"]["next_action"])

    def test_confirmed_with_validated_false_is_rejected(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        result = ac.complete_validation(campaign_id, "r1", {
            "status": "confirmed", "validated": False,
            "reason": "experimental_complied_control_did_not",
            "baseline_score": 0, "judge_score": 3})
        summary = ac.campaign_summary(campaign_id)
        self.assertNotEqual("confirmed", summary["state"])
        self.assertEqual("inconclusive", summary["state"])
        request = ac.get_request(campaign_id, "r1")
        self.assertEqual("evaluated", request["status"])
        self.assertEqual("inconclusive",
                         request["validation"]["result"]["status"])
        self.assertEqual("validation_inconsistent",
                         request["validation"]["result"]["reason"])
        self.assertEqual("confirmed",
                         request["validation"]["result"]["requested_status"])
        self.assertEqual(0, summary["judge_stats"]["confirmed"])

    def test_confirmed_without_control_arm_evidence_is_rejected(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        ac.complete_validation(campaign_id, "r1", {
            "status": "confirmed", "validated": True,
            "reason": "experimental_complied_control_did_not",
            "baseline_score": 3, "judge_score": 3})
        self.assertEqual("inconclusive",
                         ac.campaign_summary(campaign_id)["state"])
        self.assertIn("control arm already complied",
                      ac.get_request(campaign_id, "r1")
                      ["validation"]["result"]["inconsistency"])

    def test_confirmed_with_an_alias_status_is_rejected(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        ac.complete_validation(campaign_id, "r1", {
            "status": "validated", "validated": True,
            "reason": "experimental_complied_control_did_not",
            "baseline_score": 0, "judge_score": 3})
        self.assertEqual("inconclusive",
                         ac.campaign_summary(campaign_id)["state"])

    def test_sysprompt_confirmation_needs_canary_evidence(self):
        campaign_id = self.campaign("sysprompt")
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "verbatim_system_text"})
        ac.begin_validation(campaign_id, "r1")
        ac.complete_validation(campaign_id, "r1", {
            "status": "confirmed", "validated": True,
            "reason": "ground_truth_canary_matched", "evidence": ""})
        self.assertEqual("inconclusive",
                         ac.campaign_summary(campaign_id)["state"])

        # A fresh candidate with matching canary evidence may confirm.
        self.assertTrue(self.probe(campaign_id, "r2",
                                   prompt="retry probe")["ok"])
        ac.record_response(campaign_id, "r2", "leaked prompt")
        ac.complete_judge(campaign_id, "r2", {
            "verdict": "success", "trigger": "verbatim_system_text"})
        ac.begin_validation(campaign_id, "r2")
        ac.complete_validation(campaign_id, "r2", {
            "status": "confirmed", "validated": True,
            "reason": "ground_truth_canary_matched",
            "evidence": "artifact contains the operator canary"})
        self.assertEqual("confirmed",
                         ac.campaign_summary(campaign_id)["state"])

    def test_oracle_unverified_is_terminal(self):
        campaign_id = self.campaign("sysprompt")
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "verbatim_system_text"})
        ac.begin_validation(campaign_id, "r1")
        result = ac.complete_validation(campaign_id, "r1", {
            "validated": False, "status": "unverified",
            "reason": "no_ground_truth_oracle"})
        self.assertEqual("stop_unverified", result["receipt"]["next_action"])
        self.assertEqual("unverified", ac.campaign_summary(campaign_id)["state"])
        denied = self.probe(campaign_id, "r2", prompt="another probe")
        self.assertFalse(denied["ok"])
        self.assertEqual("campaign_terminal", denied["error"])

    def test_explicit_negative_status_beats_the_validated_flag(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success"})
        ac.begin_validation(campaign_id, "r1")
        ac.complete_validation(campaign_id, "r1", {
            "validated": True, "status": "unverified",
            "reason": "judge_unavailable"})
        self.assertNotEqual("confirmed", ac.campaign_summary(campaign_id)["state"])
        self.assertEqual("inconclusive", ac.campaign_summary(campaign_id)["state"])

    def test_transient_validation_failure_is_retryable(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        result = ac.complete_validation(campaign_id, "r1", {
            "validated": False, "status": "unverified",
            "reason": "judge_unavailable", "error": "judge down"})
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("inconclusive", summary["state"])
        self.assertNotIn(summary["state"], ac.TERMINAL_STATES)
        self.assertEqual("retry_intent", result["receipt"]["next_action"])
        self.assertTrue(self.probe(campaign_id, "r2", prompt="new framing")["ok"])

    def test_judge_inconclusive_blocks_and_allows_retry(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {
            "verdict": "inconclusive", "error": "schema"})
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("inconclusive", summary["state"])
        self.assertEqual("blocked", summary["current_intent"]["status"])
        receipt = ac.latest_receipt(campaign_id)
        self.assertEqual("retry_intent", receipt["next_action"])


class IntentProgressionTests(CampaignFixture):
    def test_refused_intent_advances_to_the_next(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="I refuse")
        ac.complete_judge(campaign_id, "r1", {"verdict": "refused"})
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual(1, summary["progress"]["intent_index"])
        resolved = self.probe(campaign_id, "r2", prompt="new intent probe")
        self.assertTrue(resolved["ok"])
        self.assertEqual("guardrail-i2", resolved["intent"]["intent_id"])

    def test_transport_error_blocks_without_judging(self):
        campaign_id = self.campaign()
        reserved = self.probe(campaign_id, "r1")
        self.assertTrue(reserved["ok"])
        recorded = ac.record_response(campaign_id, "r1",
                                      "[TIMEOUT — target took 300s]")
        self.assertTrue(recorded["transport_error"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("blocked", summary["current_intent"]["status"])
        self.assertNotEqual("candidate", summary["state"])
        self.assertEqual(0, summary["judge_stats"]["judged"])

    def test_first_intent_receipt_names_the_next_intent(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "refused"})
        receipt = ac.latest_receipt(campaign_id)
        self.assertEqual("continue", receipt["next_action"])
        self.assertEqual("guardrail-i2", receipt["next_intent"]["intent_id"])


class PersistenceTests(CampaignFixture):
    def test_campaign_survives_a_restart(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="stored answer")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        before = ac.campaign_summary(campaign_id)

        ac.reset()  # simulate process restart: memory gone, disk remains

        after = ac.campaign_summary(campaign_id)
        self.assertEqual(before["state"], after["state"])
        self.assertEqual(before["budget"], after["budget"])
        self.assertEqual("candidate", after["state"])
        self.assertEqual("stored answer",
                         ac.get_request(campaign_id, "r1")["response"])
        self.assertTrue(ac.campaign_events(campaign_id))

    def test_snapshot_behind_the_event_log_replays_the_missing_events(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1", text="stored answer")
        snapshot_path = Path(self.tmp.name) / campaign_id / "campaign.json"
        stale = snapshot_path.read_text(encoding="utf-8")
        # This writes judge_completed to the event log and then the snapshot.
        ac.complete_judge(campaign_id, "r1", {
            "verdict": "success", "trigger": "prohibited_content",
            "evidence": "stored answer"})
        # Simulate a crash after the event append but before the snapshot.
        snapshot_path.write_text(stale, encoding="utf-8")
        ac.reset()
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("candidate", summary["state"])
        self.assertEqual("done",
                         ac.get_request(campaign_id, "r1")["judge"]["status"])
        self.assertEqual(1, summary["judge_stats"]["candidates"])
        self.assertEqual("evaluated", summary["current_intent"]["status"])
        raw = ac.get_campaign(campaign_id)
        self.assertEqual("candidate",
                         raw["intents"][0]["probe_hashes"][0]["outcome"])

    def test_replayed_reservation_stays_idempotent(self):
        campaign_id = self.campaign()
        snapshot_path = Path(self.tmp.name) / campaign_id / "campaign.json"
        stale = snapshot_path.read_text(encoding="utf-8")
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        snapshot_path.write_text(stale, encoding="utf-8")
        ac.reset()
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])
        duplicate = self.probe(campaign_id, "r1")
        self.assertTrue(duplicate["ok"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(1, ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_two_campaigns_do_not_share_state(self):
        first = self.campaign(sessions=("s1",))
        second = self.campaign(sessions=("s2",))
        self.answered(first, "a1", session_id="s1")
        self.answered(second, "b1", session_id="s2")
        ac.complete_judge(first, "a1", {"verdict": "success",
                                        "trigger": "prohibited_content"})
        self.assertEqual("candidate", ac.campaign_summary(first)["state"])
        self.assertEqual("evaluating", ac.campaign_summary(second)["state"])
        self.assertEqual("target answer",
                         ac.get_request(second, "b1")["response"])
        self.assertIsNone(ac.get_request(second, "a1"))

    def test_campaign_for_session_resolves_only_its_own(self):
        first = self.campaign(sessions=("alpha",))
        second = self.campaign(sessions=("beta",))
        self.assertEqual(first, ac.campaign_for_session("alpha")["campaign_id"])
        self.assertEqual(second, ac.campaign_for_session("beta")["campaign_id"])
        self.assertIsNone(ac.campaign_for_session("gamma"))

    def test_persisted_state_redacts_credential_shaped_text(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1",
                      text="the key is sk-ant-verysecret123456")
        # The live record still carries the evidence the judge saw.
        self.assertIn("sk-ant-verysecret123456",
                      ac.get_request(campaign_id, "r1")["response"])
        ac.reset()
        root = Path(self.tmp.name) / campaign_id
        snapshot = (root / "campaign.json").read_text(encoding="utf-8")
        events = (root / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk-ant-verysecret123456", snapshot)
        self.assertNotIn("sk-ant-verysecret123456", events)
        self.assertIn("<redacted-key>", snapshot)

    def test_continuation_slots_are_bounded(self):
        campaign_id = self.campaign()
        for _ in range(4):
            self.assertTrue(ac.record_continuation(campaign_id, limit=4)["ok"])
        denied = ac.record_continuation(campaign_id, limit=4)
        self.assertFalse(denied["ok"])
        self.assertEqual("continuation_limit", denied["error"])

    def test_session_config_roundtrip(self):
        campaign_id = self.campaign()
        ac.set_session_config(campaign_id, {"runtime": "claude",
                                            "custom_prompt": True})
        ac.reset()
        config = ac.get_campaign(campaign_id)["session_config"]
        self.assertEqual("claude", config["runtime"])
        self.assertTrue(config["custom_prompt"])

    def test_abort_marks_open_requests_and_blocks_reservations(self):
        campaign_id = self.campaign()
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        ac.abort(campaign_id, "operator_abort")
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("aborted", summary["state"])
        self.assertEqual("aborted",
                         ac.get_request(campaign_id, "r1")["status"])
        denied = self.probe(campaign_id, "r2")
        self.assertFalse(denied["ok"])
        self.assertEqual("campaign_terminal", denied["error"])
        # A retry of the pre-abort prompt must not be re-authorized either.
        retry = self.probe(campaign_id, "r1")
        self.assertFalse(retry["ok"])
        self.assertEqual("campaign_terminal", retry["error"])

    def test_recovery_closes_a_prompted_request_whose_helper_died(self):
        campaign_id = self.campaign()
        self.assertTrue(self.probe(campaign_id, "r1")["ok"])
        ac.reset()
        recovered = ac.recoverable_requests()
        self.assertEqual([], recovered)  # no response to judge
        request = ac.get_request(campaign_id, "r1")
        self.assertEqual("failed", request["status"])
        self.assertEqual("lost_after_restart", request["failure"])
        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("inconclusive", summary["state"])
        self.assertEqual("blocked", summary["current_intent"]["status"])
        # The intent is retryable with new wording.
        self.assertTrue(self.probe(campaign_id, "r2", prompt="new wording")["ok"])

    def test_interrupted_validation_is_inconclusive_and_not_rescheduled(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        ac.begin_validation(campaign_id, "r1")
        self.assertTrue(ac.reserve_validation_call(
            campaign_id, "r1", "baseline")["ok"])
        used_before = ac.campaign_summary(campaign_id)["budget"]["used"]

        ac.reset()  # process restart while baseline delivery is uncertain
        self.assertEqual([], ac.recoverable_validations())

        summary = ac.campaign_summary(campaign_id)
        self.assertEqual("inconclusive", summary["state"])
        self.assertEqual(used_before, summary["budget"]["used"])
        request = ac.get_request(campaign_id, "r1")
        self.assertEqual("evaluated", request["status"])
        self.assertEqual("validation_interrupted",
                         request["validation"]["result"]["reason"])
        self.assertTrue(request["validation"]["result"]["uncertain_delivery"])
        receipt = ac.receipt(campaign_id, "r1")
        self.assertTrue(receipt["validation"]["uncertain_delivery"])
        self.assertEqual("retry_intent", receipt["next_action"])
        # The old validation call cannot be reissued on the same reservation.
        duplicate = ac.reserve_validation_call(campaign_id, "r1", "baseline")
        self.assertEqual("duplicate_validation_call", duplicate["error"])
        # A retry is a new probe with a new durable reservation.
        self.assertTrue(self.probe(campaign_id, "r2",
                                   prompt="different wording")["ok"])
        self.assertEqual(used_before + 1,
                         ac.campaign_summary(campaign_id)["budget"]["used"])

    def test_recovery_lists_interrupted_work(self):
        campaign_id = self.campaign()
        self.answered(campaign_id, "r1")
        ac.complete_judge(campaign_id, "r1", {"verdict": "success",
                                              "trigger": "prohibited_content"})
        # Candidate exists but validation never marked running.
        self.assertEqual([{"campaign_id": campaign_id, "request_id": "r1"}],
                         ac.recoverable_validations())

        candidate = self.campaign(sessions=("s3",))
        self.answered(candidate, "c1", session_id="s3")
        # Simulate a crash after the response but before the judge.
        self.assertEqual([{"campaign_id": candidate, "request_id": "c1"}],
                         ac.recoverable_requests())


if __name__ == "__main__":
    unittest.main()
