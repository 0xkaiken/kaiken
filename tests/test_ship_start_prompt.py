import json
import unittest
from unittest.mock import patch

import server


class ShipStartPromptTests(unittest.TestCase):
    def test_prompt_is_read_and_escaped_for_a_js_string(self):
        if not (server.BASE_DIR / "Prompts" / "For DS.txt").exists():
            self.skipTest("local Ship starter prompt is not tracked")
        escaped = server._ship_start_prompt_js()
        self.assertTrue(escaped, "Prompts/For DS.txt must be readable")
        self.assertNotIn("\n", escaped, "raw newlines would break the JS string")
        self.assertNotIn("</", escaped, "a closing tag must never escape the script")
        decoded = json.loads('"' + escaped + '"')
        self.assertIn("MAHI-V3-DEEPSEEK", decoded)
        self.assertIn("I am ready, Mr. Mahi", decoded)

    def test_injects_only_for_opencode_deepseek(self):
        with patch.object(server, "_ship_start_prompt_text", return_value="STARTER"):
            self.assertEqual(
                server._inject_ds_start_prompt(
                    "task", "opencode", "opencode-go/deepseek-v4.1-flash"),
                "STARTER\n\ntask")
            self.assertEqual(
                server._inject_ds_start_prompt(
                    "task", "opencode", "opencode-go/glm-5.3-flash"),
                "task")
            self.assertEqual(
                server._inject_ds_start_prompt("task", "claude", "deepseek-x"),
                "task")

    def test_injection_does_not_duplicate_the_ship_prompt(self):
        with patch.object(server, "_ship_start_prompt_text", return_value="STARTER"):
            self.assertEqual(
                server._inject_ds_start_prompt(
                    "STARTER\n\nSHIP MODE", "opencode", "x/deepseek"),
                "STARTER\n\nSHIP MODE")

    def test_missing_file_is_a_noop(self):
        with patch.object(server, "_ship_start_prompt_text", return_value=""):
            self.assertEqual(
                server._inject_ds_start_prompt("task", "opencode", "x/deepseek"),
                "task")


if __name__ == "__main__":
    unittest.main()
