import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import server


class PendingTargetAttemptsTests(unittest.TestCase):
    def test_operator_message_waits_in_queue_without_killing_background_work(self):
        session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
        session.waiting_for_target_responses = True
        session.claude_session_id = "fixture-conversation"
        session.process = Mock()
        session.process.poll.return_value = 0
        with patch.object(server, "broadcast_sse"), patch.object(server.threading, "Thread") as thread:
            session.send_message("Additional context")
        self.assertEqual(session.message_queue, ["Additional context"])
        thread.assert_not_called()

    def test_manual_continuation_does_not_rewait_old_unanswered_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = Path(tmp) / "fixture.jsonl"
            chat.write_text(json.dumps({"type": "prompt", "attempt": 21}) + "\n", encoding="utf-8")
            session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)):
                session._capture_target_chat_offset()
                self.assertEqual(server.pending_target_attempts("fixture"), [21])
                self.assertTrue(session._wait_for_target_responses(timeout=0))

    def test_background_wait_is_not_reported_as_a_zombie(self):
        session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
        session.waiting_for_target_responses = True
        session.last_output_time = 0
        session.process = Mock(returncode=0)
        session.process.poll.return_value = 0
        active = {"fixture": session}
        watchdog = server.fixer_mod.Watchdog()
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch.object(server, "active_sessions", active), patch.object(server, "scheduler_stop_event", stop):
            self.assertEqual(watchdog._detect_stuck_sessions(), [])
            self.assertEqual(watchdog._detect_orphan_processes(), [])
            server.session_reaper_loop()
        self.assertIn("fixture", active)
        self.assertEqual(session.status, "running")

    def test_wait_is_bounded_and_obeys_stop(self):
        session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
        with patch.object(server, "pending_target_attempts", return_value=[1]):
            self.assertFalse(session._wait_for_target_responses(timeout=0))
            session.status = "stopped"
            self.assertFalse(session._wait_for_target_responses(timeout=600))

    def test_stop_closes_containment_while_waiting_after_root_exit(self):
        session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
        session.waiting_for_target_responses = True
        session.process = Mock()
        session.process.poll.return_value = 0
        with patch.object(session, "_close_job") as cleanup, patch.object(session, "_save_log"), \
                patch.object(session, "_save_session_record"), patch.object(server, "broadcast_sse"), \
                patch.object(server, "active_sessions", {}):
            session.stop("stopped")
        cleanup.assert_called_once()
        self.assertEqual(session.status, "stopped")

    def test_final_analysis_cannot_start_an_unbounded_request_chain(self):
        session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
        session._target_completion_resumes = 1
        session.process = Mock()
        session.process.stdout = iter([])
        session.process.wait.return_value = 0
        with patch.object(server, "pending_target_attempts", return_value=[2]), \
                patch.object(session, "_close_job"), patch.object(session, "_wait_for_target_responses") as wait, \
                patch.object(session, "_send_continuation") as resume, \
                patch.object(session, "_save_log"), patch.object(session, "_save_session_record"), \
                patch.object(server, "broadcast_sse"), patch.object(server, "active_sessions", {}), \
                patch.object(server, "_load_experiment", return_value=None), patch.object(server, "lair_agents", {}):
            session._read_output()
        wait.assert_not_called()
        resume.assert_not_called()
        self.assertEqual(session.status, "error")

    @unittest.skipUnless(os.name == "nt", "Windows process containment")
    def test_background_response_survives_root_exit_and_is_collected_before_analysis(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş background ") as tmp:
            chat = Path(tmp) / "fixture.jsonl"
            chat.write_text(json.dumps({"type": "prompt", "attempt": 1}) + "\n", encoding="utf-8")
            child = ("import json,sys,time; time.sleep(0.3); "
                     "f=open(sys.argv[1],'a',encoding='utf-8'); "
                     "f.write(json.dumps({'type':'response','attempt':1,'text':'Harmless fixture reply'})+'\\n'); f.close()")
            parent = ("import subprocess,sys; sys.stdin.readline(); "
                      "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
                      "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)")
            process = subprocess.Popen([sys.executable, "-c", parent, child, str(chat)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", **server.process_group_options())
            session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
            session.process = process
            session.claude_session_id = "fixture-conversation"
            session._job_track(process)
            try:
                process.stdin.write("go\n")
                process.stdin.close()
                with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)), \
                        patch.object(session, "_send_continuation") as resume, \
                        patch.object(session, "_save_log"), patch.object(session, "_save_session_record"), \
                        patch.object(server, "broadcast_sse"), patch.object(server, "active_sessions", {}), \
                        patch.object(server, "_load_experiment", return_value=None), \
                        patch.object(server, "lair_agents", {}), patch.object(server.threading, "Thread"):
                    session._read_output()
                self.assertIn("Harmless fixture reply", chat.read_text(encoding="utf-8"))
                resume.assert_called_once()
                self.assertEqual(resume.call_args.kwargs, {"target_completion": True})
            finally:
                session._close_job()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdout.close()

    def test_only_unanswered_requests_from_this_session_are_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = [{"type": "prompt", "attempt": 21}, {"type": "typing", "attempt": 21},
                      {"type": "prompt", "attempt": 22}, {"type": "response", "attempt": 22}]
            (root / "fixture.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            (root / "other.jsonl").write_text(json.dumps({"type": "prompt", "attempt": 99}), encoding="utf-8")
            with patch.object(server, "REDTEAM_CHATS_DIR", root):
                self.assertEqual(server.pending_target_attempts("fixture"), [21])
                self.assertEqual(server.pending_target_attempts("missing"), [])

    def test_root_exit_with_pending_requests_is_not_successful_completion(self):
        for pending, expected in [([21, 22], "error"), ([], "completed")]:
            with self.subTest(pending=pending):
                session = server.Session("fixture", "Harmless task", runtime="codex", custom_prompt=True)
                session.process = Mock()
                session.process.stdout = iter([])
                session.process.wait.return_value = 0
                with patch.object(server, "pending_target_attempts", return_value=pending), \
                        patch.object(session, "_wait_for_target_responses", return_value=False), \
                        patch.object(session, "_close_job") as cleanup, \
                        patch.object(session, "_save_log"), patch.object(session, "_save_session_record"), \
                        patch.object(server, "broadcast_sse"), patch.object(server, "active_sessions", {}), \
                        patch.object(server, "_load_experiment", return_value=None), \
                        patch.object(server, "lair_agents", {}), patch.object(server.threading, "Thread"):
                    session._read_output()
                cleanup.assert_called_once()
                self.assertEqual(session.status, expected)
                if pending:
                    self.assertTrue(any("Task incomplete" in line for line in session.log_lines))


if __name__ == "__main__":
    unittest.main()
