"""Regression tests for the Computer Use security/reliability boundary.

Everything here is mocked: no real input is sent and no real screen is read.
Real-Windows checks live in tests/test_computer_use_windows_live.py.
"""
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from unittest.mock import Mock, patch

from PIL import Image

import cu_capabilities
import agent_permissions
import agent_secrets
import opencode_runner
import pw_browser
import server
import windows_desktop
from computer_use_mcp import ComputerUseTools
from windows_desktop import (ActionCancelled, DesktopFocusChanged, StaleFrame, TargetMismatch,
                             WindowsDesktop)


def _cursor(ptr):
    ptr._obj.x, ptr._obj.y = 50, 50
    return 1


def make_desktop(test, owner="mission-a"):
    control = WindowsDesktop()
    control.user = Mock()
    control._interactive = Mock()
    control._init = Mock()
    control.owner = owner
    control.paused = False
    control.user.GetForegroundWindow.return_value = 123
    control.user.GetCursorPos.side_effect = _cursor
    control.monitor_id = "m1"
    control.monitors = Mock(return_value=[{"id": "m1", "primary": True, "left": 0, "top": 0,
                                           "width": 1280, "height": 800}])
    root = Path(test.enterContext(tempfile.TemporaryDirectory()))
    test.enterContext(patch.object(windows_desktop, "SNAPSHOT_ROOT", root))
    control.snapshot_path = root / str(owner) / "desktop_screen.png"
    control._send = Mock()
    return control


def grab(test, size=(1280, 800)):
    return test.enterContext(patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", size)))


def handler(headers=None, body=None, client=("127.0.0.1", 50000)):
    h = server.Handler.__new__(server.Handler)
    h.headers = {"Host": "127.0.0.1:8888", **(headers or {})}
    h.client_address = client
    h.path = "/api/test"
    h._read_body = Mock(return_value=json.dumps(body or {}).encode())
    h._send_json = Mock()
    return h


def agent_headers(capability=None):
    headers = {"Authorization": "Bearer " + server.AGENT_TOKEN, "Content-Type": "application/json"}
    if capability:
        headers[cu_capabilities.HEADER_NAME] = capability
    return headers


OPERATOR = "operator-test-token-0123456789"


def operator_headers():
    return {"Authorization": "Bearer " + OPERATOR, "Content-Type": "application/json"}


class Base(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(server, "audit_log"))
        self.enterContext(patch.dict(os.environ, {"PLINY_AUTH_TOKEN": OPERATOR}))
        self.registry = cu_capabilities.CapabilityRegistry()
        self.enterContext(patch.object(server, "CU_CAPABILITIES", self.registry))

    def sent(self, h):
        body, status = (list(h._send_json.call_args.args) + [200])[:2]
        return body, status


# ─── Pause / resume privilege ───────────────────────────────────────────────

class PauseResumePrivilegeTests(Base):
    def setUp(self):
        super().setUp()
        self.control = make_desktop(self)
        self.enterContext(patch.object(server, "desktop", self.control))

    def test_agent_token_cannot_resume_operator_pause(self):
        self.control.pause(True)
        capability = self.registry.grant("mission-a", "desktop")
        for headers in (agent_headers(), agent_headers(capability)):
            with self.subTest(with_capability=cu_capabilities.HEADER_NAME in headers):
                h = handler(headers)
                h._api_desktop("resume", mutate=True)
                body, status = self.sent(h)
                self.assertEqual(status, 403)
                self.assertEqual(body["code"], "operator_required")
                self.assertTrue(self.control.operator_paused)
                self.assertTrue(self.control.paused)

    def test_anonymous_local_request_cannot_resume(self):
        self.control.pause(True)
        h = handler({"Content-Type": "application/json"})
        h._api_desktop("resume", mutate=True)
        # Anonymous traffic fails the desktop gate before reaching resume.
        self.assertEqual(self.sent(h)[1], 403)
        self.assertTrue(self.control.operator_paused)

    def test_operator_can_resume_and_agent_can_still_pause(self):
        h = handler(agent_headers())
        h._api_desktop("pause", mutate=True)
        self.assertTrue(self.control.operator_paused)  # ESC hook / agent may stop input
        h = handler(operator_headers())
        h._api_desktop("resume", mutate=True)
        self.assertEqual(self.sent(h)[1], 200)
        self.assertFalse(self.control.operator_paused)

    def test_browser_resume_is_operator_only(self):
        with patch.object(server, "_browser_operator_paused", True), \
                patch.object(server.Handler, "_proxy_pw_browser") as proxy:
            h = handler(agent_headers())
            h._api_browser_resume()
            self.assertEqual(self.sent(h)[1], 403)
            proxy.assert_not_called()
            self.assertTrue(server._browser_operator_paused)
            h = handler(operator_headers())
            h._api_browser_resume()
            proxy.assert_called_once_with("/resume", "POST")
            self.assertFalse(server._browser_operator_paused)

    def test_agent_cannot_resurrect_a_finished_computer_use_mission(self):
        # End mission -> release() clears Take Control -> an agent must not be
        # able to bring the mission back with input unlocked.
        session = server.Session("cu-dead", "x", computer_use=True, cu_mode="desktop", runtime="claude")
        session.status, session.claude_session_id = "completed", "conv"
        with patch.object(server, "active_sessions", {"cu-dead": session}), \
                patch.object(server.Session, "send_message") as send, patch.object(server, "broadcast_sse"), \
                patch.object(server, "post_comms_message"):
            h = handler(agent_headers(), {"session_id": "cu-dead", "message": "keep going"})
            h._api_session_message()
            body, status = self.sent(h)
            self.assertEqual((status, body["code"]), (403, "operator_required"))
            send.assert_not_called()
            self.assertEqual(session.status, "completed")

    def test_agent_cannot_continue_a_paused_computer_use_mission(self):
        session = server.Session("cu-paused", "x", computer_use=True, runtime="claude")
        session.status = "paused"
        with patch.object(server, "active_sessions", {"cu-paused": session}), \
                patch.object(server.Session, "send_message") as send:
            h = handler(agent_headers(), {"session_id": "cu-paused"})
            h._api_session_continue()
            self.assertEqual(self.sent(h)[1], 403)
            send.assert_not_called()

    def test_agent_messages_to_running_mission_still_work(self):
        session = server.Session("cu-live", "x", computer_use=True, runtime="claude")
        session.status = "running"
        with patch.object(server, "active_sessions", {"cu-live": session}), \
                patch.object(server.Session, "send_message") as send, patch.object(server, "post_comms_message"):
            h = handler(agent_headers(), {"session_id": "cu-live", "message": "status?"})
            h._api_session_message()
            send.assert_called_once_with("status?")

    def test_agent_request_cu_is_pending_until_operator_grants(self):
        session = server.Session("asker", "x", runtime="claude")
        session.status = "running"
        with patch.object(server, "active_sessions", {"asker": session}), \
                patch.object(server, "_cu_acquire") as acquire, patch.object(server, "broadcast_sse") as sse:
            h = handler(agent_headers(), {"session_id": "asker", "reason": "need a page"})
            h._api_session_request_cu()
            body, status = self.sent(h)
            self.assertEqual((status, body["pending"]), (202, True))
            acquire.assert_not_called()
            self.assertFalse(session.computer_use)
            self.assertEqual(sse.call_args.args[0], "cu_requested")


# ─── Session-scoped capabilities ────────────────────────────────────────────

class CapabilityTests(Base):
    def test_capability_is_bound_to_session_mode_scope_and_expiry(self):
        token = self.registry.grant("s1", "desktop")
        self.assertEqual(self.registry.check(token, "s1", "desktop", "act")["sid"], "s1")
        cases = [("s2", "desktop", "act", "capability_wrong_session"),
                 ("s1", "browser", "act", "capability_wrong_mode")]
        for sid, mode, scope, code in cases:
            with self.subTest(code=code), self.assertRaises(cu_capabilities.CapabilityError) as caught:
                self.registry.check(token, sid, mode, scope)
            self.assertEqual(caught.exception.code, code)
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with self.assertRaises(cu_capabilities.CapabilityError) as caught:
            self.registry.check(tampered, "s1", "desktop", "act")
        self.assertEqual(caught.exception.code, "capability_invalid")
        expired = cu_capabilities.mint(self.registry.key, "s1", "desktop", ttl=1, now=time.time() - 10)
        with self.assertRaises(cu_capabilities.CapabilityError) as caught:
            cu_capabilities.verify(self.registry.key, expired)
        self.assertEqual(caught.exception.code, "capability_expired")

    def test_revoked_or_regranted_capability_is_refused(self):
        old = self.registry.grant("s1", "desktop")
        self.registry.revoke("s1")
        with self.assertRaises(cu_capabilities.CapabilityError) as caught:
            self.registry.check(old, "s1", "desktop", "act")
        self.assertEqual(caught.exception.code, "capability_revoked")
        self.registry.grant("s1", "desktop")  # new mission epoch, new nonce
        with self.assertRaises(cu_capabilities.CapabilityError):
            self.registry.check(old, "s1", "desktop", "act")

    def _desktop_mission(self, sid="s1"):
        session = server.Session(sid, "x", computer_use=True, cu_mode="desktop", runtime="claude")
        session.status = "running"
        self.enterContext(patch.object(server, "active_sessions", {sid: session}))
        self.enterContext(patch.object(server, "_cu_active_session_id", sid))
        return session

    def test_desktop_actions_need_the_owning_sessions_capability(self):
        self._desktop_mission("s1")
        other = self.registry.grant("s2", "desktop")
        own = self.registry.grant("s1", "desktop")
        with patch.object(server, "desktop_batch_response", return_value={"ok": True, "results": []}) as run:
            for headers, code in ((agent_headers(), "capability_missing"),
                                  (agent_headers(other), "capability_wrong_session")):
                h = handler(headers, {"session_id": "s1", "actions": []})
                h._api_desktop("batch", mutate=True)
                body, status = self.sent(h)
                self.assertEqual((status, body["code"]), (403, code))
            run.assert_not_called()
            h = handler(agent_headers(own), {"session_id": "s1", "actions": []})
            h._api_desktop("batch", mutate=True)
            run.assert_called_once()
            h = handler(operator_headers(), {"session_id": "s1", "actions": []})
            h._api_desktop("batch", mutate=True)  # authorised operator workflow preserved
            self.assertEqual(run.call_count, 2)

    def test_acquire_grants_and_release_revokes(self):
        session = server.Session("s9", "x", computer_use=True, cu_mode="desktop", runtime="claude")
        with patch.object(server, "active_sessions", {"s9": session}), \
                patch.object(server, "_cu_active_session_id", None), \
                patch.object(server, "_sync_browser_owner_async"), patch.object(server, "_cleanup_cu_artifacts"):
            self.assertTrue(server._cu_acquire("s9"))
            token = self.registry.token_for("s9")
            self.assertTrue(token)
            env = session._agent_environment()
            self.assertEqual(env[cu_capabilities.ENV_NAME], token)
            self.assertNotIn("PLINY_CU_SIGNING_KEY", env)
            self.assertNotIn("PLINY_BROWSER_CONTROL_TOKEN", env)
            self.assertNotIn(server.BROWSER_CONTROL_TOKEN, json.dumps(env))
            self.assertNotIn(self.registry.key.hex(), json.dumps(env))
            server._cu_release("s9")
            self.assertEqual(self.registry.token_for("s9"), "")
            with self.assertRaises(cu_capabilities.CapabilityError):
                self.registry.check(token, "s9", "desktop", "act")

    def test_sessions_without_computer_use_get_no_capability(self):
        session = server.Session("plain", "x", runtime="claude")
        self.registry.grant("plain", "browser")
        self.assertNotIn(cu_capabilities.ENV_NAME, session._agent_environment())

    def test_nested_runner_env_carries_the_sessions_capability(self):
        """The grant must survive every scrub between the server and the shell.

        opencode_runner (and codex_runner) rebuild the child environment with
        agent_secrets.filtered_env(). Before this regression test, PLINY_CU_TOKEN
        matched the *TOKEN credential pattern there and was dropped between the
        runner process and the agent's tool shell: the launch audit read
        cu_capability_granted while the first /api/desktop/batch call answered
        capability_missing.
        """
        token = self.registry.grant("s1", "desktop")
        session = server.Session("s1", "x", computer_use=True, cu_mode="desktop", runtime="opencode")
        agent_env = session._agent_environment()
        self.assertEqual(agent_env[cu_capabilities.ENV_NAME], token)
        with patch.dict(os.environ, {**agent_env, "OPENROUTER_API_KEY": "sk-or-fixture"}):
            env = agent_secrets.filtered_env()
            runner_env = opencode_runner.config_env({})
        for name, child in (("filtered_env", env), ("opencode_runner", runner_env)):
            with self.subTest(child=name):
                self.assertEqual(child[cu_capabilities.ENV_NAME], token)
                self.assertNotIn("OPENROUTER_API_KEY", child)

    def test_desktop_frames_are_written_inside_the_workshop(self):
        # The prompt tells the agent to read screenshot_path directly; an
        # %TEMP% path can arrive as an 8.3 alias that OpenCode's
        # external-directory rule does not match, which cost a copy per turn.
        frame_dir = Path(server.desktop.session_dir("s1"))
        self.assertTrue(frame_dir.is_relative_to(server.WORKSHOP_DIR), frame_dir)

    def test_desktop_missions_grant_the_user_content_write_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "cu_user_write_roots", return_value=[Path(tmp)]):
                desktop = server.Session("s1", "x", computer_use=True, cu_mode="desktop", runtime="opencode")
                env = desktop._agent_environment()
                browser = server.Session("s2", "x", computer_use=True, cu_mode="browser", runtime="opencode")
                browser_env = browser._agent_environment()
        self.assertEqual(env[agent_permissions.WRITE_ROOTS_ENV], tmp)
        self.assertNotIn(agent_permissions.WRITE_ROOTS_ENV, browser_env)

    def test_cu_write_roots_can_be_disabled_or_overridden(self):
        with patch.dict(os.environ, {"PLINY_CU_WRITE_ROOTS": "0"}, clear=False):
            self.assertEqual(server.cu_user_write_roots(), [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.dict(os.environ, {agent_permissions.WRITE_ROOTS_ENV: str(root)}, clear=False):
                self.assertEqual(server.cu_user_write_roots(), [root])

    def test_every_runtime_gets_the_inline_screenshot_mcp_tools(self):
        """OpenCode 2.2.7 exposes local MCP tools; the shell path is the fallback.

        The old comment claimed OpenCode could not see MCP tools, so every CU
        step ran a PowerShell (171ms) + HTTP call and then a second read tool
        for the image. A probe tool called by opencode run and an image block
        answered by the model proved otherwise.
        """
        for runtime in ("opencode", "codex", "claude"):
            session = server.Session(f"s-{runtime}", "x", computer_use=True,
                                     cu_mode="desktop", runtime=runtime)
            with self.subTest(runtime=runtime):
                self.assertIn("pliny_cu", session._cu_mcp_servers())
                args = session._runner_mcp_args()
                if runtime == "opencode":
                    self.assertEqual(args[0], "--mcp-json")
                    self.assertIn("computer_use_mcp.py", args[1])

    def test_sessions_without_computer_use_get_no_mcp_tools(self):
        session = server.Session("plain", "x", runtime="opencode")
        self.assertEqual(session._cu_mcp_servers(), {})


# ─── Endpoint robustness ────────────────────────────────────────────────────

class SessionNameEndpointTests(unittest.TestCase):
    def call(self, raw):
        h = server.Handler.__new__(server.Handler)
        h.headers = {"Host": "127.0.0.1:8888"}
        h._read_body = Mock(return_value=raw)
        h._send_json = Mock()
        h._api_session_name()
        return h._send_json.call_args.args

    def test_malformed_json_gets_a_400_instead_of_a_dropped_connection(self):
        # PowerShell mangles inline JSON; the old unguarded decode closed the
        # socket and surfaced as curl (52) "Empty reply from server".
        for raw in (b"{bad", b"[]", b'{"session_id": 5, "title": null}', b""):
            with self.subTest(raw=raw):
                body, status = self.call(raw)
                self.assertEqual(status, 400)
                self.assertIn("error", body)


class DesktopPythonToolTests(unittest.TestCase):
    def test_desktop_py_returns_text_and_only_displayed_images(self):
        tools = ComputerUseTools("desktop", "s1", desktop_python=True)
        worker = Mock()
        worker.run.return_value = {"ok": True, "stdout": "hi\n", "images": ["QQ=="], "error": None}
        tools._pyworker = worker
        content, failed = tools.call("desktop_py", {"code": "print('hi')\ndisplay(shot())"})
        self.assertFalse(failed)
        self.assertIn("hi", content[0]["text"])
        self.assertEqual(content[1], {"type": "image", "data": "QQ==", "mimeType": "image/jpeg"})
        self.assertEqual(worker.run.call_args.args[1], 60000)

    def test_desktop_py_rejects_empty_code(self):
        tools = ComputerUseTools("desktop", "s1", desktop_python=True)
        with self.assertRaises(ValueError):
            tools.call("desktop_py", {"code": "   "})

    def test_desktop_py_is_desktop_only(self):
        tools = ComputerUseTools("browser", "s1", desktop_python=True)
        with self.assertRaises(ValueError):
            tools.call("desktop_py", {"code": "print(1)"})


class WindowListBudgetTests(unittest.TestCase):
    def test_window_lists_are_capped_and_titles_trimmed(self):
        session = Mock()
        session.session_id = "s1"
        windows = [{"id": i, "title": "T" * 200, "pid": 1, "foreground": i == 0}
                   for i in range(30)]
        with patch.object(server.desktop, "windows", return_value=windows), \
                patch.object(server.desktop, "foreground", return_value={"id": 0, "title": "T"}):
            outcome = server.desktop_batch_response(
                session, {"actions": [], "windows": True, "screenshot": False})
        self.assertEqual(len(outcome["windows"]), 24)
        self.assertEqual(outcome["windows_truncated"], 6)
        self.assertEqual(len(outcome["windows"][0]["title"]), 80)


# ─── Browser sidecar access control ─────────────────────────────────────────

class SidecarAuthTests(unittest.TestCase):
    CONTROL = "control-token-for-tests"

    def setUp(self):
        self.key = cu_capabilities.new_key()
        self.enterContext(patch.object(pw_browser, "CONTROL_TOKEN", self.CONTROL))
        self.enterContext(patch.object(pw_browser, "SIGNING_KEY", self.key))
        self.enterContext(patch.object(pw_browser, "AUTH_DISABLED", False))
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(pw_browser, "SS_PATH", str(self.tmp / "pliny_pw_screen.png")))
        self.enterContext(patch.object(pw_browser, "_owner", None))
        self.cap = cu_capabilities.mint(self.key, "owner-1", "browser", nonce="n1")
        pw_browser.set_owner({"session_id": "owner-1", "nonce": "n1"})

    def check(self, headers, method="GET", path="/status"):
        return pw_browser.authorize({"Host": "127.0.0.1:8787", **headers}, method, path)

    def test_header_rules(self):
        bearer = lambda t: {"Authorization": "Bearer " + t}
        self.assertEqual(self.check({})[0], 401)
        self.assertEqual(self.check(bearer(self.cap)), (None, "agent"))
        self.assertEqual(self.check(bearer(self.CONTROL)), (None, "control"))
        # Cross-origin / rebinding, even with a valid credential.
        self.assertEqual(self.check({**bearer(self.cap), "Origin": "https://evil.test"})[0], 403)
        self.assertEqual(self.check({**bearer(self.cap), "Origin": "http://127.0.0.1:8787"})[0], 403)
        self.assertEqual(self.check({**bearer(self.cap), "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(pw_browser.authorize({"Host": "evil.test:8787", **bearer(self.cap)}, "GET", "/status")[0], 403)
        # Agents cannot lift a pause or move ownership; they may pause.
        for path in ("/resume", "/control/owner"):
            status, body = self.check(bearer(self.cap), "POST", path)
            self.assertEqual((status, body["code"]), (403, "operator_required"))
        self.assertEqual(self.check(bearer(self.cap), "POST", "/pause"), (None, "agent"))

    def test_other_or_finished_mission_capability_is_refused(self):
        other = cu_capabilities.mint(self.key, "owner-2", "browser", nonce="n2")
        stale = cu_capabilities.mint(self.key, "owner-1", "browser", nonce="old")
        desktop_cap = cu_capabilities.mint(self.key, "owner-1", "desktop", nonce="n1")
        forged = cu_capabilities.mint(cu_capabilities.new_key(), "owner-1", "browser", nonce="n1")
        for token, code in ((other, "capability_revoked"), (stale, "capability_revoked"),
                            (desktop_cap, "capability_wrong_mode"), (forged, "capability_invalid")):
            status, body = self.check({"Authorization": "Bearer " + token})
            self.assertEqual((status, body["code"]), (403, code))

    def test_owner_change_drops_retained_screenshot(self):
        shot = Path(pw_browser.SS_PATH)
        shot.write_bytes(b"previous mission's page")
        pw_browser.set_owner({"session_id": "owner-1", "nonce": "n1-refresh"})
        self.assertTrue(shot.exists())  # same mission refreshing its grant
        pw_browser.set_owner({"session_id": "owner-2", "nonce": "n2"})
        self.assertFalse(shot.exists())
        status, _ = self.check({"Authorization": "Bearer " + self.cap})
        self.assertEqual(status, 403)

    def test_direct_socket_access_to_the_sidecar_port(self):
        # A real HTTP listener with the sidecar handler: this is what any
        # local process (or a rebinding web page) reaching :8787 talks to.
        listener = HTTPServer(("127.0.0.1", 0), pw_browser.Handler)
        port = listener.server_address[1]
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(listener.server_close)
        self.addCleanup(listener.shutdown)

        def call(method, path, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(method, path, body=b"{}" if method == "POST" else None, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
            conn.close()
            return response.status, json.loads(body or b"{}"), response.getheader("Access-Control-Allow-Origin")

        with patch.object(pw_browser, "_paused", True):
            self.assertEqual(call("GET", "/status")[0], 401)
            self.assertEqual(call("GET", "/screenshot/base64")[0], 401)
            self.assertEqual(call("GET", "/cookies")[0], 401)
            status, body, cors = call("GET", "/status", {"Authorization": "Bearer " + self.cap, "Origin": "https://evil.test"})
            self.assertEqual((status, cors), (403, None))
            self.assertEqual(call("OPTIONS", "/navigate", {"Origin": "https://evil.test"})[0], 403)
            self.assertEqual(call("POST", "/resume", {"Authorization": "Bearer " + self.cap})[0], 403)
            self.assertTrue(pw_browser._paused)
            self.assertEqual(call("GET", "/status", {"Authorization": "Bearer " + self.cap})[0], 200)
            self.assertEqual(call("POST", "/resume", {"Authorization": "Bearer " + self.CONTROL})[0], 200)
            self.assertFalse(pw_browser._paused)

    def test_server_talks_to_sidecar_with_its_control_token_only(self):
        with patch.object(server.urllib.request, "urlopen") as urlopen:
            server.browser_sidecar_request("/status")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + server.BROWSER_CONTROL_TOKEN)
        self.assertTrue(request.full_url.startswith("http://127.0.0.1:8787/"))

    def test_prompts_get_the_capability_header_once(self):
        prompt = ("curl -s http://localhost:8787/navigate -d '{\"url\":\"x\"}'\n"
                  "curl -s http://localhost:8888/api/status\n"
                  "Invoke-RestMethod -Uri 'http://localhost:8787/status'\n")
        once = server.browser_auth_prompt(prompt)
        self.assertEqual(server.browser_auth_prompt(once), once)
        lines = once.splitlines()
        self.assertIn('curl -H "Authorization: Bearer $PLINY_CU_TOKEN" -s http://localhost:8787/navigate', lines[0])
        self.assertNotIn("PLINY_CU_TOKEN", lines[1])
        self.assertIn("$env:PLINY_CU_TOKEN", lines[2])


# ─── Screenshots never cross missions ───────────────────────────────────────

class CrossSessionCaptureTests(Base):
    def test_release_deletes_captures_and_next_mission_starts_blank(self):
        control = make_desktop(self, owner=None)
        grab(self)
        control.enable("mission-a", "m1")
        control.capture()
        a_dir = control.snapshot_path.parent
        self.assertTrue(control.snapshot_path.exists())
        self.assertIn("mission-a", str(a_dir))
        control.release("mission-a")
        self.assertFalse(a_dir.exists())
        self.assertEqual((control.last_image, control.frame, len(control.frames), control.owner), (b"", None, 0, None))
        control.enable("mission-b", "m1")
        self.assertIsNone(control.frame)
        self.assertNotEqual(control.snapshot_path.parent, a_dir)

    def test_release_by_another_session_is_ignored(self):
        control = make_desktop(self, owner="mission-a")
        self.assertFalse(control.release("mission-b"))
        self.assertEqual(control.owner, "mission-a")

    def test_screenshot_endpoint_never_serves_a_finished_missions_frame(self):
        control = make_desktop(self, owner=None)
        control.last_image = b"\x89PNG old mission"
        self.enterContext(patch.object(server, "desktop", control))
        h = handler(operator_headers())
        h._api_desktop("screenshot")
        self.assertEqual(self.sent(h)[1], 409)

    def test_agent_cannot_read_another_missions_live_screen(self):
        control = make_desktop(self, owner="mission-b")
        self.enterContext(patch.object(server, "desktop", control))
        owner = server.Session("mission-b", "x", computer_use=True, cu_mode="desktop", runtime="claude")
        owner.status = "running"
        self.enterContext(patch.object(server, "active_sessions", {"mission-b": owner}))
        foreign = self.registry.grant("mission-a", "desktop")
        control.screenshot = Mock(return_value=b"secret pixels")
        for action in ("screenshot", "windows"):
            h = handler(agent_headers(foreign))
            h._api_desktop(action)
            body, status = self.sent(h)
            self.assertEqual((status, body["code"]), (403, "capability_wrong_session"))
        control.screenshot.assert_not_called()

    def test_screen_events_reach_only_operator_streams(self):
        operator, agent = io.BytesIO(), io.BytesIO()
        clients = [{"wfile": operator, "desktop_allowed": True}, {"wfile": agent, "desktop_allowed": False}]
        with patch.object(server, "sse_clients", clients):
            server.broadcast_sse("cu_screenshot", {"cu_mode": "browser", "session_id": "x", "data": "page-pixels"})
        self.assertIn(b"page-pixels", operator.getvalue())
        self.assertEqual(agent.getvalue(), b"")
        h = handler(agent_headers())
        self.assertFalse(server.desktop_request_allowed(h) and server._check_operator_auth(h))

    def test_release_cleans_shared_temp_captures(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        leftovers = [tmp / "pliny_pw_screen.png", tmp / "pliny_cu_latest.png"]
        for path in leftovers:
            path.write_bytes(b"x")
        with patch.object(server, "cu_screenshot_paths", return_value=leftovers), \
                patch.object(server, "_cu_active_session_id", "gone"), \
                patch.object(server, "_sync_browser_owner_async"):
            server._cu_release("gone")
        self.assertFalse(any(p.exists() for p in leftovers))


# ─── Stale frames ───────────────────────────────────────────────────────────

class StaleFrameTests(unittest.TestCase):
    def setUp(self):
        self.control = make_desktop(self)
        grab(self)
        self.control.capture()
        self.frame_id = self.control.frame["frame_id"]

    def click(self, **extra):
        return self.control.action("click", {"x": 10, "y": 10, **extra}, "mission-a")

    def test_current_frame_is_accepted(self):
        self.assertEqual(self.click(frame_id=self.frame_id), {"ok": True})

    def test_unknown_frame_id_is_stale(self):
        with self.assertRaises(StaleFrame):
            self.click(frame_id="f999-dead")
        self.control._send.assert_not_called()

    def test_monitor_geometry_change_is_stale(self):
        self.control.monitors.return_value = [{"id": "m1", "primary": True, "left": 0, "top": 0, "width": 1920, "height": 1080}]
        with self.assertRaises(StaleFrame):
            self.click(frame_id=self.frame_id)
        with self.assertRaises(StaleFrame):
            self.click()  # also without an explicit frame id
        self.control._send.assert_not_called()

    def test_target_window_moved_since_frame_is_stale(self):
        rect = {"left": 100}
        def window_rect(hwnd, ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = rect["left"], 100, 500, 500
            return 1
        self.control.user.GetWindowRect.side_effect = window_rect
        self.control.user.GetAncestor.return_value = 123
        self.control.user.WindowFromPoint.return_value = 123
        self.control.target_window = 123
        self.control.capture()
        frame_id = self.control.frame["frame_id"]
        self.assertEqual(self.click(frame_id=frame_id), {"ok": True})
        rect["left"] = 300
        with self.assertRaises(StaleFrame):
            self.click(frame_id=frame_id)

    def test_old_frame_is_stale(self):
        # Negative limit: Windows' monotonic clock can report 0 elapsed time.
        with patch.object(windows_desktop, "FRAME_MAX_AGE_S", -1.0):
            with self.assertRaises(StaleFrame):
                self.click(frame_id=self.frame_id)

    def test_observer_captures_do_not_evict_the_agents_frame(self):
        for _ in range(windows_desktop.FRAME_HISTORY + 4):
            self.control.screenshot()  # dashboard poller
        self.assertEqual(self.click(frame_id=self.frame_id), {"ok": True})

    def test_batch_reports_stale_frame_with_recovery(self):
        result = self.control.batch([{"action": "click", "x": 1, "y": 1, "frame_id": "nope"}], "mission-a", settle_ms=0)
        entry = result["results"][0]
        self.assertEqual(entry["code"], "stale_frame")
        self.assertIn("fresh screenshot", entry["recovery"])

    def test_mcp_pins_pointer_steps_to_the_frame_the_agent_saw(self):
        tools = ComputerUseTools("desktop", "mission-a")
        replies = [(200, json.dumps({"ok": True, "results": [], "image": "QQ==", "frame": {"frame_id": "f7-abc"}}).encode(), "application/json"),
                   (200, json.dumps({"ok": True, "results": []}).encode(), "application/json")]
        tools._request = Mock(side_effect=replies)
        tools.call("desktop_screen", {})
        tools.call("desktop_act", {"actions": [{"action": "click", "x": 1, "y": 1}, {"action": "type", "text": "a"},
                                               {"action": "move", "x": 2, "y": 2, "frame_id": "explicit"}]})
        steps = tools._request.call_args.args[1]["actions"]
        self.assertEqual(steps[0]["frame_id"], "f7-abc")
        self.assertNotIn("frame_id", steps[1])
        self.assertEqual(steps[2]["frame_id"], "explicit")

    def test_mcp_sends_capability_to_server_and_sidecar(self):
        tools = ComputerUseTools("browser", "s1")
        with patch.dict(os.environ, {"PLINY_CU_TOKEN": "cap-value", "PLINY_AGENT_TOKEN": "agent-value"}), \
                patch("computer_use_mcp.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
            tools._request(tools.browser + "/status")
            browser_request = urlopen.call_args.args[0]
            tools._request(tools.server + "/api/desktop/batch", {})
            server_request = urlopen.call_args.args[0]
        self.assertEqual(browser_request.get_header("Authorization"), "Bearer cap-value")
        self.assertEqual(server_request.get_header("Authorization"), "Bearer agent-value")
        self.assertEqual(server_request.get_header("X-pliny-cu-token"), "cap-value")


# ─── Focus / target validation ──────────────────────────────────────────────

class FocusValidationTests(unittest.TestCase):
    def setUp(self):
        self.control = make_desktop(self)
        grab(self)
        self.control.capture()
        self.control.target_window = 123
        self.control.user.WindowFromPoint.return_value = 123
        self.control.user.GetAncestor.side_effect = lambda hwnd, rel: hwnd

    def test_click_requires_target_in_foreground(self):
        self.control.user.GetForegroundWindow.return_value = 999
        with self.assertRaises(DesktopFocusChanged):
            self.control.action("click", {"x": 5, "y": 5}, "mission-a")
        self.control._send.assert_not_called()
        self.control.user.SetCursorPos.assert_not_called()

    def test_click_on_a_covering_window_is_target_mismatch(self):
        self.control.user.WindowFromPoint.return_value = 555
        for action in ("click", "scroll", "drag"):
            with self.subTest(action=action), self.assertRaises(TargetMismatch):
                self.control.action(action, {"x": 5, "y": 5, "to_x": 9, "to_y": 9}, "mission-a")
        self.control._send.assert_not_called()

    def test_dialog_owned_by_target_is_accepted(self):
        # Save As dialog (hwnd 777) owned by Notepad (123).
        self.control.user.GetForegroundWindow.return_value = 777
        self.control.user.WindowFromPoint.return_value = 777
        self.control.user.GetAncestor.side_effect = lambda hwnd, rel: 123 if (hwnd == 777 and rel == 3) else hwnd
        self.assertEqual(self.control.action("type", {"text": "a.txt"}, "mission-a"), {"ok": True})
        self.assertEqual(self.control.action("click", {"x": 5, "y": 5}, "mission-a"), {"ok": True})

    def test_clear_target_allows_a_deliberate_outside_click(self):
        self.control.user.WindowFromPoint.return_value = 555
        self.control.action("clear_target", {}, "mission-a")
        self.assertEqual(self.control.action("click", {"x": 5, "y": 5}, "mission-a"), {"ok": True})
        with self.assertRaises(ValueError):
            self.control.action("type", {"text": "x"}, "mission-a")  # keyboard still needs a target

    def test_focus_change_mid_typing_stops_input(self):
        foreground = iter([123, 123] + [999] * 1000)
        self.control.user.GetForegroundWindow.side_effect = lambda: next(foreground)
        result = self.control.batch([{"action": "type", "text": "x" * 200}], "mission-a", settle_ms=0)
        entry = result["results"][0]
        self.assertEqual(entry["code"], "focus_changed")
        self.assertEqual(entry["progress"]["total_units"], 200)
        self.assertLess(entry["progress"]["sent_units"], 200)


# ─── Cancellation and bounded execution ─────────────────────────────────────

class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.control = make_desktop(self)
        self.control.target_window = 123

    def test_escape_during_typing_stops_within_one_unit(self):
        sent = []
        def send(**kwargs):
            sent.append(kwargs)
            if len(sent) == 20:
                self.control.emergency_pause()  # ESC hook fires mid-input
        self.control._send.side_effect = send
        result = self.control.batch([{"action": "type", "text": "y" * 500},
                                     {"action": "press", "key": "Enter"}], "mission-a", settle_ms=0)
        entry = result["results"][0]
        self.assertEqual(entry["code"], "desktop_paused")
        self.assertEqual(entry["progress"]["sent_units"], 10)
        self.assertEqual(len(sent), 20)  # nothing after the pause, Enter never pressed
        with self.assertRaises(PermissionError):
            self.control.action("press", {"key": "Enter"}, "mission-a")

    def test_threaded_escape_interrupts_a_long_wait_quickly(self):
        timer = threading.Timer(0.1, self.control.emergency_pause)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = self.control.batch([{"action": "wait", "ms": 5000}, {"action": "press", "key": "a"}], "mission-a", settle_ms=0)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(result["results"][0]["code"], "desktop_paused")
        self.control._send.assert_not_called()

    def test_threaded_escape_during_real_paced_typing(self):
        self.control._send.side_effect = lambda **_: time.sleep(0.001)
        timer = threading.Timer(0.05, self.control.emergency_pause)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.control.batch([{"action": "type", "text": "z" * 5000}], "mission-a", settle_ms=0)
        progress = result["results"][0]["progress"]
        self.assertLess(progress["sent_units"], 5000)
        self.assertTrue(self.control.operator_paused)

    def test_mission_stop_cancels_input_and_reports_cancelled(self):
        def send(**kwargs):
            if self.control._send.call_count == 6:
                self.control.cancelled.set()  # mission stop, operator did not pause
        self.control._send.side_effect = send
        self.control.paused = False
        result = self.control.batch([{"action": "type", "text": "abcdefgh"}], "mission-a", settle_ms=0)
        self.assertEqual(result["results"][0]["code"], "cancelled")

    def test_held_modifiers_are_released_when_cancelled_mid_chord(self):
        def send(keyboard=None, mouse=None):
            if keyboard == (0x11, 0, 0):
                self.control.emergency_pause()
        self.control._send.side_effect = send
        with self.assertRaises(PermissionError):
            self.control.action("press", {"key": "Ctrl+Shift+S"}, "mission-a")
        keys = [c.kwargs["keyboard"] for c in self.control._send.call_args_list]
        self.assertEqual(keys, [(0x11, 0, 0), (0x11, 0, 2)])  # Ctrl down, then Ctrl up; nothing else

    def test_batch_deadline_is_enforced(self):
        result = self.control.batch([{"action": "wait", "ms": 300}, {"action": "press", "key": "a"}],
                                    "mission-a", settle_ms=0, timeout_ms=50)
        self.assertEqual(result["results"][0]["code"], "deadline_exceeded")
        self.control._send.assert_not_called()
        with self.assertRaises(ValueError):
            self.control.batch([], "mission-a", timeout_ms=10 ** 9)

    def test_server_maps_codes_to_status_and_recovery(self):
        for error, status, code in ((StaleFrame("old"), 409, "stale_frame"), (TargetMismatch("x"), 409, "target_mismatch"),
                                    (ActionCancelled("x"), 409, "cancelled"), (TimeoutError("x"), 408, "deadline_exceeded")):
            with self.subTest(code=code):
                session = Mock(status="running", computer_use=True, cu_mode="desktop")
                h = handler(operator_headers(), {"session_id": "t", "window_id": 1, "key": "a"})
                with patch.dict(os.environ, {"PLINY_AUTH_TOKEN": OPERATOR}), patch.object(server, "audit_log"), \
                        patch.object(server, "active_sessions", {"t": session}), patch.object(server, "_cu_active_session_id", "t"), \
                        patch.object(server.desktop, "paused", False), patch.object(server.desktop, "action", side_effect=error):
                    h._api_desktop("press", mutate=True)
                body, actual = h._send_json.call_args.args
                self.assertEqual((actual, body["code"]), (status, code))
                self.assertTrue(body["recovery"])


if __name__ == "__main__":
    unittest.main()
