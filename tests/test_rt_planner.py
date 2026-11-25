"""Pure-planner tests for the rt_session_ended decision table.

The planner is a pure function: same inputs -> same action, no mutation, no
I/O. The thin executors in server.py are covered by the existing
AutoContinue/rt_session_ended integration tests in test_hardening_pass.py.
"""
import unittest

import server


def snap(**over):
    base = {
        "active": True,
        "session_id": "s1",
        "aborted": False,
        "cracked": False,
        "attempts_seen": 0,
        "max_attempts": 10,
        "retries_used": 0,
        "max_retries": 5,
        "auto_continue": True,
        "original_prompt": "probe",
        "target_budget": None,
        "session_config": {},
        "provider": "p",
        "model": "m",
        "objective": "obj",
    }
    base.update(over)
    return base


def plan(state, session_id="s1", **kw):
    return server.plan_redteam_next_action(state, session_id=session_id, **kw)


class RtPlannerTests(unittest.TestCase):
    def test_wrong_session_is_ignored(self):
        self.assertEqual(plan(snap(), session_id="other")["action"], "ignore")
        self.assertEqual(plan(snap(active=False))["action"], "ignore")

    def test_aborted_ends_without_broadcast(self):
        self.assertEqual(plan(snap(aborted=True))["action"], "end")

    def test_complete_when_attempts_done(self):
        r = plan(snap(attempts_seen=10))
        self.assertEqual((r["action"], r["reason"]), ("end", "complete"))

    def test_complete_takes_precedence_over_cracked(self):
        # Preserved legacy ordering: the attempts-exhausted branch returns
        # before the cracked broadcast is ever sent.
        r = plan(snap(attempts_seen=10, cracked=True))
        self.assertEqual((r["action"], r["reason"]), ("end", "complete"))

    def test_budget_exhaustion_uses_ledger_not_attempt_ids(self):
        budget = {"attempt_budget_id": "s1", "max_attempts": 4}
        r = plan(snap(attempts_seen=2, target_budget=budget), ledger_used=4)
        self.assertEqual((r["action"], r["reason"]), ("end", "budget_exhausted"))

    def test_budget_exhaustion_takes_precedence_over_cracked(self):
        budget = {"attempt_budget_id": "s1", "max_attempts": 4}
        r = plan(snap(attempts_seen=2, cracked=True, target_budget=budget), ledger_used=4)
        self.assertEqual((r["action"], r["reason"]), ("end", "budget_exhausted"))

    def test_cracked_broadcast(self):
        r = plan(snap(attempts_seen=3, cracked=True))
        self.assertEqual(r["action"], "cracked")
        self.assertEqual(r["attempts"], 3)

    def test_stalled_retries_with_incrementing_retry_num(self):
        r = plan(snap(attempts_seen=0, retries_used=2))
        self.assertEqual(r["action"], "retry")
        self.assertEqual(r["retry_num"], 3)
        self.assertIsNone(r["fallback_agent"])

    def test_fallback_agents_on_retries_four_and_five(self):
        r4 = plan(snap(attempts_seen=0, retries_used=3))
        r5 = plan(snap(attempts_seen=0, retries_used=4))
        self.assertEqual(r4["fallback_agent"], "rt-fallback-alpha")
        self.assertEqual(r5["fallback_agent"], "rt-fallback-beta")

    def test_hermes_only_on_first_exhaustion_with_key(self):
        base = snap(attempts_seen=0, retries_used=5, max_retries=5)
        self.assertEqual(plan(base, hermes_available=True)["action"], "hermes_fallback")
        self.assertEqual(plan(base, hermes_available=False)["action"], "end")
        # After the Hermes attempt, retries_used is max+1: never Hermes again.
        later = snap(attempts_seen=0, retries_used=6, max_retries=5)
        self.assertEqual(plan(later, hermes_available=True)["action"], "end")

    def test_retries_exhausted_reason(self):
        r = plan(snap(attempts_seen=0, retries_used=5), hermes_available=False)
        self.assertEqual((r["action"], r["reason"]), ("end", "retries_exhausted"))

    def test_engaged_auto_continue_off_ends(self):
        r = plan(snap(attempts_seen=5, auto_continue=False))
        self.assertEqual((r["action"], r["reason"]), ("end", "auto_continue_off"))

    def test_engaged_continues_with_remaining_count(self):
        r = plan(snap(attempts_seen=5, max_attempts=10))
        self.assertEqual(r["action"], "continue")
        self.assertEqual(r["remaining"], 5)
        self.assertEqual(r["resume_from"], 6)

    def test_planner_does_not_mutate_input(self):
        state = snap(attempts_seen=5)
        frozen = dict(state)
        plan(state)
        self.assertEqual(state, frozen)


class RtContinuationPromptTests(unittest.TestCase):
    def test_prompt_names_target_attempts_and_learnings(self):
        text = server.render_redteam_continuation(
            provider="prov", model="mod", objective="obj",
            attempts_done=3, max_attempts=10, learnings="LEARNINGS-HERE")
        self.assertIn("prov", text)
        self.assertIn("mod", text)
        self.assertIn("LEARNINGS-HERE", text)
        self.assertIn("start from attempt 4", text)
        self.assertIn("7 remaining", text)

    def test_prompt_contains_target_lock_rule(self):
        text = server.render_redteam_continuation(
            provider="prov", model="mod", objective="obj",
            attempts_done=1, max_attempts=2, learnings="")
        self.assertIn("TARGET LOCK", text)
        self.assertIn("Attack ONLY mod via prov", text)


if __name__ == "__main__":
    unittest.main()
