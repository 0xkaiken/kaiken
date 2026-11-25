"""Account usage boundary — regression tests.

Rule under test (AGENTS.md, CLAUDE.md): an agent or a test may not, on its own
initiative, use a user's specific Anthropic or GPT subscription accounts.

No network calls, no provider keys, no agent is launched.
"""

import json
import os
import threading
import unittest
from unittest.mock import patch

import account_guard
import agent_permissions
import server


class Handler:
    """Minimal request handler stand-in for the auth helpers."""

    def __init__(self, headers):
        self.headers = headers
        self.path = "/api/test"

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status


class OriginTests(unittest.TestCase):
    def tearDown(self):
        account_guard.clear_origin()

    def test_default_is_operator(self):
        account_guard.clear_origin()
        self.assertEqual(account_guard.current_origin(), account_guard.ORIGIN_OPERATOR)
        self.assertTrue(account_guard.current_authorization())

    def test_agent_origin_cannot_spend_a_seat(self):
        with account_guard.agent_request():
            decision = account_guard.decide("claude")
            self.assertFalse(decision.allowed)
            self.assertIn("agent", decision.reason)
            with self.assertRaises(account_guard.SubscriptionDenied):
                account_guard.require("claude")

    def test_test_origin_cannot_spend_a_seat(self):
        with account_guard.test_request():
            self.assertFalse(account_guard.decide("codex").allowed)

    def test_automation_origin_can_spend_a_seat(self):
        with account_guard.automation_request():
            self.assertTrue(account_guard.decide("claude").allowed)
            self.assertTrue(account_guard.decide("codex").allowed)

    def test_agent_origin_stays_denied_even_when_marked_authorized(self):
        # Authorization alone is not enough: the origin has to be an operator
        # request. This keeps a future call site from "fixing" a denial by
        # flipping the boolean.
        with account_guard.origin_context(account_guard.ORIGIN_AGENT, authorized=True):
            self.assertFalse(account_guard.decide("claude").allowed)

    def test_operator_without_authorization_is_denied(self):
        with account_guard.origin_context(account_guard.ORIGIN_OPERATOR, authorized=False):
            self.assertFalse(account_guard.decide("claude").allowed)

    def test_non_subscription_runtime_is_always_allowed(self):
        with account_guard.agent_request():
            self.assertTrue(account_guard.decide("opencode").allowed)

    def test_origin_restored_after_context(self):
        with account_guard.agent_request():
            pass
        self.assertEqual(account_guard.current_origin(), account_guard.ORIGIN_OPERATOR)

    def test_thread_local_isolation(self):
        seen = {}

        def worker():
            seen["origin"] = account_guard.current_origin()

        account_guard.set_origin("agent", authorized=False)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(seen["origin"], account_guard.ORIGIN_OPERATOR)


class AuthStampTests(unittest.TestCase):
    def tearDown(self):
        account_guard.clear_origin()

    def test_operator_token_stamps_operator(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertEqual(
                    server._auth_kind(Handler({"Authorization": "Bearer operator-fixture"})),
                    "operator",
                )

    def test_agent_token_stamps_agent_and_blocks_the_seat(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({"Authorization": "Bearer agent-fixture"})
                self.assertEqual(server._auth_kind(handler), "agent")
                self.assertTrue(server._require_api_access(handler))
                self.assertEqual(account_guard.current_origin(), "agent")
                self.assertFalse(account_guard.decide("claude").allowed)

    def test_wrong_token_yields_no_kind(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertIsNone(
                    server._auth_kind(Handler({"Authorization": "Bearer wrong"}))
                )


class SessionFlagTests(unittest.TestCase):
    def test_claude_session_gets_the_anthropic_seat(self):
        flags = account_guard.session_flags("claude", True, "operator")
        self.assertEqual(flags[account_guard.SEAT_ENV], "claude")
        self.assertEqual(flags[account_guard.AUTHORIZED_ENV], "1")

    def test_unauthorized_session_is_marked_zero(self):
        flags = account_guard.session_flags("claude", False, "agent")
        self.assertEqual(flags[account_guard.AUTHORIZED_ENV], "0")

    def test_opencode_has_no_seat(self):
        flags = account_guard.session_flags("opencode", True, "operator")
        self.assertEqual(flags[account_guard.SEAT_ENV], "")
        self.assertEqual(flags[account_guard.AUTHORIZED_ENV], "0")

    def test_runner_denies_seat_mismatch(self):
        with patch.dict(os.environ, {
            account_guard.SEAT_ENV: "claude",
            account_guard.AUTHORIZED_ENV: "1",
        }, clear=False):
            self.assertIsNone(account_guard.runner_denial("claude"))
            self.assertIn("different account", account_guard.runner_denial("codex"))
            with self.assertRaises(account_guard.SubscriptionDenied):
                account_guard.require_runner("codex")

    def test_runner_denies_unauthorized_session(self):
        with patch.dict(os.environ, {
            account_guard.SEAT_ENV: "codex",
            account_guard.AUTHORIZED_ENV: "0",
        }, clear=False):
            self.assertIn("not authorized", account_guard.runner_denial("codex"))

    def test_runner_allows_standalone_invocation(self):
        with patch.dict(os.environ, {
            account_guard.SEAT_ENV: "",
            account_guard.AUTHORIZED_ENV: "",
        }, clear=False):
            self.assertIsNone(account_guard.runner_denial("claude"))
            self.assertIsNone(account_guard.runner_denial("codex"))


class BrowserGuardTests(unittest.TestCase):
    def test_consumer_domains_are_blocked(self):
        for url in (
            "https://claude.ai/new",
            "https://chatgpt.com/",
            "https://chat.openai.com/chat",
            "claude.ai",
        ):
            self.assertIsNotNone(account_guard.browser_denial(url, allowed=False), url)
            self.assertIn("subscription-account site", account_guard.browser_denial(url, allowed=False))

    def test_ordinary_domains_pass(self):
        for url in ("https://x.com", "https://example.com/a", "http://localhost:8888"):
            self.assertIsNone(account_guard.browser_denial(url, allowed=False))

    def test_env_override_allows_deliberate_use(self):
        with patch.dict(os.environ, {account_guard.BROWSER_ALLOW_ENV: "1"}, clear=False):
            self.assertIsNone(account_guard.browser_denial("https://claude.ai/"))

    def test_lookalike_hosts_are_not_treated_as_the_real_site(self):
        self.assertIsNone(account_guard.browser_denial("https://claude.ai.evil.test/", allowed=False))

    def test_subscription_host_extraction(self):
        self.assertEqual(account_guard.subscription_host("https://chatgpt.com/c/1"), "chatgpt.com")
        self.assertEqual(account_guard.subscription_host("https://x.com"), "")


class ClaudeDenyRuleTests(unittest.TestCase):
    def test_deny_rules_ride_along_in_both_profiles(self):
        for profile in ("workshop", "unrestricted"):
            args = agent_permissions.claude_permission_args(profile)
            self.assertIn("--settings", args)
            rules = json.loads(args[args.index("--settings") + 1])
            denied = rules["permissions"]["deny"]
            self.assertIn("Bash(claude:*)", denied)
            self.assertIn("Bash(codex:*)", denied)

    def test_switch_disables_the_rules(self):
        with patch.dict(os.environ, {"PLINY_AGENT_SUBSCRIPTION_CLI_DENY": "0"}, clear=False):
            self.assertNotIn("--settings", agent_permissions.claude_permission_args("workshop"))


if __name__ == "__main__":
    unittest.main()
