import unittest

import gauntlet
import provider_health
import server
import transport_errors


SENTINELS = (
    "[BLOCKED — classifier refused]",
    "[RATELIMIT — provider 429]",
    "[RATE LIMIT — provider 429]",
    "[TIMEOUT — target took 300s]",
    "[NETWORK — DNS failure]",
    "[ERROR — auth 401: invalid api key]",
    "[ERROR — unknown upstream failure]",
    "[OpenCode Go error: timed out after 300s]",
)


class TransportClassifierTests(unittest.TestCase):
    def test_every_module_agrees_on_transport_errors(self):
        for text in SENTINELS:
            self.assertTrue(transport_errors.is_transport_error(text), text)
            self.assertTrue(server.is_transport_error(text), text)
            self.assertIsNotNone(gauntlet.classify_upstream_error(text), text)

    def test_model_answers_that_quote_a_sentinel_are_not_errors(self):
        text = ("Sure, here is a story where the operator sees "
                "[ERROR — auth 401] in a log and moves on.")
        self.assertFalse(transport_errors.is_transport_error(text))
        self.assertFalse(server.is_transport_error(text))
        self.assertIsNone(gauntlet.classify_upstream_error(text))

    def test_health_classification_is_shared(self):
        for text in SENTINELS:
            self.assertEqual(transport_errors.health_class(text),
                             provider_health.classify_response(text), text)

    def test_unknown_error_is_soft_and_never_resets_the_breaker(self):
        # The old provider_health said None here, so the next success reset
        # the failure the previous call had just recorded.
        self.assertEqual("soft", provider_health.classify_response(
            "[ERROR — some new upstream shape]"))

    def test_seat_denial_and_classifier_block_are_not_provider_failures(self):
        self.assertIsNone(provider_health.classify_response(
            "[ERROR — subscription guard: session not granted the claude seat]"))
        self.assertIsNone(provider_health.classify_response(
            "[BLOCKED — classifier refused this prompt]"))

    def test_gauntlet_empty_body_semantics_are_preserved(self):
        self.assertEqual("empty_stop",
                         gauntlet.classify_upstream_error("", "stop"))
        self.assertEqual("empty_other",
                         gauntlet.classify_upstream_error("", "length"))
        self.assertEqual("error", gauntlet.classify_upstream_error("", ""))

    def test_redaction_removes_credential_shapes(self):
        text = ("key sk-ant-verysecret123456 and Bearer abcdefghijklmnop "
                "api_key=supersecretvalue")
        redacted = transport_errors.redact_secrets(text)
        for secret in ("sk-ant-verysecret123456", "abcdefghijklmnop",
                       "supersecretvalue"):
            self.assertNotIn(secret, redacted)


if __name__ == "__main__":
    unittest.main()
