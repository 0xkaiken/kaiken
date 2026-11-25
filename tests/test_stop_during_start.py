import threading
import unittest
from unittest.mock import Mock, patch

import server


class StopDuringStartTests(unittest.TestCase):
    def session(self, **options):
        session = server.Session("stop-fixture", "Harmless task", runtime="codex", **options)
        self.addCleanup(server.active_sessions.pop, session.session_id, None)
        server.active_sessions[session.session_id] = session
        return session

    def quiet(self, session):
        patches = [patch.object(session, name) for name in ("_save_log", "_save_session_record", "_initial_prompt")]
        patches.append(patch.object(server, "broadcast_sse"))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        session._initial_prompt.return_value = "Harmless task"

    def test_stop_before_spawn_prevents_the_agent_from_starting(self):
        session = self.session()
        self.quiet(session)
        session.stop("killed", force=True)
        self.assertEqual(session.status, "killed")
        self.assertNotIn(session.session_id, server.active_sessions)
        with patch.object(server, "launch_agent_process") as spawn:
            session._start_inner()
        spawn.assert_not_called()

    def test_stop_during_preparation_prevents_the_spawn(self):
        session = self.session()
        self.quiet(session)
        # The real gap: preparation is already past its first check when STOP lands.
        session._initial_prompt.side_effect = lambda: session.stop("killed", force=True) or "Harmless task"
        with patch.object(server, "launch_agent_process") as spawn:
            session._start_inner()
        spawn.assert_not_called()
        self.assertEqual(session.status, "killed")

    def test_stop_during_browser_preparation_opens_no_browser_and_frees_the_slot(self):
        session = self.session(computer_use=True)
        self.quiet(session)
        session.stop("killed", force=True)
        session._stop_requested = False
        real_acquire = server._cu_acquire

        def acquire_then_stop(session_id):
            acquired = real_acquire(session_id)
            session._stop_requested = True
            return acquired

        with patch.object(server, "_cu_acquire", side_effect=acquire_then_stop), \
                patch.object(server, "launch_pw_browser") as browser, \
                patch.object(server, "launch_agent_process") as spawn, \
                patch("urllib.request.urlopen", side_effect=OSError), \
                patch.object(server.time, "sleep"):
            session._start_inner()
        browser.assert_not_called()
        spawn.assert_not_called()
        self.assertNotEqual(server._cu_active_session_id, session.session_id)

    def test_stop_waits_for_a_spawn_in_progress_then_kills_it(self):
        session = self.session()
        self.quiet(session)
        spawned, release = threading.Event(), threading.Event()
        process = Mock()
        process.poll.return_value = None

        def slow_spawn(*_args, **_options):
            spawned.set()
            release.wait(5)
            return process

        stopper = threading.Thread(target=lambda: (spawned.wait(5), session.stop("killed", force=True)))
        with patch.object(server, "launch_agent_process", side_effect=slow_spawn), \
                patch.object(server, "stop_process_tree") as kill, \
                patch.object(session, "_job_track"), \
                patch.object(session, "_read_output"), \
                patch.object(session, "_idle_watchdog"):
            stopper.start()
            starter = threading.Thread(target=session._start_inner)
            starter.start()
            spawned.wait(5)
            release.set()
            starter.join(5)
            stopper.join(5)
        kill.assert_called_once_with(process, force=True)
        self.assertEqual(session.status, "killed")

    def test_status_is_terminal_before_the_kill_so_queued_messages_do_not_resume(self):
        session = self.session()
        self.quiet(session)
        session.process = Mock()
        session.process.poll.return_value = None
        seen = []
        with patch.object(server, "stop_process_tree", side_effect=lambda *_a, **_k: seen.append(session.status)):
            session.stop("killed", force=True)
        self.assertEqual(seen, ["killed"])

    def test_job_cleanup_cannot_turn_an_operator_stop_into_a_crash(self):
        session = self.session()
        self.quiet(session)
        session.process = Mock()
        session.process.poll.return_value = None
        seen = []

        def close_job():
            # Closing a Windows job can wake the reader before stop() returns.
            seen.append(session.status)
            session.process.poll.return_value = 4294967295
            if session.status == "running":
                session.status = "error"

        with patch.object(session, "_close_job", side_effect=close_job):
            session.stop("killed", force=True)
        self.assertEqual(seen, ["killed"])
        self.assertEqual(session.status, "killed")

    def test_no_relaunch_path_runs_after_stop(self):
        session = self.session()
        self.quiet(session)
        session.stop("killed", force=True)
        with patch.object(server, "launch_agent_process") as spawn, patch.object(session, "start") as start:
            session._send_continuation(["keep going"])
            session._fallback_relaunch()
            session._fallback_relaunch_hermes()
        spawn.assert_not_called()
        start.assert_not_called()
        self.assertEqual(session.status, "killed")


if __name__ == "__main__":
    unittest.main()
