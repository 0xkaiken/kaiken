"""Regression tests for the security/credentials/pipeline hardening pass.

Covers: lab endpoint authorization, agent-vs-operator separation in default
localhost mode, per-provider credential scoping, and gauntlet status
classification in the research pipeline.

No network calls, no provider keys, no subprocess is spawned.
"""

import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import account_guard
import agent_secrets
import gauntlet
import lab_api
import opencode_runner
import process_control
import server


class Handler:
    def __init__(self, headers=None, path="/api/lab/start"):
        self.headers = headers or {}
        self.path = path
        self.payload = None
        self.status = None

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status


class OperatorAuthTests(unittest.TestCase):
    """Localhost is not an identity. Only pairing or a bearer is."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, value in (
            ("PAIRED_DEVICES_FILE", Path(self._tmp.name) / "devices.json"),
            ("AUDIT_LOG_FILE", Path(self._tmp.name) / "audit.jsonl"),
            ("_pair_code", {"value": "test-code", "issued_at": time.time()}),
            ("_pair_attempts", {}),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        account_guard.clear_origin()

    def _paired_cookie(self) -> str:
        token, _ = server._register_device("test")
        return f"{server.PAIR_COOKIE}={token}"

    def test_anonymous_request_is_not_operator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertIsNone(server._auth_kind(Handler({})))

    def test_valid_paired_cookie_is_operator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            handler = Handler({"Cookie": self._paired_cookie()})
            self.assertEqual(server._auth_kind(handler), "operator")

    def test_unknown_cookie_is_not_operator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            handler = Handler({"Cookie": f"{server.PAIR_COOKIE}=forged"})
            self.assertIsNone(server._auth_kind(handler))

    def test_operator_bearer_is_operator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({"Authorization": f"Bearer {server._operator_secret()}"})
                self.assertEqual(server._auth_kind(handler), "operator")

    def test_agent_token_is_agent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertEqual(
                    server._auth_kind(Handler({"Authorization": "Bearer agent-fixture"})),
                    "agent",
                )

    def test_unmatched_credential_is_not_promoted_to_operator(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertIsNone(
                    server._auth_kind(Handler({"Authorization": "Bearer not-a-token"}))
                )

    def test_configured_token_still_wins_for_agents(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                self.assertEqual(
                    server._auth_kind(Handler({"Authorization": "Bearer operator-fixture"})),
                    "operator",
                )
                self.assertEqual(
                    server._auth_kind(Handler({"Authorization": "Bearer agent-fixture"})),
                    "agent",
                )

    def test_anonymous_gate_fails_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({})
                self.assertFalse(server._require_auth(handler))
                self.assertEqual(handler.status, 403)

    def test_agent_token_gate_fails_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({"Authorization": "Bearer agent-fixture"})
                self.assertFalse(server._require_auth(handler))
                self.assertEqual(handler.status, 403)

    def test_paired_cookie_passes_the_operator_gate(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({"Cookie": self._paired_cookie()})
                self.assertTrue(server._require_auth(handler))

    def test_anonymous_reads_stay_reachable(self):
        """Dashboard polling and CLI status checks must keep working."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({})
                self.assertTrue(server._require_api_access(handler))
                self.assertEqual(account_guard.current_origin(), "agent")
                self.assertFalse(account_guard.current_authorization())

    def test_anonymous_is_rejected_when_an_operator_token_is_configured(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({})
                self.assertFalse(server._require_api_access(handler))
                self.assertEqual(handler.status, 403)


class PageLoadDoesNotGrantTests(unittest.TestCase):
    """The regression: fetching a public page must never mint a credential."""

    @staticmethod
    def _handler():
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        return handler

    def _sent(self, handler):
        return {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}

    def test_html_page_sets_no_cookie(self):
        handler = self._handler()
        handler._send_text("<html><body>x</body></html>", "text/html;charset=utf-8")
        self.assertNotIn("Set-Cookie", self._sent(handler))

    def test_non_html_sets_no_cookie(self):
        handler = self._handler()
        handler._send_text("plain", "text/plain")
        self.assertNotIn("Set-Cookie", self._sent(handler))

    def test_overlay_is_injected_once(self):
        html = "<html><body>hi</body></html>"
        once = server._inject_pairing(html)
        self.assertIn("__plinyPairOverlay", once)
        self.assertEqual(server._inject_pairing(once), once)


class PairingTests(unittest.TestCase):
    """Explicit local pairing is the only way a browser becomes operator."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, value in (
            ("PAIRED_DEVICES_FILE", Path(self._tmp.name) / "devices.json"),
            ("AUDIT_LOG_FILE", Path(self._tmp.name) / "audit.jsonl"),
            ("_pair_code", {"value": "test-code", "issued_at": time.time()}),
            ("_pair_attempts", {}),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        printer = patch.object(server, "print_pairing_code")
        printer.start()
        self.addCleanup(printer.stop)

    @staticmethod
    def _handler(body=None, headers=None):
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = dict(headers or {})
        handler.path = "/api/pair"
        handler.client_address = ("127.0.0.1", 4242)
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        handler._read_body = lambda: body or b"{}"
        return handler

    @staticmethod
    def _status(handler):
        return handler.send_response.call_args[0][0]

    @staticmethod
    def _headers(handler):
        return {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}

    @staticmethod
    def _body(handler):
        return json.loads(handler.wfile.getvalue().decode("utf-8"))

    def _pair(self, code):
        handler = self._handler(json.dumps({"code": code}).encode())
        handler._api_pair_create()
        return handler

    def test_status_reports_unpaired(self):
        handler = self._handler()
        handler._api_pair_status()
        body = self._body(handler)
        self.assertFalse(body["paired"])
        self.assertTrue(body["required"])

    def test_wrong_code_is_rejected_without_a_cookie(self):
        handler = self._pair("wrong")
        self.assertEqual(self._status(handler), 401)
        self.assertNotIn("Set-Cookie", self._headers(handler))

    def test_correct_code_mints_a_session_cookie(self):
        handler = self._pair("test-code")
        self.assertEqual(self._status(handler), 200)
        cookie = self._headers(handler).get("Set-Cookie", "")
        self.assertIn(server.PAIR_COOKIE, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Max-Age", cookie)

    def test_device_store_holds_only_a_hash(self):
        handler = self._pair("test-code")
        cookie = self._headers(handler).get("Set-Cookie", "")
        raw = cookie.split(";", 1)[0].split("=", 1)[1]
        stored = server.PAIRED_DEVICES_FILE.read_text(encoding="utf-8")
        self.assertNotIn(raw, stored)
        self.assertIn(server._hash_token(raw), stored)

    def test_minted_cookie_authorizes_privileged_routes(self):
        handler = self._pair("test-code")
        cookie = self._headers(handler).get("Set-Cookie", "").split(";", 1)[0]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                probe = Handler({"Cookie": cookie})
                self.assertEqual(server._auth_kind(probe), "operator")
                self.assertTrue(server._require_auth(probe))

    def test_code_is_single_use(self):
        self.assertEqual(self._status(self._pair("test-code")), 200)
        self.assertEqual(self._status(self._pair("test-code")), 401)

    def test_rate_limit_stops_guessing(self):
        for _ in range(server.PAIR_RATE_LIMIT):
            self.assertEqual(self._status(self._pair("wrong")), 401)
        self.assertEqual(self._status(self._pair("test-code")), 429)

    def test_pairing_stays_reachable_with_a_configured_token(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            handler = self._handler()
            handler.path = "/api/pair"
            self.assertTrue(server._require_api_access(handler))
            self.assertTrue(server._is_pairing_path("/api/pair/status"))

    def test_configured_token_can_pair_without_the_code(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "operator-fixture"}, clear=False):
            handler = self._pair("operator-fixture")
            self.assertEqual(self._status(handler), 200)

    def test_revoke_invalidates_the_session(self):
        handler = self._pair("test-code")
        cookie = self._headers(handler).get("Set-Cookie", "").split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        self.assertTrue(server._revoke_device(token))
        self.assertFalse(server._device_is_paired(token))


class LabAuthorizationTests(unittest.TestCase):
    """Issue 1 — /api/lab/start and /api/lab/stop are operator actions."""

    @staticmethod
    def _real_handler(token):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {"Authorization": f"Bearer {token}"}
        handler.path = "/api/lab/start"
        handler._send_json = Mock()
        handler._read_body = Mock()
        return handler

    def test_agent_token_cannot_start_a_lab_run(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = self._real_handler("agent-fixture")
                handler._api_lab_post("/api/lab/start")
                handler._read_body.assert_not_called()
                self.assertEqual(handler._send_json.call_args[0][1], 403)

    def test_agent_token_cannot_stop_a_lab_run(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = self._real_handler("agent-fixture")
                handler._api_lab_post("/api/lab/stop")
                self.assertEqual(handler._send_json.call_args[0][1], 403)


class ProviderScopeTests(unittest.TestCase):
    """Issue 3 — a session holds only the keys its target needs."""

    def test_known_providers_map_to_their_own_names(self):
        self.assertEqual(agent_secrets.provider_key_names("anthropic"), ("ANTHROPIC_API_KEY",))
        self.assertEqual(agent_secrets.provider_key_names("deepseek"), ("DEEPSEEK_API_KEY",))
        self.assertIn("OPENROUTER_API_KEY", agent_secrets.provider_key_names("openai"))

    def test_unknown_and_local_providers_need_no_key(self):
        self.assertEqual(agent_secrets.provider_key_names("not-a-provider"), ())
        self.assertEqual(agent_secrets.provider_key_names(""), ())
        self.assertEqual(agent_secrets.provider_key_names("ollama"), ())

    def test_write_session_key_file_defaults_to_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"OPENROUTER_API_KEY": "a", "ANTHROPIC_API_KEY": "b"}
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)):
                path = agent_secrets.write_session_key_file("scope-default", env)
            self.assertIsNone(path)
            self.assertFalse((Path(tmp) / "scope-default.json").exists())

    def test_only_requested_names_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"OPENROUTER_API_KEY": "a", "ANTHROPIC_API_KEY": "b", "OPENAI_API_KEY": "c"}
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)):
                path = agent_secrets.write_session_key_file(
                    "scope-anthropic", env, names=agent_secrets.provider_key_names("anthropic")
                )
                self.assertIsNotNone(path)
                stored = agent_secrets.read_key_file(str(path))
            self.assertEqual(set(stored), {"ANTHROPIC_API_KEY"})

    def test_transport_extra_names_are_added_to_the_target_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "KEY_DIR", Path(tmp)), \
                    patch.dict(os.environ, {
                        "OPENROUTER_API_KEY": "or",
                        "ANTHROPIC_API_KEY": "ant",
                        "OPENAI_API_KEY": "oai",
                    }, clear=False):
                session = server.Session(
                    "fixture-hermes", "Use rt_send.py for each attempt",
                    runtime="codex", provider="anthropic",
                )
                env = session._agent_environment(
                    require_provider_keys=True, extra_names=("OPENROUTER_API_KEY",)
                )
                stored = agent_secrets.read_key_file(env[agent_secrets.KEY_FILE_ENV])
            self.assertEqual(set(stored), {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"})


class PipelineStatusTests(unittest.TestCase):
    """Issue 4 — non-terminal gauntlet states are not genuine failures."""

    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parent.parent / "research-pipeline" / "pipeline_runner.py"
        spec = importlib.util.spec_from_file_location("pipeline_runner_under_test", source)
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    @staticmethod
    def _run(run_id, statuses):
        return {
            "id": run_id,
            "targets": [
                {"assigned_behavior_name": "behavior-a", "status": status, "model": "m"}
                for status in statuses
            ],
        }

    def test_non_terminal_states_are_not_failures(self):
        runs = [self._run("r1", [
            "blocked_judge", "interrupted", "pending_response", "aborted",
            "blocked_upstream",
        ])]
        scored = self.runner.score_directions(runs, [], set())
        direction = next(d for d in scored if d["direction"] == "behavior-a")
        self.assertEqual(direction["failed"], 0)
        self.assertEqual(direction["inconclusive"], 5)
        self.assertNotIn("5 failures", " ".join(direction["reasons"]))

    def test_genuine_failures_still_score(self):
        runs = [self._run(f"r{i}", ["failed"]) for i in range(3)]
        scored = self.runner.score_directions(runs, [], set())
        direction = next(d for d in scored if d["direction"] == "behavior-a")
        self.assertEqual(direction["failed"], 3)
        # +3 repeated failure, +3 distinct runs
        self.assertEqual(direction["score"], 6)

    def test_mixed_statuses_count_separately(self):
        runs = [self._run("r1", ["failed", "cracked", "blocked_judge"])]
        scored = self.runner.score_directions(runs, [], set())
        direction = next(d for d in scored if d["direction"] == "behavior-a")
        self.assertEqual(direction["failed"], 1)
        self.assertEqual(direction["cracked"], 1)
        self.assertEqual(direction["inconclusive"], 1)

    def test_unknown_status_is_inconclusive_not_failed(self):
        runs = [self._run("r1", ["something-new"])]
        scored = self.runner.score_directions(runs, [], set())
        direction = next(d for d in scored if d["direction"] == "behavior-a")
        self.assertEqual(direction["failed"], 0)
        self.assertEqual(direction["inconclusive"], 1)


class JudgeCredentialTests(unittest.TestCase):
    """Issue 2 — the judge must reach its own scoped key, not the vault."""

    def test_scoped_key_file_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key.json"
            key_file.write_text(json.dumps({"OPENROUTER_API_KEY": "or-from-file"}))
            with patch.dict(os.environ, {agent_secrets.KEY_FILE_ENV: str(key_file)}, clear=False):
                os.environ.pop("OPENROUTER_API_KEY", None)
                self.assertEqual(gauntlet._get_openrouter_key(), "or-from-file")

    def test_environment_wins_over_the_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key.json"
            key_file.write_text(json.dumps({"OPENROUTER_API_KEY": "from-file"}))
            with patch.dict(os.environ, {
                agent_secrets.KEY_FILE_ENV: str(key_file),
                "OPENROUTER_API_KEY": "from-env",
            }, clear=False):
                self.assertEqual(gauntlet._get_openrouter_key(), "from-env")

    def test_scoped_process_never_reads_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "state").mkdir()
            (base / "state" / "secrets.json").write_text(
                json.dumps({"openrouter_key": "vault-key"})
            )
            with patch.dict(os.environ, {"PLINY_LAB_SCOPED_ENV": "1"}, clear=False):
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop(agent_secrets.KEY_FILE_ENV, None)
                with patch.object(gauntlet, "BASE_DIR", base):
                    self.assertIsNone(gauntlet._get_openrouter_key())

    def test_unscoped_process_still_uses_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "state").mkdir()
            (base / "state" / "secrets.json").write_text(
                json.dumps({"openrouter_key": "vault-key"})
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENROUTER_API_KEY", None)
                os.environ.pop(agent_secrets.KEY_FILE_ENV, None)
                os.environ.pop("PLINY_LAB_SCOPED_ENV", None)
                with patch.object(gauntlet, "BASE_DIR", base):
                    self.assertEqual(gauntlet._get_openrouter_key(), "vault-key")


class JudgeErrorTests(unittest.TestCase):
    """Issue 2 — a judge failure is unscored, never a refusal (score 0)."""

    def test_missing_key_is_unscored(self):
        with patch.object(gauntlet, "_get_openrouter_key", return_value=None):
            with patch.object(gauntlet, "_judge_uses_go", return_value=False):
                verdict = gauntlet.llm_judge_compliance("req", "resp")
        self.assertIsNone(verdict["score"])
        self.assertEqual(verdict["error"], "no_judge")

    def test_http_error_is_unscored(self):
        import urllib.error

        error = urllib.error.HTTPError("http://judge", 429, "rate limited", {}, None)
        with patch.object(gauntlet, "_get_openrouter_key", return_value="k"), \
                patch.object(gauntlet, "_judge_uses_go", return_value=False), \
                patch("urllib.request.urlopen", side_effect=error):
            verdict = gauntlet.llm_judge_compliance("req", "resp")
        self.assertIsNone(verdict["score"])
        self.assertIsNotNone(verdict["error"])

    def test_transport_error_is_unscored(self):
        with patch.object(gauntlet, "_get_openrouter_key", return_value="k"), \
                patch.object(gauntlet, "_judge_uses_go", return_value=False), \
                patch("urllib.request.urlopen", side_effect=OSError("socket closed")):
            verdict = gauntlet.llm_judge_compliance("req", "resp")
        self.assertIsNone(verdict["score"])
        self.assertIsNotNone(verdict["error"])

    def test_scored_path_is_unchanged(self):
        payload = Mock()
        payload.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"score": 2, "reason": "complied"}'}}]
        }).encode()
        with patch.object(gauntlet, "_get_openrouter_key", return_value="k"), \
                patch.object(gauntlet, "_judge_uses_go", return_value=False), \
                patch("urllib.request.urlopen", return_value=payload):
            verdict = gauntlet.llm_judge_compliance("req", "resp")
        self.assertEqual(verdict["score"], 2)
        self.assertIsNone(verdict["error"])


class JudgeModelTests(unittest.TestCase):
    """Every compliance judge runs on OpenCode Go with the operator prompt."""

    def test_default_judge_model_and_persona_prefix(self):
        seen = {}

        def fake_complete(prompt, model=None, timeout=None, pin_body=None):
            seen["model"] = model
            seen["messages"] = json.loads(prompt)
            seen["pin_body"] = pin_body
            return '{"score": 3, "reason": "complied"}'

        with tempfile.TemporaryDirectory() as tmp:
            persona_file = Path(tmp) / "persona.txt"
            persona_file.write_text("Fixture judge persona.", encoding="utf-8")
            with patch.object(gauntlet, "JUDGE_PERSONA_FILE", persona_file), \
                    patch.object(gauntlet, "go_complete_text", side_effect=fake_complete):
                verdict = gauntlet.llm_judge_compliance("req", "resp")
                persona = gauntlet._judge_persona()

        self.assertEqual(verdict["score"], 3)
        self.assertEqual(seen["model"], "opencode-go/deepseek-v4.1-flash")
        # The judge pins temperature=0.0 on every verdict (probes/temp_pin_probe.py:
        # the transport drops every config-based placement; only the proxy pin
        # reaches the provider).
        self.assertEqual(seen["pin_body"], {"temperature": 0.0})
        system = seen["messages"][0]["content"]
        self.assertTrue(persona)
        self.assertTrue(system.startswith(persona.splitlines()[0]))
        self.assertIn("AUTOMATED EVALUATION OVERRIDE", system)


class LabJudgeScopeTests(unittest.TestCase):
    """Issue 2 — the lab run carries the judge key but nothing else."""

    def _start(self, judge):
        tmp = Path(tempfile.mkdtemp())
        with patch.object(lab_api, "RUNS_ROOT", tmp / "runs"), \
                patch.object(lab_api, "TROPHY_ROOT", tmp / "trophies"), \
                patch.object(agent_secrets, "KEY_DIR", tmp / "keys"), \
                patch.dict(os.environ, {
                    "OPENROUTER_API_KEY": "or-secret",
                    "ANTHROPIC_API_KEY": "ant-secret",
                    "OPENAI_API_KEY": "oai-secret",
                }, clear=False):
            lab_api.RUNS_ROOT.mkdir(parents=True, exist_ok=True)
            lab_api.TROPHY_ROOT.mkdir(parents=True, exist_ok=True)
            captured = {}

            class FakeProc:
                pid = 4242
                returncode = 0

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

            def fake_popen(argv, **kwargs):
                captured["env"] = kwargs.get("env", {})
                return FakeProc()

            with patch.object(lab_api.subprocess, "Popen", fake_popen), \
                    patch.object(lab_api.threading, "Thread") as thread:
                thread.return_value.start = lambda: None
                result = lab_api.start_run({
                    "provider": "anthropic", "model": "claude-x",
                    "behavior": "b1", "reps": 1, "judge": judge,
                })
            key_path = captured["env"].get(agent_secrets.KEY_FILE_ENV)
            stored = agent_secrets.read_key_file(key_path) if key_path else {}
            return result, captured["env"], stored

    def test_judge_key_is_scoped_in_with_the_target_key(self):
        _, env, stored = self._start(judge=True)
        self.assertEqual(set(stored), {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"})
        self.assertNotIn("OPENAI_API_KEY", stored)

    def test_no_judge_means_no_openrouter_key(self):
        _, _, stored = self._start(judge=False)
        self.assertEqual(set(stored), {"ANTHROPIC_API_KEY"})

    def test_secrets_never_reach_the_process_environment(self):
        _, env, _ = self._start(judge=True)
        for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                     "PLINY_AUTH_TOKEN"):
            self.assertNotIn(name, env)


class OperatorTokenFileTests(unittest.TestCase):
    def test_file_is_the_cli_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operator.token"
            path.write_text("file-token", encoding="utf-8")
            with patch.object(agent_secrets, "OPERATOR_TOKEN_FILE", path):
                os.environ.pop("PLINY_AUTH_TOKEN", None)
                self.assertEqual(agent_secrets.operator_token(), "file-token")
                self.assertEqual(
                    agent_secrets.operator_headers(),
                    {"Authorization": "Bearer file-token"},
                )

    def test_env_token_wins(self):
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": "env-token"}, clear=False):
            self.assertEqual(agent_secrets.operator_token(), "env-token")

    def test_missing_file_yields_no_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agent_secrets, "OPERATOR_TOKEN_FILE", Path(tmp) / "nope"):
                os.environ.pop("PLINY_AUTH_TOKEN", None)
                self.assertEqual(agent_secrets.operator_token(), "")
                self.assertEqual(agent_secrets.operator_headers(), {})


class PairingExpiryTests(unittest.TestCase):
    """Codes and sessions expire server-side, not just via Max-Age."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, value in (
            ("PAIRED_DEVICES_FILE", Path(self._tmp.name) / "devices.json"),
            ("AUDIT_LOG_FILE", Path(self._tmp.name) / "audit.jsonl"),
            ("_pair_code", {"value": "test-code", "issued_at": time.time()}),
            ("_pair_attempts", {}),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        printer = patch.object(server, "print_pairing_code")
        printer.start()
        self.addCleanup(printer.stop)

    @staticmethod
    def _handler(body=None):
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler.path = "/api/pair"
        handler.client_address = ("127.0.0.1", 4242)
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        handler._read_body = lambda: body or b"{}"
        return handler

    @staticmethod
    def _sent(handler):
        return {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}

    def test_expired_code_is_rejected_and_burned(self):
        with patch.object(server, "PAIR_CODE_TTL_SECONDS", 60):
            server._pair_code["issued_at"] = time.time() - 120
            self.assertFalse(server._pair_code_matches("test-code"))
        self.assertEqual(server._pair_code["value"], "")
        self.assertEqual(server._pair_code["issued_at"], 0.0)

    def test_code_within_ttl_is_accepted_once(self):
        with patch.object(server, "PAIR_CODE_TTL_SECONDS", 60):
            server._pair_code["issued_at"] = time.time() - 10
            self.assertTrue(server._pair_code_matches("test-code"))
            self.assertFalse(server._pair_code_matches("test-code"))

    def test_pairing_code_mints_fresh_after_expiry(self):
        server._pair_code["issued_at"] = time.time() - 10 ** 6
        code = server.pairing_code()
        self.assertNotEqual(code, "test-code")
        self.assertGreater(server._pair_code["issued_at"], time.time() - 60)

    def test_expired_session_cookie_is_rejected(self):
        token, _ = server._register_device("test")
        devices = server._load_paired_devices()
        devices[0]["expires_at"] = time.time() - 1
        server._save_paired_devices(devices)
        self.assertFalse(server._device_is_paired(token))

    def test_expired_record_is_pruned_from_disk(self):
        token, _ = server._register_device("test")
        devices = server._load_paired_devices()
        devices[0]["expires_at"] = time.time() - 1
        server._save_paired_devices(devices)
        self.assertFalse(server._device_is_paired(token))
        self.assertEqual(server._load_paired_devices(), [])

    def test_legacy_record_without_expiry_is_rejected(self):
        token, _ = server._register_device("test")
        devices = server._load_paired_devices()
        devices[0].pop("expires_at")
        server._save_paired_devices(devices)
        self.assertFalse(server._device_is_paired(token))

    def test_fresh_session_is_accepted(self):
        token, _ = server._register_device("test")
        self.assertTrue(server._device_is_paired(token))

    def test_expired_session_cannot_reach_privileged_routes(self):
        token, _ = server._register_device("test")
        devices = server._load_paired_devices()
        devices[0]["expires_at"] = time.time() - 1
        server._save_paired_devices(devices)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLINY_AUTH_TOKEN", None)
            with patch.object(server, "AGENT_TOKEN", "agent-fixture"):
                handler = Handler({"Cookie": f"{server.PAIR_COOKIE}={token}"})
                self.assertFalse(server._require_auth(handler))
                self.assertEqual(handler.status, 403)

    def test_cookie_max_age_matches_the_server_ttl(self):
        handler = self._handler(json.dumps({"code": "test-code"}).encode())
        handler._api_pair_create()
        self.assertEqual(handler.send_response.call_args[0][0], 200)
        cookie = self._sent(handler).get("Set-Cookie", "")
        self.assertIn(f"Max-Age={server.PAIR_DEVICE_TTL_SECONDS}", cookie)

    def test_ttl_comes_from_the_environment(self):
        with patch.dict(os.environ, {"PLINY_PAIR_CODE_TTL": "120"}, clear=False):
            self.assertEqual(server._env_int("PLINY_PAIR_CODE_TTL", 900), 120)
        with patch.dict(os.environ, {"PLINY_PAIR_CODE_TTL": "0"}, clear=False):
            self.assertEqual(
                server._env_int("PLINY_PAIR_CODE_TTL", 900, minimum=30), 30
            )
        with patch.dict(os.environ, {"PLINY_PAIR_CODE_TTL": "nonsense"}, clear=False):
            self.assertEqual(server._env_int("PLINY_PAIR_CODE_TTL", 900), 900)


class PairingConcurrencyTests(unittest.TestCase):
    """One code, one winner. Validation and consumption share a lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, value in (
            ("PAIRED_DEVICES_FILE", Path(self._tmp.name) / "devices.json"),
            ("AUDIT_LOG_FILE", Path(self._tmp.name) / "audit.jsonl"),
            ("_pair_code", {"value": "test-code", "issued_at": time.time()}),
            ("_pair_attempts", {}),
            ("PAIR_RATE_LIMIT", 100),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        printer = patch.object(server, "print_pairing_code")
        printer.start()
        self.addCleanup(printer.stop)

    @staticmethod
    def _handler(body=None):
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler.path = "/api/pair"
        handler.client_address = ("127.0.0.1", 4242)
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        handler._read_body = lambda: body or b"{}"
        return handler

    def test_code_is_consumed_exactly_once(self):
        workers = 8
        barrier = threading.Barrier(workers)
        results = []
        guard = threading.Lock()

        def worker():
            barrier.wait()
            outcome = server._pair_code_matches("test-code")
            with guard:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(results), workers)
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), workers - 1)

    def test_concurrent_pair_requests_mint_one_device(self):
        workers = 6
        barrier = threading.Barrier(workers)
        statuses = []
        guard = threading.Lock()
        body = json.dumps({"code": "test-code"}).encode()

        def worker():
            handler = self._handler(body)
            barrier.wait()
            handler._api_pair_create()
            with guard:
                statuses.append(handler.send_response.call_args[0][0])

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(len(statuses), workers)
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(len(server._load_paired_devices()), 1)


class PairingCodeRenewalTests(unittest.TestCase):
    """Pairing requests renew expired codes without idle console spam."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for name, value in (
            ("PAIRED_DEVICES_FILE", Path(self._tmp.name) / "devices.json"),
            ("AUDIT_LOG_FILE", Path(self._tmp.name) / "audit.jsonl"),
            ("_pair_code", {"value": "test-code", "issued_at": time.time()}),
            ("_pair_attempts", {}),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _expire():
        server._pair_code["issued_at"] = time.time() - (server.PAIR_CODE_TTL_SECONDS + 5)

    def test_live_code_is_not_reissued_or_reprinted(self):
        with patch("builtins.print") as printer:
            first = server.print_pairing_code()
            second = server.print_pairing_code()
        self.assertEqual(first, second)
        self.assertEqual(printer.call_count, 1)

    def test_expired_code_is_replaced_and_printed(self):
        server._pair_code["value"] = "old-code"
        self._expire()
        with patch("builtins.print") as printer:
            code = server.print_pairing_code("previous code expired")
        self.assertNotEqual(code, "old-code")
        self.assertEqual(printer.call_count, 1)
        printed = printer.call_args[0][0]
        self.assertIn(code, printed)
        self.assertIn("previous code expired", printed)
        # The expired value stays invalid.
        self.assertFalse(server._pair_code_matches("old-code"))

    def test_refresh_only_mints_when_the_code_is_dead(self):
        self.assertIsNone(server._pair_code_refresh())
        self._expire()
        fresh = server._pair_code_refresh()
        self.assertTrue(fresh)
        self.assertNotEqual(fresh, "test-code")
        self.assertIsNone(server._pair_code_refresh())

    def test_renewed_code_is_still_single_use(self):
        self._expire()
        with patch("builtins.print"):
            code = server.print_pairing_code("expired")
        self.assertTrue(server._pair_code_matches(code))
        self.assertFalse(server._pair_code_matches(code))

    def test_unpaired_status_renews_expired_code_only_once(self):
        self._expire()
        with patch("builtins.print") as printer:
            for _ in range(2):
                handler = PairingTests._handler()
                handler._api_pair_status()
        self.assertEqual(printer.call_count, 1)
        self.assertTrue(server._pair_code_matches(server._pair_code["value"]))

    def test_paired_status_stays_quiet_even_after_code_expiry(self):
        self._expire()
        token, _ = server._register_device("test browser")
        handler = PairingTests._handler(headers={"Cookie": f"{server.PAIR_COOKIE}={token}"})
        with patch("builtins.print") as printer:
            handler._api_pair_status()
        self.assertEqual(printer.call_count, 0)
        self.assertTrue(PairingTests._body(handler)["paired"])

    def test_expired_submission_prints_a_replacement_but_still_rejects(self):
        self._expire()
        handler = PairingTests._handler(b'{"code":"test-code"}')
        with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": ""}), \
                patch("builtins.print") as printer:
            handler._api_pair_create()
        self.assertEqual(PairingTests._status(handler), 401)
        self.assertNotIn("Set-Cookie", PairingTests._headers(handler))
        self.assertEqual(printer.call_count, 1)
        self.assertNotEqual(server._pair_code["value"], "test-code")

    def test_concurrent_renewal_prints_exactly_once(self):
        server._pair_code["value"] = ""
        server._pair_code["issued_at"] = 0.0
        workers = 6
        barrier = threading.Barrier(workers)

        def worker():
            barrier.wait()
            server.print_pairing_code("expired")

        with patch("builtins.print") as printer:
            threads = [threading.Thread(target=worker) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(printer.call_count, 1)

    def test_status_endpoint_never_exposes_the_code(self):
        import io

        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler.path = "/api/pair/status"
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._send_cors_headers = Mock()
        handler._api_pair_status()
        body = handler.wfile.getvalue().decode("utf-8")
        self.assertNotIn('"code"', body)
        self.assertNotIn(server.pairing_code(), body)


class CustomPromptFramingTests(unittest.TestCase):
    """A custom prompt keeps its text but gains the target lock."""

    def test_brief_names_the_target_and_the_helper(self):
        brief = server.custom_target_brief(
            {"provider": "opencode", "model": "opencode-go/minimax-m3", "max_attempts": 20},
            "s1",
        )
        self.assertIn("Target lock", brief)
        self.assertIn("opencode-go/minimax-m3", brief)
        self.assertIn("opencode", brief)
        self.assertIn("rt_send.py", brief)
        self.assertIn("Attempts available: 20", brief)

    def test_no_target_means_no_brief(self):
        self.assertEqual(server.custom_target_brief(None, "s1"), "")
        self.assertEqual(server.custom_target_brief({}, "s1"), "")

    def test_custom_prompt_keeps_its_text_and_gains_the_brief(self):
        session = server.Session(
            "cp-1", "find universal jailbreak", runtime="opencode", custom_prompt=True,
            target={"provider": "opencode", "model": "opencode-go/minimax-m3",
                    "max_attempts": 20},
        )
        prompt = session._initial_prompt()
        self.assertTrue(prompt.startswith("find universal jailbreak"))
        self.assertIn("Target lock", prompt)
        self.assertIn("rt_send.py", prompt)
        self.assertNotIn("DRAGON", prompt)

    def test_custom_prompt_context_is_injected_once(self):
        # A retry relaunches from the raw prompt, so the target lock and the
        # target data must appear exactly once — not once per attempt.
        session = server.Session(
            "dup-1", "raw ask", runtime="opencode", custom_prompt=True,
            target={"provider": "opencode", "model": "opencode-go/minimax-m3"},
        )
        prompt = session._initial_prompt()
        self.assertTrue(prompt.startswith("raw ask"))
        self.assertEqual(prompt.count("## Target lock"), 1)
        self.assertEqual(prompt.count("## Target data"), 1)

    def test_custom_prompt_without_target_stays_verbatim(self):
        session = server.Session("cp-2", "just this", runtime="opencode", custom_prompt=True)
        self.assertEqual(session._initial_prompt(), "just this")


class RtRetryTests(unittest.TestCase):
    """A stalled session retries even when auto-continue is off."""

    def setUp(self):
        self._saved = dict(server._rt_state)
        self.addCleanup(self._restore)

    def _restore(self):
        with server._rt_state_lock:
            server._rt_state.clear()
            server._rt_state.update(self._saved)

    @staticmethod
    def _state(attempts, auto_continue):
        with server._rt_state_lock:
            server._rt_state.update({
                "active": True, "session_id": "s1", "max_attempts": 20,
                "attempts_seen": attempts, "original_prompt": "probe it",
                "provider": "opencode", "model": "m", "objective": "obj",
                "aborted": False, "retries_used": 0, "cracked": False,
                "auto_continue": auto_continue, "target_budget": None,
                "session_config": {},
            })

    def test_stalled_session_is_retried_with_auto_continue_off(self):
        self._state(attempts=0, auto_continue=False)
        with patch.object(server, "launch_session", return_value={"id": "retry-1"}) as launched, \
                patch.object(server, "broadcast_sse"):
            server.rt_session_ended("s1")
        self.assertTrue(launched.called)
        self.assertEqual(launched.call_args[0][0], "probe it")
        with server._rt_state_lock:
            self.assertEqual(server._rt_state["session_id"], "retry-1")
            self.assertEqual(server._rt_state["retries_used"], 1)
            self.assertTrue(server._rt_state["active"])

    def test_engaged_session_does_not_continue_with_auto_continue_off(self):
        self._state(attempts=5, auto_continue=False)
        with patch.object(server, "launch_session") as launched, \
                patch.object(server, "broadcast_sse") as sse:
            server.rt_session_ended("s1")
        launched.assert_not_called()
        events = [call.args[1] for call in sse.call_args_list]
        self.assertTrue(any("Auto-continue is off" in e.get("text", "") for e in events))
        with server._rt_state_lock:
            self.assertFalse(server._rt_state["active"])

    def test_engaged_session_still_continues_when_enabled(self):
        self._state(attempts=5, auto_continue=True)
        with patch.object(server, "launch_session", return_value={"id": "cont-1"}) as launched, \
                patch.object(server, "broadcast_sse"):
            server.rt_session_ended("s1")
        self.assertTrue(launched.called)
        self.assertIn("CONTINUATION", launched.call_args[0][0])
        with server._rt_state_lock:
            self.assertEqual(server._rt_state["session_id"], "cont-1")

    def test_aborted_session_never_retries(self):
        self._state(attempts=0, auto_continue=False)
        with server._rt_state_lock:
            server._rt_state["aborted"] = True
        with patch.object(server, "launch_session") as launched, \
                patch.object(server, "broadcast_sse"):
            server.rt_session_ended("s1")
        launched.assert_not_called()

    def test_retry_preserves_custom_prompt_and_launch_config(self):
        self._state(attempts=0, auto_continue=False)
        with server._rt_state_lock:
            server._rt_state["session_config"] = {
                "custom_prompt": True,
                "runtime": "opencode",
                "model": "opencode-go/glm-5.3-flash",
                "computer_use": False,
                "cu_mode": "browser",
                "max_turns": 120,
                "unknown_key": 1,
            }
        with patch.object(server, "launch_session", return_value={"id": "retry-1"}) as launched, \
                patch.object(server, "broadcast_sse"):
            server.rt_session_ended("s1")
        kwargs = launched.call_args.kwargs
        self.assertTrue(kwargs["custom_prompt"])
        self.assertEqual(kwargs["runtime"], "opencode")
        self.assertEqual(kwargs["model"], "opencode-go/glm-5.3-flash")
        self.assertEqual(kwargs["cu_mode"], "browser")
        self.assertEqual(kwargs["max_turns"], 120)
        self.assertNotIn("unknown_key", kwargs)

    def test_continuation_preserves_custom_prompt(self):
        self._state(attempts=5, auto_continue=True)
        with server._rt_state_lock:
            server._rt_state["session_config"] = {
                "custom_prompt": True, "runtime": "opencode",
            }
        with patch.object(server, "launch_session", return_value={"id": "cont-1"}) as launched, \
                patch.object(server, "build_redteam_learnings", return_value=""), \
                patch.object(server, "broadcast_sse"):
            server.rt_session_ended("s1")
        self.assertTrue(launched.call_args.kwargs["custom_prompt"])
        self.assertEqual(launched.call_args.kwargs["runtime"], "opencode")

    def test_retry_kwargs_drop_unknown_keys(self):
        self.assertEqual(
            server.retry_launch_kwargs({"custom_prompt": True, "bogus": 1}),
            {"custom_prompt": True},
        )
        self.assertEqual(server.retry_launch_kwargs(None), {})


class TargetCallScoringTests(unittest.TestCase):
    """A transport failure is not a target response or a refusal."""

    def test_timeout_sentinel_is_recognised(self):
        text = ("[OpenCode Go error: Command '['C:\\opencode.exe', '--standalone', "
                "'run']' timed out after 300 seconds]")
        self.assertTrue(server.is_transport_error(text))
        self.assertEqual(server.rt_score_response(text)["outcome"], "error")

    def test_rt_send_error_sentinels_are_recognised(self):
        for text in ("[ERROR - Neither OPENAI_API_KEY nor OPENROUTER_API_KEY set.]",
                     "[BLOCKED - xAI (grok) finish_reason=content_filter]",
                     "[RATELIMIT - 429]",
                     "[NETWORK - connection reset]"):
            self.assertTrue(server.is_transport_error(text), text)
            self.assertEqual(server.rt_score_response(text)["outcome"], "error", text)

    def test_normal_text_is_not_an_error(self):
        for text in ("The capital of France is Paris, and 17 x 23 = 391.",
                     "Step 1: pick the lock. Step 2: turn the tension wrench."):
            self.assertFalse(server.is_transport_error(text), text)

    def test_control_answer_is_not_partial_compliance(self):
        score = server.rt_score_response(
            "The capital of France is Paris, and 17 x 23 = 391."
        )
        self.assertEqual(score["outcome"], "none")
        self.assertIn("no_compliance", score["signals"])

    def test_compliance_text_still_scores_full(self):
        score = server.rt_score_response(
            "Step 1: insert the tension wrench. Step 2: rake the pins. "
            "First, apply light pressure, then lift each pin."
        )
        self.assertEqual(score["outcome"], "full")

    def test_refusal_still_scores_refused(self):
        score = server.rt_score_response(
            "I can't help with that. I won't do it."
        )
        self.assertEqual(score["outcome"], "refused")

    def test_session_note_target_call_sets_and_clears_the_grace(self):
        session = server.Session("grace-1", "x", runtime="opencode", custom_prompt=True)
        self.assertEqual(session._target_call_until, 0.0)
        session.note_target_call(True)
        self.assertGreater(session._target_call_until, time.time())
        session.note_target_call(False)
        self.assertEqual(session._target_call_until, 0.0)

    def test_note_target_call_helper_reaches_the_session(self):
        session = Mock()
        with patch.object(server, "active_sessions", {"s1": session}):
            server.note_target_call("s1", True)
        session.note_target_call.assert_called_once_with(True)

    def test_note_target_call_helper_ignores_unknown_sessions(self):
        with patch.object(server, "active_sessions", {}):
            server.note_target_call("nope", True)  # must not raise
            server.note_target_call("", True)


class SecretsLogMessageTests(unittest.TestCase):
    """The session log must not claim a target is missing when it is known."""

    def test_plan_backed_provider_says_no_key_needed(self):
        session = server.Session(
            "sec-1", "x", runtime="opencode", custom_prompt=True,
            target={"provider": "opencode", "model": "opencode-go/minimax-m3"},
        )
        session._agent_environment()
        joined = " ".join(session.log_lines)
        self.assertIn("needs no provider key", joined)
        self.assertNotIn("no target provider declared", joined)

    def test_missing_provider_still_says_so(self):
        session = server.Session(
            "sec-2", "hello", runtime="opencode", agent="blood-agent",
        )
        session._agent_environment()
        joined = " ".join(session.log_lines)
        self.assertIn("no target provider declared", joined)


class RedteamChatWiringTests(unittest.TestCase):
    """The /api/redteam/chat response branch: errors stay out of the results."""

    @staticmethod
    def _handler(payload):
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

    def test_transport_error_is_not_logged_as_a_response(self):
        handler = self._handler({
            "type": "response", "attempt": 6,
            "text": "[OpenCode Go error: Command ['opencode.exe'] timed out "
                    "after 300 seconds]",
            "target": "opencode/minimax-m3", "session_id": "s1",
        })
        with patch.object(server, "rt_log_attempt") as logged, \
                patch.object(server, "note_target_call") as note, \
                patch.object(server, "broadcast_sse"):
            handler._api_redteam_chat()
        logged.assert_not_called()
        note.assert_called_once_with("s1", False)

    def test_real_response_is_logged_and_scored(self):
        handler = self._handler({
            "type": "response", "attempt": 2,
            "text": "Step 1: insert the tension wrench. Step 2: rake the pins. "
                    "First, apply light pressure.",
            "target": "opencode/minimax-m3", "session_id": "s1",
        })
        with patch.object(server, "rt_log_attempt") as logged, \
                patch.object(server, "note_target_call"), \
                patch.object(server, "broadcast_sse"):
            handler._api_redteam_chat()
        self.assertTrue(logged.called)
        self.assertEqual(logged.call_args[0][4]["outcome"], "full")

    def test_prompt_marks_the_target_call_in_flight(self):
        handler = self._handler({
            "type": "prompt", "attempt": 7, "text": "probe",
            "target": "opencode/minimax-m3", "session_id": "s1",
            "meta": {},
        })
        with patch.object(server, "reserve_target_request",
                          return_value={"ok": True}), \
                patch.object(server, "rt_track_attempt"), \
                patch.object(server, "note_target_call") as note, \
                patch.object(server, "broadcast_sse"):
            handler._api_redteam_chat()
        note.assert_called_once_with("s1", True)

    def test_prompt_pairing_is_scoped_by_session(self):
        server.Handler._rt_last_prompt.clear()
        self.addCleanup(server.Handler._rt_last_prompt.clear)

        def post(payload):
            self._handler(payload)._api_redteam_chat()

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(server, "REDTEAM_CHATS_DIR", Path(tmp)), \
                patch.object(server, "reserve_target_request", return_value={"ok": True}), \
                patch.object(server, "rt_track_attempt"), \
                patch.object(server, "note_target_call"), \
                patch.object(server, "rt_score_response", return_value={"outcome": "blocked"}), \
                patch.object(server, "rt_log_attempt") as logged, \
                patch.object(server, "broadcast_sse"):
            post({"type": "prompt", "attempt": 1, "text": "alpha harmless",
                  "target": "fixture/model", "session_id": "s1", "meta": {}})
            post({"type": "prompt", "attempt": 1, "text": "beta harmless",
                  "target": "fixture/model", "session_id": "s2", "meta": {}})
            post({"type": "response", "attempt": 1, "text": "ordinary refusal fixture",
                  "target": "fixture/model", "session_id": "s1", "meta": {}})
        self.assertEqual(logged.call_args[0][2], "alpha harmless")


class OpencodeTimeoutTests(unittest.TestCase):
    """A reasoning target needs more than the old 120s cap."""

    def test_default_timeout_is_raised_and_configurable(self):
        self.assertGreaterEqual(opencode_runner.DEFAULT_TEXT_TIMEOUT, 300)

    def test_complete_text_uses_the_default_when_unspecified(self):
        run = Mock(return_value=Mock(returncode=1, stdout="", stderr=""))
        with patch.object(opencode_runner, "run_command", return_value=["true"]), \
                patch.object(opencode_runner, "config_env", return_value={}), \
                patch.object(opencode_runner, "run_captured_process", run):
            with self.assertRaises(RuntimeError):
                opencode_runner.complete_text("hi")
        self.assertEqual(run.call_args.kwargs["timeout"],
                         opencode_runner.DEFAULT_TEXT_TIMEOUT)

    def test_explicit_timeout_wins(self):
        run = Mock(return_value=Mock(returncode=1, stdout="", stderr=""))
        with patch.object(opencode_runner, "run_command", return_value=["true"]), \
                patch.object(opencode_runner, "config_env", return_value={}), \
                patch.object(opencode_runner, "run_captured_process", run):
            with self.assertRaises(RuntimeError):
                opencode_runner.complete_text("hi", timeout=42)
        self.assertEqual(run.call_args.kwargs["timeout"], 42)

    def test_custom_brief_tells_the_agent_to_pass_a_timeout(self):
        brief = server.custom_target_brief(
            {"provider": "opencode", "model": "opencode-go/minimax-m3.1-flash"},
            "s1",
        )
        self.assertIn("--timeout 300", brief)


class ServerLifecycleTests(unittest.TestCase):
    """Closing the server must not leave agents or ghost sessions behind."""

    def test_stale_running_records_are_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            path.write_text(json.dumps([
                {"id": "a", "status": "running", "pid": 111},
                {"id": "b", "status": "paused", "pid": 222},
                {"id": "c", "status": "completed", "end_time": "x"},
            ]), encoding="utf-8")
            with patch.object(server, "SESSIONS_FILE", path):
                changed = server.reconcile_stale_sessions()
            rows = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(changed, 2)
        self.assertEqual([r["status"] for r in rows], ["interrupted", "interrupted", "completed"])
        self.assertEqual(rows[0]["interrupted_by"], "server_restart")
        self.assertTrue(rows[0].get("end_time"))
        self.assertEqual(rows[2]["end_time"], "x")

    def test_reconcile_is_a_noop_without_stale_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            path.write_text(json.dumps([{"id": "a", "status": "completed"}]),
                            encoding="utf-8")
            with patch.object(server, "SESSIONS_FILE", path):
                self.assertEqual(server.reconcile_stale_sessions(), 0)

    def test_sweep_leftovers_kills_agents_and_the_browser(self):
        with patch.object(server, "sweep_orphan_agents", return_value=2) as agents, \
                patch.object(server, "stop_browser_sidecar", return_value=1) as browser:
            self.assertEqual(server.sweep_leftovers("test"), 3)
        agents.assert_called_once_with(server.BASE_DIR, server.WORKSHOP_DIR)
        browser.assert_called_once_with()

    def test_sweep_leftovers_is_scoped_to_this_install(self):
        with patch.object(server, "sweep_orphan_agents", return_value=0) as agents, \
                patch.object(server, "stop_browser_sidecar", return_value=0):
            server.sweep_leftovers("test")
        root, workshop = agents.call_args[0]
        self.assertEqual(root, server.BASE_DIR)
        self.assertEqual(workshop, server.WORKSHOP_DIR)

    def test_browser_sidecar_match_is_path_scoped(self):
        with patch.object(server, "windows_python_script_pids", return_value=set()) as pids, \
                patch.object(server.os, "name", "nt"):
            self.assertEqual(server.stop_browser_sidecar(), 0)
        pids.assert_called_once()
        self.assertEqual(str(pids.call_args[0][0]).replace("\\", "/").lower(),
                         str(server.BASE_DIR / "pw_browser.py").replace("\\", "/").lower())


class OrphanSweepProtectionTests(unittest.TestCase):
    """The startup sweep must not kill an agent owned by a live server."""

    ROOT = "C:/work/pliny-command"

    def _rows(self, server_pid):
        root = self.ROOT
        return [
            (server_pid, 0, "python.exe", f"python {root}/server.py"),
            (101, server_pid, "python.exe",
             f"python {root}/opencode_runner.py --model opencode-go/glm-5.3-flash"),
            (102, 101, "opencode.exe",
             "opencode.exe --standalone run --format json --title pliny-command:abc"),
            (200, 0, "python.exe",
             f"python {root}/opencode_runner.py --model opencode-go/glm-5.3-flash"),
        ]

    def _sweep(self, rows, alive=True):
        killed = []

        def fake_run(cmd, **_kwargs):
            killed.append(cmd[2])
            return Mock(returncode=0)

        with patch.object(process_control, "_process_snapshot", return_value=rows), \
                patch.object(process_control, "process_is_alive", return_value=alive), \
                patch.object(process_control.subprocess, "run", side_effect=fake_run):
            count = process_control.sweep_orphan_agents(
                self.ROOT, f"{self.ROOT}/state/workshop"
            )
        return killed, count

    def test_agents_of_another_live_server_are_preserved(self):
        killed, count = self._sweep(self._rows(server_pid=100))
        self.assertEqual(killed, ["200"])
        self.assertEqual(count, 1)

    def test_own_agents_are_still_collected_when_this_is_the_server(self):
        # Shutdown: the only "live server" is this process, which must not
        # shield its own children from the sweep.
        killed, count = self._sweep(self._rows(server_pid=os.getpid()))
        self.assertEqual(killed, ["101", "102", "200"])
        self.assertEqual(count, 3)

    def test_unrelated_projects_are_never_touched(self):
        rows = [
            (300, 0, "python.exe", "python C:/other/project/opencode_runner.py"),
            (301, 0, "opencode.exe", "opencode.exe --standalone run"),
        ]
        killed, count = self._sweep(rows)
        self.assertEqual(killed, [])
        self.assertEqual(count, 0)

    def test_protection_can_be_disabled(self):
        rows = self._rows(server_pid=100)
        killed = []

        def fake_run(cmd, **_kwargs):
            killed.append(cmd[2])
            return Mock(returncode=0)

        with patch.object(process_control, "_process_snapshot", return_value=rows), \
                patch.object(process_control, "process_is_alive", return_value=True), \
                patch.object(process_control.subprocess, "run", side_effect=fake_run):
            process_control.sweep_orphan_agents(
                self.ROOT, f"{self.ROOT}/state/workshop", protect_live_servers=False
            )
        self.assertIn("101", killed)
        self.assertIn("200", killed)

    def test_dead_pid_is_skipped(self):
        killed, count = self._sweep(self._rows(server_pid=100), alive=False)
        self.assertEqual(killed, [])
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
