import json
import unittest
from unittest.mock import Mock, patch

import server


TARGET = {"provider": "opencode", "model": "opencode-go/glm-5.3-flash"}


class CustomPromptTests(unittest.TestCase):
    def test_custom_instruction_bypasses_personality_and_shared_context(self):
        session = server.Session("custom-test", "  Only summarize these results.\n", runtime="codex", custom_prompt=True)
        with patch.object(server, "build_memory_context", side_effect=AssertionError("shared memory")), \
                patch.object(server, "build_comms_context", side_effect=AssertionError("shared comms")):
            self.assertEqual(session._initial_prompt(), session.prompt)
        self.assertIsNone(session.agent)
        self.assertIsNone(session.chaos_flavor)

    def test_history_matches_target_version_and_excludes_old_instructions(self):
        rows = [
            {"target": "opencode/opencode/glm-5.3-flash", "response_preview": "Previous target refused.",
             "prompt_preview": "OLD TASK MUST NOT BE INHERITED", "outcome": "refused"},
            {"target": "opencode/opencode-go/glm-5.3-flash", "response_preview": "Second target observation."},
            {"target": "opencode/opencode-go/glm-5.3-pro", "response_preview": "OTHER VERSION"},
            {"target": "openai/glm-5.3-flash", "response_preview": "OTHER PROVIDER"},
            {"target": "opencode/opencode-go/glm-5.3-flash", "session_id": "custom-test",
             "response_preview": "CURRENT SESSION"},
        ]
        session = server.Session("custom-test", "Inspect this target.", runtime="codex", custom_prompt=True, target=TARGET)
        with patch.object(server, "_load_rt_results", return_value=rows):
            prompt = session._initial_prompt()
        self.assertTrue(prompt.startswith(session.prompt))
        self.assertIn("Previous target refused.", prompt)
        self.assertIn("Second target observation.", prompt)
        for excluded in ("OLD TASK", "OTHER VERSION", "OTHER PROVIDER", "CURRENT SESSION", "BLOOD AGENT SESSION"):
            self.assertNotIn(excluded, prompt)
        self.assertIn("untrusted data", prompt)

    def test_no_matching_history_adds_no_other_model_responses(self):
        with patch.object(server, "_load_rt_results", return_value=[{
                "target": "openai/another-model", "response_preview": "Unrelated response"}]):
            context = server.custom_target_context(TARGET, "custom-test")
        self.assertIn('"prior_target_observations": []', context)
        self.assertNotIn("Unrelated response", context)

    def test_preset_still_receives_existing_shared_context(self):
        session = server.Session("preset-test", "Preset task", runtime="codex")
        with patch.object(server, "build_memory_context", return_value="MEMORY"), \
                patch.object(server, "build_watchtower_briefing", return_value="WATCHTOWER"), \
                patch.object(server, "build_comms_context", return_value="COMMS"), \
                patch.object(server, "build_capabilities_context", return_value="CAPABILITIES"), \
                patch.object(server, "build_naming_context", return_value="NAMING"), \
                patch.object(server, "skills_mod", None):
            prompt = session._initial_prompt()
        for expected in (session.DRAGON_SOUL, "MEMORY", "COMMS", "WATCHTOWER", "NAMING", "Preset task"):
            self.assertIn(expected, prompt)

    def test_actual_custom_runner_input_contains_only_the_custom_instruction(self):
        session = server.Session("custom-test", "Only do this task.\n", runtime="codex", custom_prompt=True)
        with patch.object(session, "_codex_command", return_value=["runner"]), \
                patch.object(server, "launch_agent_process") as launch, \
                patch.object(session, "_job_track"), patch.object(session, "_save_session_record"), \
                patch.object(server.threading, "Thread"):
            session._start_inner()
        self.assertEqual(launch.call_args.kwargs["input_text"], session.prompt)

    def test_custom_mode_and_target_survive_save_and_resume(self):
        session = server.Session("custom-test", "My task", runtime="codex", custom_prompt=True, target=TARGET)
        session.claude_session_id = "conversation-test"
        session.status = "completed"
        with patch.object(server, "load_json", return_value=[]), patch.object(server, "save_json") as save:
            session._save_session_record()
        record = save.call_args.args[1][0]
        self.assertTrue(record["custom_prompt"])
        self.assertEqual(record["target"], TARGET)
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = Mock(return_value=json.dumps({"session_id": "custom-test", "message": "Continue"}))
        handler._send_json = Mock()
        with patch.object(server, "load_json", return_value=[record]), \
                patch.object(server, "active_sessions", {}), \
                patch.object(server.Session, "send_message"), \
                patch.object(server, "broadcast_sse"), patch.object(server, "post_comms_message"):
            handler._api_session_message()
            restored = server.active_sessions["custom-test"]
            self.assertTrue(restored.custom_prompt)
            self.assertEqual(restored.target, TARGET)
            self.assertIsNone(restored.agent)

    def test_api_preserves_custom_text_and_disables_automatic_new_tasks(self):
        for custom in (True, False):
            with self.subTest(custom=custom):
                handler = server.Handler.__new__(server.Handler)
                raw = "  Summarize the response behavior.\n"
                handler._read_body = Mock(return_value=json.dumps({
                    "prompt": raw, "custom_prompt": custom, "runtime": "codex", "redteam": TARGET}))
                handler._send_json = Mock()
                with patch.object(server, "_require_auth", return_value=True), \
                        patch.object(server, "launch_session", return_value={"id": "fixture"}) as launch, \
                        patch.object(server, "rt_start") as register, \
                        patch.object(server, "audit_log"), patch.object(server, "_audit_request", return_value={}):
                    handler._api_launch()
                self.assertEqual(launch.call_args.args[0], raw if custom else raw.strip())
                self.assertEqual(launch.call_args.kwargs["custom_prompt"], custom)
                self.assertEqual(launch.call_args.kwargs["target"], dict(TARGET, max_attempts=10))
                self.assertEqual(register.call_args.kwargs["auto_continue"], not custom)

    def test_custom_completion_does_not_continue_an_engaged_session(self):
        # auto_continue=False gates the continuation branch only. A session that
        # engaged (>=2 attempts) but is incomplete stops here; a session that
        # never engaged is retried instead, which is not a continuation.
        state = {
            "active": True, "session_id": "fixture", "auto_continue": False,
            "aborted": False, "attempts_seen": 3, "max_attempts": 10,
            "retries_used": 0, "max_retries": 5, "original_prompt": "",
            "target_budget": None, "session_config": {}, "provider": "",
            "model": "", "objective": "", "cracked": False,
        }
        with patch.object(server, "_rt_state", state), patch.object(server, "launch_session") as launch:
            server.rt_session_ended("fixture")
        self.assertFalse(state["active"])
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
