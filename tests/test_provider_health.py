import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import provider_health


class ProviderHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name)
        self.file_patch = patch.object(provider_health, "STATE_FILE", path / "health.json")
        self.lock_patch = patch.object(provider_health, "LOCK_FILE", path / "health.lock")
        self.file_patch.start()
        self.lock_patch.start()
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.lock_patch.stop)
        provider_health.reset()

    def test_classify_only_looks_at_error_sentinels(self):
        self.assertIsNone(provider_health.classify_response(
            "Sure — here is a story where the hacker sees auth 401 and moves on."))
        self.assertEqual("soft", provider_health.classify_response(
            "[RATELIMIT — openrouter 429: slow down]"))
        self.assertEqual("soft", provider_health.classify_response(
            "[TIMEOUT — target took >180s]"))
        self.assertEqual("hard", provider_health.classify_response(
            "[ERROR — OpenAI auth 401: invalid api key]"))
        self.assertIsNone(provider_health.classify_response(
            "[BLOCKED — classifier refused this prompt]"))
        self.assertIsNone(provider_health.classify_response(
            "[ERROR — subscription guard: session not granted the claude seat]"))

    def test_hard_failure_opens_immediately(self):
        provider_health.note_result("openrouter", "[ERROR — OpenRouter auth 402: no credit]")
        detail = provider_health.check("openrouter")
        self.assertIsNotNone(detail)
        self.assertIn("openrouter", detail)
        state = provider_health.snapshot()["openrouter"]
        self.assertEqual(1, state["trips"])
        self.assertGreater(state["open_until"], 0)

    def test_three_transient_failures_open_breaker(self):
        for _ in range(provider_health.SOFT_TRIP_AFTER - 1):
            provider_health.note_result("xai", "[RATELIMIT — xai 429]")
            self.assertIsNone(provider_health.check("xai"))
        provider_health.note_result("xai", "[RATELIMIT — xai 429]")
        self.assertIsNotNone(provider_health.check("xai"))

    def test_success_clears_state(self):
        provider_health.note_result("openai", "[ERROR — OpenAI auth 401: bad key]")
        self.assertIsNotNone(provider_health.check("openai"))
        provider_health.note_result("openai", "A normal model answer with no sentinel.")
        self.assertIsNone(provider_health.check("openai"))
        self.assertNotIn("openai", provider_health.snapshot())

    def test_error_detail_is_scrubbed(self):
        provider_health.note_result(
            "deepseek",
            "[ERROR — DeepSeek auth 401: sk-or-v1-abcdef1234567890 rejected]",
        )
        stored = provider_health.snapshot()["deepseek"]["last_error"]
        self.assertNotIn("sk-or-v1-abcdef", stored)
        self.assertIn("<redacted>", stored)

    def test_cooldown_ladder(self):
        self.assertEqual(60.0, provider_health._cooldown_for(1))
        self.assertEqual(300.0, provider_health._cooldown_for(2))
        self.assertEqual(1800.0, provider_health._cooldown_for(3))
        self.assertEqual(1800.0, provider_health._cooldown_for(9))


if __name__ == "__main__":
    unittest.main()
