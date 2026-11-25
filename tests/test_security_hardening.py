"""Regression tests for the security/reliability hardening pass.

No network calls, no provider keys are required, and no agent is launched.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import agent_permissions
import agent_secrets
import codex_runner
import gauntlet
import server
from process_control import _is_orphan_agent


class AgentSecretBoundaryTests(unittest.TestCase):
    def test_filtered_env_drops_credentials_but_keeps_runtime_config(self):
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "sk-or-fixture",
            "ANTHROPIC_API_KEY": "sk-ant-fixture",
            "GITHUB_TOKEN": "ghp_fixture",
            "PLINY_AGENT_TOKEN": "agent-fixture",
            "PATH": os.environ.get("PATH", ""),
        }, clear=False):
            env = agent_secrets.filtered_env()
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["PLINY_AGENT_TOKEN"], "agent-fixture")
        self.assertIn("PATH", env)

    def test_session_key_file_round_trip_and_resolution_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)):
                path = agent_secrets.write_session_key_file(
                    "fixture", {"OPENROUTER_API_KEY": "from-file"}, names=("OPENROUTER_API_KEY",)
                )
                self.assertIsNotNone(path)
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OPENROUTER_API_KEY", None)
                    self.assertEqual(
                        agent_secrets.resolve_key("OPENROUTER_API_KEY", key_file=str(path)),
                        "from-file",
                    )
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "from-env"}, clear=False):
                    self.assertEqual(
                        agent_secrets.resolve_key("OPENROUTER_API_KEY", key_file=str(path)),
                        "from-env",
                    )
                agent_secrets.remove_session_key_file("fixture")
                self.assertFalse(path.exists())

    def test_general_session_gets_no_provider_keys_or_key_file(self):
        with patch.object(agent_secrets, "KEY_DIR", Path(tempfile.mkdtemp())):
            session = server.Session("fixture-general", "Write a harmless poem", runtime="codex")
            env = session._agent_environment()
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn(agent_secrets.KEY_FILE_ENV, env)

    def test_red_team_session_gets_scoped_key_file_not_env_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)), \
                    patch.dict(os.environ, {
                        "OPENROUTER_API_KEY": "fixture-or",
                        "OPENAI_API_KEY": "fixture-openai",
                        "ANTHROPIC_API_KEY": "fixture-anthropic",
                    }, clear=False):
                session = server.Session(
                    "fixture-rt", "Use rt_send.py for each attempt",
                    runtime="codex", provider="openai",
                )
                env = session._agent_environment()
                self.assertNotIn("OPENROUTER_API_KEY", env)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertIn(agent_secrets.KEY_FILE_ENV, env)
                self.assertTrue(Path(env[agent_secrets.KEY_FILE_ENV]).exists())
                # Only the target provider's names — never the whole set.
                stored = agent_secrets.read_key_file(env[agent_secrets.KEY_FILE_ENV])
                self.assertEqual(set(stored), {"OPENAI_API_KEY", "OPENROUTER_API_KEY"})
                self.assertNotIn("ANTHROPIC_API_KEY", stored)
                agent_secrets.remove_session_key_file("fixture-rt")

    def test_red_team_session_without_a_provider_gets_no_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)), \
                    patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture-key"}, clear=False):
                session = server.Session(
                    "fixture-rt-noprov", "Use rt_send.py for each attempt", runtime="codex"
                )
                env = session._agent_environment()
                self.assertNotIn(agent_secrets.KEY_FILE_ENV, env)
                self.assertFalse(agent_secrets.session_key_path("fixture-rt-noprov").exists())


class HttpBoundaryTests(unittest.TestCase):
    def handler(self, headers, path="/api/status"):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = headers
        handler.path = path
        handler._send_json = Mock()
        return handler

    def test_cross_origin_requests_are_rejected(self):
        self.assertFalse(server._request_origin_allowed(self.handler({
            "Host": "localhost:8888", "Origin": "https://evil.test",
        })))
        self.assertFalse(server._require_api_access(self.handler({
            "Host": "localhost:8888", "Origin": "https://evil.test",
        })))

    def test_same_origin_and_cli_requests_are_allowed(self):
        self.assertTrue(server._request_origin_allowed(self.handler({})))
        self.assertFalse(server._request_origin_allowed(self.handler({
            "Sec-Fetch-Site": "cross-site",
        })))
        self.assertTrue(server._request_origin_allowed(self.handler({
            "Sec-Fetch-Site": "same-origin",
        })))
        self.assertTrue(server._request_origin_allowed(self.handler({
            "Host": "localhost:8888", "Origin": "http://localhost:8888",
        })))
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertTrue(server._check_auth(self.handler({
                    "Authorization": "Bearer operator-fixture",
                })))
                self.assertTrue(server._check_auth(self.handler({
                    "Authorization": "Bearer agent-fixture",
                })))
                self.assertFalse(server._check_auth(self.handler({
                    "Authorization": "Bearer wrong",
                })))
                # The scoped agent token must not open operator-only routes.
                self.assertTrue(server._require_auth(self.handler({
                    "Authorization": "Bearer operator-fixture",
                })))
                self.assertFalse(server._require_auth(self.handler({
                    "Authorization": "Bearer agent-fixture",
                })))

    def test_cors_header_never_wildcards(self):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {"Host": "localhost:8888", "Origin": "https://evil.test"}
        handler.send_header = Mock()
        handler._send_cors_headers()
        handler.send_header.assert_not_called()

        handler.headers = {"Host": "localhost:8888", "Origin": "http://localhost:8888"}
        handler.send_header.reset_mock()
        handler._send_cors_headers()
        handler.send_header.assert_any_call(
            "Access-Control-Allow-Origin", "http://localhost:8888"
        )
        handler.send_header.assert_any_call("Vary", "Origin")


class ProcessOwnershipTests(unittest.TestCase):
    ROOT = r"C:\work\pliny-command"
    WORKSHOP = ROOT + r"\state\workshop"

    def test_own_runners_are_orphans(self):
        self.assertTrue(_is_orphan_agent(
            "python.exe", rf"python {self.ROOT}\opencode_runner.py --model x",
            self.ROOT, self.WORKSHOP,
        ))
        self.assertTrue(_is_orphan_agent(
            "claude.exe", rf"claude --output-format stream-json --add-dir {self.WORKSHOP}",
            self.ROOT, self.WORKSHOP,
        ))
        self.assertTrue(_is_orphan_agent(
            "opencode.exe",
            "opencode.exe --standalone run --format json --title pliny-command:abc --agent build",
            self.ROOT, self.WORKSHOP,
        ))

    def test_unrelated_agents_are_not_orphans(self):
        self.assertFalse(_is_orphan_agent(
            "python.exe", r"python C:\other-project\opencode_runner.py --model x",
            self.ROOT, self.WORKSHOP,
        ))
        self.assertFalse(_is_orphan_agent(
            "claude.exe",
            "claude --output-format stream-json --verbose --dangerously-skip-permissions",
            self.ROOT, self.WORKSHOP,
        ))
        self.assertFalse(_is_orphan_agent(
            "opencode.exe",
            "opencode.exe --standalone run --format json --agent build",
            self.ROOT, self.WORKSHOP,
        ))


class GauntletClassificationTests(unittest.TestCase):
    def run_fixture(self, run_id="fixture-judge"):
        run = {
            "id": run_id,
            "status": "running",
            "max_attempts": 1,
            "preset": {"markers": [{
                "id": "behavior",
                "judge_type": "compliance",
                "request": "do X",
                "name": "X",
                "judge_threshold": 2,
            }]},
            "targets": [{
                "id": "target", "name": "Target", "provider": "fixture",
                "model": "fixture", "status": "running", "attempts": [],
                "assigned_behavior_id": "behavior",
            }],
        }
        gauntlet._gauntlet_runs[run_id] = run
        self.addCleanup(gauntlet._gauntlet_runs.pop, run_id, None)
        return run

    def test_judge_error_is_not_a_scored_refusal_or_failure(self):
        run = self.run_fixture()
        with patch.object(gauntlet, "_save_run"), patch("server.broadcast_sse"):
            with patch.object(gauntlet, "llm_judge_compliance", return_value={
                "score": 0, "reason": "judge HTTP error", "error": "500",
            }):
                target = gauntlet.record_attempt("fixture-judge", "target", {
                    "prompt": "p", "response": "a real target response",
                })
        self.assertEqual(target["status"], "running")
        self.assertTrue(target["attempts"][0]["is_error"])
        self.assertEqual(target["attempts"][0]["error_type"], "judge_error")
        self.assertEqual(run["targets"][0]["status"], "running")

    def test_three_judge_errors_block_the_target_without_calling_it_failed(self):
        self.run_fixture("fixture-judge-blocked")
        with patch.object(gauntlet, "_save_run"), patch("server.broadcast_sse"):
            with patch.object(gauntlet, "llm_judge_compliance", return_value={
                "score": 0, "reason": "judge unavailable", "error": "no_judge",
            }):
                for _ in range(gauntlet._MAX_CONSECUTIVE_JUDGE_ERRORS):
                    gauntlet.record_attempt("fixture-judge-blocked", "target", {
                        "prompt": "p", "response": "a real target response",
                    })
        self.assertEqual(
            gauntlet._gauntlet_runs["fixture-judge-blocked"]["targets"][0]["status"],
            "blocked_judge",
        )

    def test_pending_and_cancelled_targets_are_not_failures(self):
        run = {
            "id": "fixture-pending", "status": "running",
            "targets": [{"id": "target", "status": "running",
                         "current_session_id": "fixture-session"}],
        }
        gauntlet._gauntlet_runs[run["id"]] = run
        self.addCleanup(gauntlet._gauntlet_runs.pop, run["id"], None)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)):
                self.assertEqual(
                    gauntlet._target_end_status(run["id"], run["targets"][0]),
                    "failed",
                )
                (Path(tmp) / "fixture-session.jsonl").write_text(
                    json.dumps({"type": "prompt", "attempt": 1}) + "\n",
                    encoding="utf-8",
                )
                self.assertEqual(
                    gauntlet._target_end_status(run["id"], run["targets"][0]),
                    "pending_response",
                )
                run["status"] = "aborted"
                self.assertEqual(
                    gauntlet._target_end_status(run["id"], run["targets"][0]),
                    "aborted",
                )


class AgentPermissionTests(unittest.TestCase):
    def test_default_profile_does_not_bypass_permissions(self):
        with patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "workshop"}, clear=False):
            self.assertNotIn(
                "--dangerously-skip-permissions",
                agent_permissions.claude_permission_args(),
            )
            config = agent_permissions.opencode_permission_config()
            self.assertEqual(config["permission"]["external_directory"]["*"], "deny")
            self.assertEqual(config["permission"]["read"]["*.env"], "deny")

    def test_unrestricted_profile_is_opt_in(self):
        with patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "unrestricted"}, clear=False):
            self.assertIn(
                "--dangerously-skip-permissions",
                agent_permissions.claude_permission_args(),
            )
            self.assertEqual(agent_permissions.opencode_permission_config(), {})

    def test_desktop_write_roots_are_scoped_to_the_process_that_was_granted_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            granted_path = Path(tmp).resolve()
            with patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "workshop",
                                        agent_permissions.WRITE_ROOTS_ENV: str(granted_path)}, clear=False):
                config = agent_permissions.opencode_permission_config()
                claude = agent_permissions.claude_permission_args(write_roots=agent_permissions.write_roots())
        granted = granted_path.as_posix()
        self.assertEqual(config["permission"]["edit"][f"{granted}/**"], "allow")
        self.assertEqual(config["permission"]["external_directory"][f"{granted}/**"], "allow")
        # The credential read denies survive the grant.
        self.assertEqual(config["permission"]["read"]["*.env"], "deny")
        self.assertEqual(config["permission"]["read"]["**/operator.token"], "deny")
        self.assertIn("--add-dir", claude)
        self.assertIn(str(granted_path), claude)

    def test_missing_write_root_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "workshop",
                                        agent_permissions.WRITE_ROOTS_ENV: str(missing)}, clear=False):
                config = agent_permissions.opencode_permission_config()
        self.assertNotIn(missing.as_posix() + "/**", json.dumps(config))


class RedteamChatPersistenceBoundaryTests(unittest.TestCase):
    """Red-team chat filenames and durable rows are part of the credential boundary."""

    @staticmethod
    def post_handler(payload):
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        handler._read_body = lambda: json.dumps(payload).encode("utf-8")
        return handler

    def test_redteam_chat_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)):
                with self.assertRaises(ValueError):
                    server.redteam_chat_path("../../outside")

    def test_redteam_chat_post_rejects_traversal_session_id(self):
        handler = self.post_handler({
            "type": "status", "text": "harmless fixture",
            "session_id": "../../outside", "meta": {},
        })
        handler._send_json = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)), \
                    patch.object(server, "broadcast_sse"):
                handler._api_redteam_chat()
        self.assertEqual(handler._send_json.call_args[0][1], 400)
        self.assertIn("session_id", handler._send_json.call_args[0][0]["error"])

    def test_redteam_chat_backfill_rejects_traversal_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chats = root / "chats"
            chats.mkdir()
            (root / "secret.jsonl").write_text(
                json.dumps({"type": "status", "text": "outside"}) + "\n",
                encoding="utf-8",
            )
            handler = server.Handler.__new__(server.Handler)
            handler._send_json = Mock()
            with patch.object(server, "REDTEAM_CHATS_DIR", chats):
                handler._api_session_redteam_chat({"id": ["../secret"]})
        self.assertEqual(handler._send_json.call_args[0][1], 400)
        self.assertIn("session_id", handler._send_json.call_args[0][0]["error"])

    def test_freeform_chat_rows_are_secret_redacted_on_disk(self):
        secret = "sk-ant-fixture123456"
        handler = self.post_handler({
            "type": "status", "text": f"harmless fixture token {secret}",
            "session_id": "fixture-redaction", "meta": {},
        })
        handler._send_json = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)), \
                    patch.object(server, "broadcast_sse"):
                handler._api_redteam_chat()
            row = (Path(tmp) / "fixture-redaction.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, row)
        self.assertIn("<redacted-key>", row)

    def test_redaction_preserves_valid_jsonl_for_key_value_patterns(self):
        # Greedy \S{8,} in redact_secrets would swallow closing JSON quotes if
        # run after json.dumps(); redacting values structurally before dumping
        # keeps disk JSONL parseable and leaves the caller's live dict intact.
        event = {
            "type": "response",
            "text": "Use api_key: supersecret12345",
            "meta": {"header": "Authorization: Bearer abcdef1234567890"},
            "session_id": "fixture-json-valid",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)):
                path = server.append_redteam_event("fixture-json-valid", event)
            raw = path.read_text(encoding="utf-8").strip()
        parsed = json.loads(raw)
        self.assertNotIn("supersecret12345", raw)
        self.assertNotIn("abcdef1234567890", raw)
        self.assertIn("api_key=<redacted>", parsed["text"])
        self.assertIn("Bearer <redacted>", parsed["meta"]["header"])
        # Live caller payload was not mutated in place.
        self.assertIn("supersecret12345", event["text"])


class CodexSandboxTests(unittest.TestCase):
    def test_codex_mcp_forwards_the_cu_capability(self):
        overrides = " ".join(codex_runner.mcp_overrides(
            "pliny_cu", {"command": "python", "args": []}))
        self.assertIn("PLINY_CU_TOKEN", overrides)

    def test_workshop_profile_keeps_the_codex_sandbox(self):
        with patch("codex_runner.shutil.which", return_value="codex"), \
                patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "workshop"}, clear=False):
            command = codex_runner.codex_command()
        self.assertIn('sandbox_mode="workspace-write"', command)

    def test_unrestricted_profile_lifts_the_codex_sandbox(self):
        with patch("codex_runner.shutil.which", return_value="codex"), \
                patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "unrestricted"}, clear=False):
            command = codex_runner.codex_command()
        self.assertIn('sandbox_mode="danger-full-access"', command)
        self.assertNotIn('sandbox_mode="workspace-write"', command)

    def test_codex_writable_roots_follow_the_session_grant(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("codex_runner.shutil.which", return_value="codex"), \
                patch.dict(os.environ, {agent_permissions.PROFILE_ENV: "workshop",
                                        agent_permissions.WRITE_ROOTS_ENV: tmp}, clear=False):
            command = codex_runner.codex_command()
        self.assertTrue(any(entry.startswith("sandbox_workspace_write.writable_roots=")
                            for entry in command))


class ProviderKeyMetadataTests(unittest.TestCase):
    def test_redteam_agent_name_constants_are_shared(self):
        expected = frozenset({"blood-agent", "rt-fallback-alpha", "rt-fallback-beta"})
        self.assertEqual(server.REDTEAM_TRANSPORT_AGENT_NAMES, expected)
        self.assertIs(server.NO_CLI_FALLBACK_AGENT_NAMES,
                      server.REDTEAM_TRANSPORT_AGENT_NAMES)
        self.assertTrue(expected <= server.PRIVATE_CLAUDE_AGENT_NAMES)
        self.assertIn("pliny-the-liberator", server.PRIVATE_CLAUDE_AGENT_NAMES)

    def test_explicit_true_overrides_benign_prompt(self):
        session = server.Session("fixture-explicit-yes", "Write a harmless poem",
                                 runtime="codex", needs_provider_keys=True)
        self.assertTrue(session._needs_provider_keys())

    def test_explicit_false_overrides_redteam_flavored_prompt(self):
        # An ordinary mission whose prompt merely mentions red-team tooling
        # must not receive provider material when the caller says it is not
        # a transport session.
        session = server.Session(
            "fixture-explicit-no",
            "Summarise this redteam report that mentions rt_send.py and gauntlet",
            runtime="codex", needs_provider_keys=False)
        self.assertFalse(session._needs_provider_keys())
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)):
                env = session._agent_environment()
        self.assertNotIn(agent_secrets.KEY_FILE_ENV, env)

    def test_legacy_heuristic_still_applies_without_metadata(self):
        session = server.Session("fixture-legacy-heuristic",
                                 "Use rt_send.py for each attempt", runtime="codex")
        self.assertTrue(session._needs_provider_keys())

    def test_launch_session_passes_explicit_metadata_through(self):
        captured = {}

        class FakeSession(server.Session):
            def __init__(self, *args, **kwargs):
                captured["needs_provider_keys"] = kwargs.get("needs_provider_keys")
                raise RuntimeError("stop before spawn")

        with patch.object(server, "Session", FakeSession):
            try:
                server.launch_session("harmless fixture", duration_seconds=60,
                                      needs_provider_keys=True)
            except Exception:
                pass
        self.assertTrue(captured.get("needs_provider_keys"))

    def test_api_launch_grants_provider_keys_for_explicit_transport_agent(self):
        def call_launch(payload):
            handler = RedteamChatPersistenceBoundaryTests.post_handler(payload)
            handler._send_json = Mock()
            with patch.object(server, "_require_auth", return_value=True), \
                    patch.object(server, "validate_model"), \
                    patch.object(server, "audit_log"), \
                    patch.object(server, "_audit_request", return_value={}), \
                    patch.object(server, "launch_session",
                                 return_value={"id": "s-fixture"}) as launched:
                handler._api_launch()
            return launched.call_args.kwargs["needs_provider_keys"]

        self.assertTrue(call_launch({
            "prompt": "raw probe",
            "agent": "blood-agent",
        }))
        self.assertFalse(call_launch({
            "prompt": "Summarise this redteam report mentioning rt_send.py",
            "agent": "pliny-the-liberator",
        }))


if __name__ == "__main__":
    unittest.main()
