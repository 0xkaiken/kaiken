"""A single refusal must not burn an attack direction.

Evidence: campaign artex-1886f1d5edf7496f (2026-11-17) ended
``all_intents_refused`` with 4/100 budget used -- one probe per intent.
"""
import tempfile
import unittest
from unittest.mock import patch

import artex_campaign as ac


class VariantFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        ac.configure(self.tmp.name)
        ac.reset()
        self.addCleanup(ac.configure, None)

    def campaign(self, variants=None, attempts=50):
        kwargs = {} if variants is None else {"intent_variants": variants}
        campaign = ac.create_campaign(
            "guardrail", "map the boundary", "opencode", "fixture-model",
            attempts, **kwargs)
        ac.attach_session(campaign["campaign_id"], "s1")
        return campaign["campaign_id"]

    def refuse(self, campaign_id, request_id, prompt, verdict="refused"):
        campaign = ac.get_campaign(campaign_id)
        reserved = ac.reserve_target_call(
            campaign_id, request_id, "s1", 1, prompt,
            target=ac.locked_target(campaign))
        self.assertTrue(reserved["ok"], reserved)
        self.assertTrue(ac.record_response(campaign_id, request_id, "no.")["ok"])
        ac.complete_judge(campaign_id, request_id, {"verdict": verdict})
        return reserved


class IntentVariantTests(VariantFixture):
    def test_first_refusal_keeps_the_intent_open_for_a_variant(self):
        cid = self.campaign(variants=3)
        self.refuse(cid, "r1", "wording one")
        summary = ac.campaign_summary(cid)
        self.assertEqual("guardrail-i1", summary["current_intent"]["intent_id"])
        self.assertEqual("blocked", summary["current_intent"]["status"])
        self.assertEqual("refused_variant", summary["current_intent"]["failure_reason"])
        self.assertEqual(1, summary["current_intent"]["variants_used"])
        self.assertEqual(3, summary["current_intent"]["variants_max"])
        self.assertEqual("retry_intent", ac.latest_receipt(cid)["next_action"])

    def test_identical_wording_is_still_rejected(self):
        cid = self.campaign(variants=3)
        self.refuse(cid, "r1", "wording one")
        campaign = ac.get_campaign(cid)
        again = ac.reserve_target_call(cid, "r2", "s1", 2, "wording one",
                                       target=ac.locked_target(campaign))
        self.assertFalse(again["ok"])
        self.assertEqual("identical_probe", again["error"])

    def test_intent_fails_after_the_variant_cap(self):
        cid = self.campaign(variants=2)
        first = self.refuse(cid, "r1", "wording one")
        second = self.refuse(cid, "r2", "wording two")
        self.assertEqual("guardrail-i1", first["intent"]["intent_id"])
        self.assertEqual("guardrail-i1", second["intent"]["intent_id"])
        summary = ac.campaign_summary(cid)
        self.assertEqual("guardrail-i2", summary["current_intent"]["intent_id"])
        campaign = ac.get_campaign(cid)
        self.assertEqual("failed", campaign["intents"][0]["status"])
        self.assertEqual("refused", campaign["intents"][0]["failure_reason"])

    def test_no_evidence_counts_against_the_same_cap(self):
        cid = self.campaign(variants=2)
        self.refuse(cid, "r1", "a", verdict="no_evidence")
        self.assertEqual("guardrail-i1",
                         ac.campaign_summary(cid)["current_intent"]["intent_id"])
        self.refuse(cid, "r2", "b", verdict="refused")
        self.assertEqual("guardrail-i2",
                         ac.campaign_summary(cid)["current_intent"]["intent_id"])

    def test_all_refused_is_reported_only_after_every_variant(self):
        cid = self.campaign(variants=2)
        n_intents = len(ac.get_campaign(cid)["intents"])
        n = 0
        for i in range(n_intents):
            for v in range(2):
                n += 1
                self.refuse(cid, f"r{n}", f"intent {i} variant {v}")
        campaign = ac.get_campaign(cid)
        self.assertEqual("refused", campaign["state"])
        self.assertEqual("all_intents_refused", campaign["terminal_reason"])
        self.assertEqual(2 * n_intents, campaign["budget"]["used"])

    def test_variant_state_survives_restart(self):
        cid = self.campaign(variants=3)
        self.refuse(cid, "r1", "wording one")
        before = ac.campaign_summary(cid)["current_intent"]
        ac.reset()
        after = ac.campaign_summary(cid)["current_intent"]
        self.assertEqual(before, after)
        self.assertEqual(3, ac.get_campaign(cid)["intent_variants"])

    def test_legacy_campaign_without_field_keeps_single_shot(self):
        cid = self.campaign(variants=3)
        with ac._LOCK:
            ac._CAMPAIGNS[cid].pop("intent_variants")
        self.refuse(cid, "r1", "wording one")
        self.assertEqual("guardrail-i2",
                         ac.campaign_summary(cid)["current_intent"]["intent_id"])

    def test_env_default_and_clamp(self):
        with patch.dict("os.environ", {"PLINY_ARTEX_INTENT_VARIANTS": "99"}):
            cid = self.campaign()
        self.assertEqual(ac.MAX_INTENT_VARIANTS, ac.get_campaign(cid)["intent_variants"])
        with patch.dict("os.environ", {"PLINY_ARTEX_INTENT_VARIANTS": "junk"}):
            cid = self.campaign()
        self.assertEqual(ac.DEFAULT_INTENT_VARIANTS, ac.get_campaign(cid)["intent_variants"])
        cid = self.campaign(variants=0)
        self.assertEqual(1, ac.get_campaign(cid)["intent_variants"])



class ContractTextTests(unittest.TestCase):
    def test_contract_names_the_variant_budget(self):
        import blood_artex as ba
        text = ba.render_mission("guardrail", "x", "p/m", 20, intent_variants=3)
        self.assertIn("up to 3 distinct wording(s) per intent", text)
        self.assertIn("retry_intent", text)
        self.assertNotIn("one per attempt", text)

    def test_continuation_carries_the_variant_budget(self):
        import blood_artex as ba
        text = ba.render_continuation("guardrail", "x", "p/m", "artex-x", {},
                                      intent_variants=4)
        self.assertIn("up to 4 distinct wording(s)", text)

    def test_receipt_shows_variant_progress(self):
        fixture = VariantFixture("setUp")
        fixture.setUp()
        try:
            cid = fixture.campaign(variants=3)
            fixture.refuse(cid, "r1", "wording one")
            receipt = ac.latest_receipt(cid)
            self.assertEqual(1, receipt["next_intent"]["variants_used"])
            self.assertEqual(3, receipt["next_intent"]["variants_max"])
        finally:
            fixture.doCleanups()

if __name__ == "__main__":
    unittest.main()
