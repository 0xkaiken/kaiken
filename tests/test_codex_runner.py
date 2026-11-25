import unittest
from unittest.mock import patch

from codex_runner import DashboardEvents, codex_command


class CodexRunnerTests(unittest.TestCase):
    def test_thread_tools_and_answer_reach_dashboard(self):
        stream = DashboardEvents()
        init = stream.convert({"type": "thread.started", "thread_id": "thread-123"})[0]
        self.assertEqual(init["session_id"], "thread-123")
        tool = stream.convert({"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution", "command": "echo hello"}})[0]
        self.assertEqual(tool["message"]["content"][0]["input"]["command"], "echo hello")
        result = stream.convert({"type": "item.completed", "item": {
            "id": "cmd-1", "type": "command_execution", "aggregated_output": "hello"}})[0]
        self.assertEqual(result["tool_use_id"], "cmd-1")
        answer = stream.convert({"type": "item.completed", "item": {
            "type": "agent_message", "text": "Türkçe yanıt"}})[0]
        self.assertEqual(answer["message"]["content"][0]["text"], "Türkçe yanıt")
        done = stream.convert({"type": "turn.completed", "usage": {}})[0]
        self.assertEqual(done["result"], "Türkçe yanıt")
        self.assertFalse(done["is_error"])

    def test_failed_turn_is_not_reported_as_success(self):
        stream = DashboardEvents()
        result = stream.convert({"type": "turn.failed", "error": {"message": "Quota exceeded"}})[0]
        self.assertTrue(stream.failed)
        self.assertTrue(result["is_error"])
        self.assertEqual(result["result"], "Quota exceeded")

    @patch("codex_runner.shutil.which", return_value="codex.exe")
    def test_subscription_auth_and_explicit_resume(self, _which):
        command = codex_command(resume="specific-thread")
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('model_provider="openai"', command)
        self.assertEqual(command[command.index("resume") + 1], "specific-thread")
        self.assertEqual(command[-1], "-")
        self.assertNotIn("--last", command)


if __name__ == "__main__":
    unittest.main()
