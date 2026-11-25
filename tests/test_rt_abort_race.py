"""Race regression: rt_abort() landing while rt_session_ended() is planning
must not produce a new agent. The abort is injected at the exact linearization
points where a real concurrent STOP can land — inside the continuation's
learnings build, inside the retry's kwargs resolution, and inside the Hermes
spawn preparation.
"""
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import server


class AbortRaceTests(unittest.TestCase):
    def setUp(self):
        with server._rt_state_lock:
            self._saved = server._rt_state
        self.addCleanup(self._restore)

    def _restore(self):
        with server._rt_state_lock:
            server._rt_state = self._saved
            server._rt_registry.reset()

    def test_abort_during_continuation_prevents_relaunch(self):
        server.rt_start("s1", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        server.rt_track_attempt(5, session_id="s1")

        def inject_abort(*a, **kw):
            server.rt_abort("s1")  # the concurrent STOP lands mid-planning
            return ""

        with patch.object(server, "launch_session") as launched, \
                patch.object(server, "broadcast_sse"), \
                patch.object(server, "build_redteam_learnings", side_effect=inject_abort):
            server.rt_session_ended("s1")
        launched.assert_not_called()

    def test_abort_during_retry_prevents_relaunch(self):
        server.rt_start("s2", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        # zero attempts -> stalled -> retry branch

        def inject_abort(*a, **kw):
            server.rt_abort("s2")
            return {"runtime": "opencode"}

        with patch.object(server, "launch_session") as launched, \
                patch.object(server, "broadcast_sse"), \
                patch.object(server, "retry_launch_kwargs", side_effect=inject_abort):
            server.rt_session_ended("s2")
        launched.assert_not_called()

    def test_abort_during_hermes_prevents_spawn(self):
        server.rt_start("s3", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        with server._rt_state_lock:
            run = server._rt_registry.for_session("s3")
            run["retries_used"] = run["max_retries"]  # first exhaustion

        def inject_abort(*a, **kw):
            server.rt_abort("s3")
            return {}

        with patch.dict(server.os.environ, {"OPENROUTER_API_KEY": "fixture"}), \
                patch.object(server, "broadcast_sse"), \
                patch.object(server.subprocess, "Popen") as popen, \
                patch.object(server, "process_group_options", side_effect=inject_abort):
            server.rt_session_ended("s3")
        popen.assert_not_called()

    def test_relaunch_still_happens_without_abort(self):
        # Control: the same continuation path must launch when nobody aborts.
        server.rt_start("s4", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        server.rt_track_attempt(5, session_id="s4")
        with patch.object(server, "launch_session", return_value={"id": "s4-cont"}) as launched, \
                patch.object(server, "broadcast_sse"), \
                patch.object(server, "build_redteam_learnings", return_value=""):
            server.rt_session_ended("s4")
        launched.assert_called_once()

    def test_abort_in_flight_during_continuation_launch_kills_new_session(self):
        # Abort lands while launch_session is in flight (before atomic commit).
        # The freshly launched session must be killed and never rekeyed or broadcast.
        server.rt_start("s5", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        server.rt_track_attempt(5, session_id="s5")
        stopped = []

        class FakeSess:
            def stop(self, *a, **kw):
                stopped.append("s5-cont")

        def launch_then_abort(*a, **kw):
            with server.active_sessions_lock:
                server.active_sessions["s5-cont"] = FakeSess()
            server.rt_abort("s5")
            return {"id": "s5-cont"}

        try:
            with patch.object(server, "launch_session", side_effect=launch_then_abort), \
                    patch.object(server, "broadcast_sse") as sse, \
                    patch.object(server, "build_redteam_learnings", return_value=""):
                server.rt_session_ended("s5")
        finally:
            with server.active_sessions_lock:
                server.active_sessions.pop("s5-cont", None)
        self.assertEqual(stopped, ["s5-cont"])
        self.assertIsNone(server._rt_registry.for_session("s5-cont"))
        continuation_events = [
            c for c in sse.call_args_list
            if c.args and c.args[0] == "redteam_chat" and c.args[1].get("type") == "continuation"
        ]
        self.assertEqual(continuation_events, [])

    def test_abort_in_flight_during_retry_launch_kills_new_session(self):
        server.rt_start("s6", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        stopped = []

        class FakeSess:
            def stop(self, *a, **kw):
                stopped.append("s6-retry")

        def launch_then_abort(*a, **kw):
            with server.active_sessions_lock:
                server.active_sessions["s6-retry"] = FakeSess()
            server.rt_abort("s6")
            return {"id": "s6-retry"}

        try:
            with patch.object(server, "launch_session", side_effect=launch_then_abort), \
                    patch.object(server, "broadcast_sse"):
                server.rt_session_ended("s6")
        finally:
            with server.active_sessions_lock:
                server.active_sessions.pop("s6-retry", None)
        self.assertEqual(stopped, ["s6-retry"])
        self.assertIsNone(server._rt_registry.for_session("s6-retry"))

    def test_abort_in_flight_during_hermes_popen_kills_session(self):
        server.rt_start("s7", 10, "p", "m", "o", "prompt",
                        session_config={"runtime": "opencode"})
        with server._rt_state_lock:
            run = server._rt_registry.for_session("s7")
            run["retries_used"] = run["max_retries"]
        stopped = []

        def fake_popen(*a, **kw):
            server.rt_abort("s7")
            proc = Mock()
            proc.pid = 99999
            proc.poll.return_value = None
            return proc

        with patch.dict(server.os.environ, {"OPENROUTER_API_KEY": "fixture"}), \
                patch.object(server, "broadcast_sse") as sse, \
                patch.object(server.subprocess, "Popen", side_effect=fake_popen), \
                patch.object(server.Session, "_job_track"), \
                patch.object(server.Session, "_read_output"), \
                patch.object(server.Session, "_idle_watchdog"), \
                patch.object(server.Session, "stop",
                             side_effect=lambda *a, **kw: stopped.append("hermes")):
            server.rt_session_ended("s7")
        self.assertEqual(stopped, ["hermes"])
        continuation_events = [
            c for c in sse.call_args_list
            if c.args and c.args[0] == "redteam_chat" and c.args[1].get("type") == "continuation"
        ]
        self.assertEqual(continuation_events, [])


class StopAllPackTests(unittest.TestCase):
    """P1: parameterless rt_abort() must cancel EVERY active free-form pack
    run, not only whichever member started last."""

    def setUp(self):
        with server._rt_state_lock:
            self._saved = server._rt_state
        self.addCleanup(self._restore)

    def _restore(self):
        with server._rt_state_lock:
            server._rt_state = self._saved
            server._rt_registry.reset()

    def test_stop_all_aborts_every_active_pack_run(self):
        server.rt_start("pa", 10, "p", "m", "o", "x")
        server.rt_start("pb", 10, "p", "m", "o", "y")
        server.rt_start("pc", 10, "p", "m", "o", "z")
        stopped = []

        class FakeSession:
            def stop(self, *a, **kw):
                stopped.append(self.sid)

        fake_a, fake_b, fake_c = FakeSession(), FakeSession(), FakeSession()
        fake_a.sid, fake_b.sid, fake_c.sid = "pa", "pb", "pc"
        with server.active_sessions_lock:
            server.active_sessions["pa"] = fake_a
            server.active_sessions["pb"] = fake_b
            server.active_sessions["pc"] = fake_c
        try:
            server.rt_abort()
        finally:
            with server.active_sessions_lock:
                for sid in ("pa", "pb", "pc"):
                    server.active_sessions.pop(sid, None)
        for sid in ("pa", "pb", "pc"):
            self.assertIn(sid, stopped)
        # Compat slot is the LAST started run — before the fix it was the
        # only one cancelled.
        self.assertFalse(server._rt_registry.for_session("pa") is None)
        self.assertTrue(server._rt_registry.for_session("pa")["aborted"])
        self.assertTrue(server._rt_registry.for_session("pb")["aborted"])

    def test_explicit_abort_still_targets_only_that_run(self):
        server.rt_start("qa", 10, "p", "m", "o", "x")
        server.rt_start("qb", 10, "p", "m", "o", "y")
        server.rt_abort("qa")
        self.assertTrue(server._rt_registry.for_session("qa")["aborted"])
        self.assertFalse(server._rt_registry.for_session("qb")["aborted"])

    def test_abort_unrelated_or_rekeyed_session_leaves_active_runs_alone(self):
        server.rt_start("ra-old", 10, "p", "m", "o", "x")
        server.rt_start("rb-live", 10, "p", "m", "o", "y")
        self.assertTrue(server._rt_replace_session(
            server._rt_registry.for_session("ra-old"), "ra-old", "ra-new"))
        # Stopping an unrelated Command session, empty id, or pre-continuation
        # sid must never fall back to _rt_state and abort rb-live.
        server.rt_abort("unrelated-command-session")
        server.rt_abort("")
        server.rt_abort("ra-old")
        self.assertTrue(server._rt_registry.for_session("ra-new")["active"])
        self.assertFalse(server._rt_registry.for_session("ra-new")["aborted"])
        self.assertTrue(server._rt_registry.for_session("rb-live")["active"])
        self.assertFalse(server._rt_registry.for_session("rb-live")["aborted"])

    def test_concurrent_rt_start_and_active_runs_are_lock_safe(self):
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        errors = []
        stop = threading.Event()
        try:
            for i in range(200):
                server.rt_start(f"pre-{i}", 10, "p", "m", "o", "x")

            def writer():
                for i in range(400):
                    if stop.is_set():
                        return
                    sid = f"conc-{i}"
                    server.rt_start(sid, 10, "p", "m", "o", "x")
                    with server._rt_state_lock:
                        server.Handler._rt_last_prompt[(sid, 1)] = "probe"

            def reader():
                for i in range(200):
                    if stop.is_set():
                        return
                    try:
                        with server._rt_state_lock:
                            _ = server._rt_registry.active_runs()
                        server.rt_forget_pending_prompts(f"conc-{i}")
                    except RuntimeError as exc:
                        errors.append(str(exc))
                        stop.set()
                        return

            t1 = threading.Thread(target=writer)
            t2 = threading.Thread(target=reader)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            stop.set()
            t2.join(timeout=5)
        finally:
            sys.setswitchinterval(old_interval)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
