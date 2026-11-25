"""RedTeamRunRegistry: per-run state replacing the single global slot.

The compatibility contract: ``server._rt_state`` always IS the last-started
run's state dict (same object), so legacy readers keep working while pack
members no longer cross-contaminate attempts, crack flags or continuation.
"""
import unittest
from unittest.mock import patch

import server


class RegistryUnitTests(unittest.TestCase):
    def setUp(self):
        self.registry = server.RedTeamRunRegistry()

    def test_start_creates_isolated_runs(self):
        a = self.registry.start("s-a", max_attempts=5, provider="p", model="m",
                                objective="o", original_prompt="x")
        b = self.registry.start("s-b", max_attempts=5, provider="p", model="m",
                                objective="o", original_prompt="x")
        self.assertIsNot(a, b)
        a["attempts_seen"] = 4
        self.assertEqual(b["attempts_seen"], 0)

    def test_for_session_finds_only_owning_run(self):
        self.registry.start("s-a", max_attempts=5, provider="p", model="m",
                            objective="o", original_prompt="x")
        self.assertIsNone(self.registry.for_session("s-other"))
        self.assertIsNotNone(self.registry.for_session("s-a"))

    def test_rekey_moves_session_index_not_identity(self):
        run = self.registry.start("s-a", max_attempts=5, provider="p", model="m",
                                  objective="o", original_prompt="x")
        self.registry.rekey("s-a", "s-a-retry")
        self.assertIsNone(self.registry.for_session("s-a"))
        self.assertIs(self.registry.for_session("s-a-retry"), run)
        self.assertEqual(run["session_id"], "s-a-retry")


class RegistryIntegrationTests(unittest.TestCase):
    def test_rt_start_rebinds_compat_slot_to_last_started_run(self):
        with server._rt_state_lock:
            saved = server._rt_state
        try:
            server.rt_start("run-a", 10, "p", "m", "o", "prompt-a")
            slot_a = server._rt_state
            server.rt_start("run-b", 10, "p", "m", "o", "prompt-b")
            self.assertIsNot(server._rt_state, slot_a)
            self.assertEqual(server._rt_state["session_id"], "run-b")
            # The first run survives in the registry with its own state.
            run_a = server._rt_registry.for_session("run-a")
            self.assertIs(run_a, slot_a)
            self.assertEqual(run_a["original_prompt"], "prompt-a")
        finally:
            with server._rt_state_lock:
                server._rt_state = saved

    def test_pack_members_do_not_cross_contaminate_attempts_or_crack(self):
        with server._rt_state_lock:
            saved = server._rt_state
        try:
            server.rt_start("member-1", 10, "p", "m", "o", "pa")
            server.rt_start("member-2", 10, "p", "m", "o", "pb")
            server.rt_track_attempt(7, session_id="member-1")
            server.rt_mark_cracked("member-1")
            run1 = server._rt_registry.for_session("member-1")
            run2 = server._rt_registry.for_session("member-2")
            self.assertEqual(run1["attempts_seen"], 7)
            self.assertTrue(run1["cracked"])
            # member-2 started later and is the compat slot — untouched.
            self.assertEqual(run2["attempts_seen"], 0)
            self.assertFalse(run2["cracked"])
        finally:
            with server._rt_state_lock:
                server._rt_state = saved


class PackContinuationTests(unittest.TestCase):
    """The registry's reason to exist: when pack member 1 ends, its own run
    continues — not whichever member happened to start last."""

    def test_end_of_first_member_continues_first_members_run(self):
        with server._rt_state_lock:
            saved = server._rt_state
        try:
            server.rt_start("m1", 10, "prov", "mod", "obj", "prompt-1",
                            session_config={"runtime": "opencode"})
            server.rt_start("m2", 10, "prov", "mod", "obj", "prompt-2",
                            session_config={"runtime": "opencode"})
            server.rt_track_attempt(5, session_id="m1")
            with patch.object(server, "launch_session",
                              return_value={"id": "m1-cont"}) as launched,                     patch.object(server, "broadcast_sse"),                     patch.object(server, "build_redteam_learnings", return_value=""):
                server.rt_session_ended("m1")
            # The continuation is built from member-1's own progress: 5/10
            # done, resume at 6 — not member-2's zero-attempt state.
            prompt = launched.call_args[0][0]
            self.assertIn("5/10 attempts done", prompt)
            run1 = server._rt_registry.for_session("m1-cont")
            self.assertIsNotNone(run1)
            self.assertEqual(run1["attempts_seen"], 5)
            # Member-2's run is untouched and still owns its own session.
            run2 = server._rt_registry.for_session("m2")
            self.assertTrue(run2["active"])
            self.assertEqual(run2["attempts_seen"], 0)
        finally:
            with server._rt_state_lock:
                server._rt_state = saved
                server._rt_registry.reset()

    def test_turn_batch_auto_resume_resolves_pack_member_own_run(self):
        with server._rt_state_lock:
            saved = server._rt_state
        try:
            server.rt_start("m1", 10, "prov", "mod", "obj", "prompt-1")
            server.rt_start("m2", 10, "prov", "mod", "obj", "prompt-2")
            server.rt_track_attempt(4, session_id="m1")
            msg = server._rt_turn_batch_resume_message("m1")
            self.assertIsNotNone(msg)
            self.assertIn("4/10 attempts", msg)
            self.assertIn("Pick up from attempt 5", msg)
            # Aborting m1 stops its turn-batch auto-resume without affecting m2.
            server.rt_abort("m1")
            self.assertIsNone(server._rt_turn_batch_resume_message("m1"))
            self.assertIsNotNone(server._rt_turn_batch_resume_message("m2"))
        finally:
            with server._rt_state_lock:
                server._rt_state = saved
                server._rt_registry.reset()


if __name__ == "__main__":
    unittest.main()
