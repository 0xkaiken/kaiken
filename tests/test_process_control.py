"""Orphan-agent sweep matcher — must never touch the server or user tools."""

import unittest

from process_control import _is_orphan_agent


class OrphanAgentMatcherTest(unittest.TestCase):
    def test_headless_runners_are_orphan_agents(self):
        self.assertTrue(_is_orphan_agent(
            "python.exe",
            r"python C:\work\pliny-command\opencode_runner.py --model opencode-go/x"))
        self.assertTrue(_is_orphan_agent(
            "python.exe",
            r"python C:\work\pliny-command\codex_runner.py --model gpt-6"))
        self.assertTrue(_is_orphan_agent(
            "python.exe",
            r"python C:\work\pliny-command\rt_hermes.py"))
        self.assertTrue(_is_orphan_agent(
            "opencode.exe",
            'opencode.exe --standalone run --format json --model opencode-go/x --agent build'))
        self.assertTrue(_is_orphan_agent(
            "claude.exe",
            "claude --output-format stream-json --verbose --dangerously-skip-permissions"))

    def test_never_matches_the_server_or_user_sessions(self):
        self.assertFalse(_is_orphan_agent("python.exe", r"python -X utf8 -u server.py"))
        self.assertFalse(_is_orphan_agent(
            "opencode.exe",
            r'"C:\Users\X\AppData\Local\Programs\@opencode-aidesktop\OpenCode.exe" --type=renderer'))
        self.assertFalse(_is_orphan_agent(
            "opencode.exe", "opencode.exe serve --service"))
        # Interactive Claude sessions use --input-format, not our headless shape.
        self.assertFalse(_is_orphan_agent(
            "claude.exe",
            "claude.exe --output-format stream-json --verbose --input-format stream-json"))
        # The sweep's own process-snapshot helper.
        self.assertFalse(_is_orphan_agent(
            "powershell.exe",
            'powershell -Command "Get-CimInstance Win32_Process | ConvertTo-Json"'))
        # Tool shells mentioning the markers are not runners.
        self.assertFalse(_is_orphan_agent(
            "powershell.exe",
            'powershell -Command "Select-String opencode_runner.py"'))


if __name__ == "__main__":
    unittest.main()
