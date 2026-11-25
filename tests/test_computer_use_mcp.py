import io
import json
import os
import subprocess
import unittest
from unittest.mock import ANY, Mock, patch
import urllib.error

import server
from codex_runner import codex_command
from computer_use_mcp import ComputerUseTools, handle, mcp_server_spec, serve
from opencode_runner import config_env, mcp_config
from windows_desktop import DesktopFocusChanged, WindowsDesktop


class DesktopBatchTests(unittest.TestCase):
    def controller(self):
        control = WindowsDesktop()
        control.action = Mock(return_value={"ok": True})
        return control

    def test_steps_run_in_order_with_aliases(self):
        control = self.controller()
        result = control.batch([{"action": "double_click", "x": 1, "y": 2},
                                {"action": "key", "key": "Enter"},
                                {"action": "wait", "ms": 0}], "s1", settle_ms=0)
        self.assertTrue(result["ok"])
        self.assertEqual([r["action"] for r in result["results"]], ["click", "press", "wait"])
        control.action.assert_any_call("click", {"count": 2, "x": 1, "y": 2}, "s1", deadline=ANY)
        control.action.assert_any_call("press", {"key": "Enter"}, "s1", deadline=ANY)

    def test_stops_at_first_failure_with_recovery_code(self):
        control = self.controller()
        control.action.side_effect = [{"ok": True}, DesktopFocusChanged("lost focus"), {"ok": True}]
        steps = [{"action": "click", "x": 1, "y": 1}, {"action": "type", "text": "a"}, {"action": "press", "key": "Enter"}]
        result = control.batch(steps, "s1", settle_ms=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][-1]["code"], "focus_changed")
        self.assertEqual(control.action.call_count, 2)

    def test_rejects_oversized_or_malformed_batches(self):
        control = self.controller()
        with self.assertRaises(ValueError):
            control.batch([{"action": "move", "x": 1, "y": 1}] * 41, "s1")
        with self.assertRaises(ValueError):
            control.batch([{"x": 1}], "s1")

    def test_keyboard_defaults_to_last_activated_window(self):
        control = WindowsDesktop()
        control.user = Mock()
        control._interactive = Mock()
        control.owner, control.paused = "s1", False
        control.user.GetForegroundWindow.return_value = 77
        def cursor(ptr):
            ptr._obj.x, ptr._obj.y = 50, 50
        control.user.GetCursorPos.side_effect = cursor
        control._send = Mock()
        with self.assertRaises(ValueError):
            control.action("press", {"key": "Enter"}, "s1")
        control.target_window = 77
        self.assertEqual(control.action("press", {"key": "Enter"}, "s1"), {"ok": True})

    def test_release_locks_input_without_reporting_operator_pause(self):
        control = WindowsDesktop()
        control.monitors = Mock(return_value=[])
        control._init = Mock()
        control.operator_paused = control.paused = True
        control.release()
        status = control.status()
        self.assertFalse(status["paused"])
        self.assertTrue(status["input_locked"])


class InlineImageTests(unittest.TestCase):
    def test_log_lines_drop_base64_but_keep_structure(self):
        line = json.dumps({"type": "image", "source": {"type": "base64", "data": "A" * 30000}})
        cleaned = json.loads(server.strip_inline_images(line))
        self.assertEqual(cleaned["source"]["data"], "[image omitted]")


class McpProtocolTests(unittest.TestCase):
    def test_initialize_list_and_call_over_stdio(self):
        tools = ComputerUseTools("desktop", "s1")
        tools._request = Mock(return_value=(200, json.dumps({"ok": True, "results": [], "image": "QUJD"}).encode(), "application/json"))
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "desktop_act", "arguments": {"actions": [{"action": "press", "key": "Enter"}]}}}]
        out = io.StringIO()
        serve(tools, io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual({t["name"] for t in replies[1]["result"]["tools"]}, {"desktop_screen", "desktop_act"})
        content = replies[2]["result"]["content"]
        self.assertEqual(content[1], {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"})
        self.assertFalse(replies[2]["result"]["isError"])
        url, payload = tools._request.call_args[0]
        self.assertTrue(url.startswith("http://127.0.0.1:8888/api/desktop/batch"))
        self.assertEqual(payload["session_id"], "s1")
        self.assertTrue(payload["inline_image"])

    def test_tools_are_scoped_to_the_mission_mode(self):
        result = handle(ComputerUseTools("browser", "s1"), {"id": 1, "method": "tools/call",
                        "params": {"name": "desktop_act", "arguments": {"actions": []}}})
        self.assertTrue(result["isError"])

    @unittest.skipUnless(os.name == "nt", "Windows Desktop only")
    def test_desktop_python_is_listed_and_runs_only_for_opted_in_sessions(self):
        for options, expected in (({}, False), ({"cu_desktop_python": False}, False),
                                  ({"cu_desktop_python": "true"}, False), ({"cu_desktop_python": True}, True)):
            with self.subTest(options=options):
                session = server.Session("s1", "Test", computer_use=True, cu_mode="desktop", **options)
                spec = session._cu_mcp_servers()["pliny_cu"]
                requests = [
                    {"id": 1, "method": "tools/list"},
                    {"id": 2, "method": "tools/call",
                     "params": {"name": "desktop_py", "arguments": {"code": "print(6 * 7)"}}},
                ]
                result = subprocess.run(
                    [spec["command"], *spec["args"]],
                    input="".join(json.dumps(request) + "\n" for request in requests),
                    capture_output=True, text=True, encoding="utf-8", check=True, timeout=15,
                )
                listing, call = [json.loads(line)["result"] for line in result.stdout.splitlines()]
                names = {tool["name"] for tool in listing["tools"]}
                self.assertEqual(names, {"desktop_screen", "desktop_act"} | ({"desktop_py"} if expected else set()))
                self.assertEqual(call["isError"], not expected)
                if expected:
                    self.assertEqual(json.loads(call["content"][0]["text"])["stdout"], "42\n")
                self.assertEqual("desktop_py" in server.desktop_instructions("s1", desktop_python=expected), expected)

    def test_disabled_desktop_python_does_not_start_a_worker(self):
        tools = ComputerUseTools("desktop", "s1")
        with patch.object(tools, "python_worker") as worker, self.assertRaises(ValueError):
            tools.call("desktop_py", {"code": "print(42)"})
        worker.assert_not_called()

    def test_browser_rejects_unlisted_endpoints(self):
        tools = ComputerUseTools("browser", "s1")
        tools._request = Mock()
        content, failed = tools._browser([{"endpoint": "tweet", "body": {"text": "x"}}], screenshot=False)
        self.assertTrue(failed)
        tools._request.assert_not_called()

    def test_browser_supports_cloudflare_and_cookie_health_as_get(self):
        tools = ComputerUseTools("browser", "s1")
        tools._request = Mock(return_value=(200, b'{"ok":true}', "application/json"))
        content, failed = tools._browser(
            [{"endpoint": "cookie_health"}, {"endpoint": "cloudflare"}],
            screenshot=False,
        )
        self.assertFalse(failed)
        self.assertEqual(tools._request.call_args_list[0].args, ("http://127.0.0.1:8787/cookie_health", None))
        self.assertEqual(tools._request.call_args_list[1].args, ("http://127.0.0.1:8787/cloudflare", None))

    def test_request_unreachable_returns_503_json(self):
        tools = ComputerUseTools("desktop", "s1")
        with patch("computer_use_mcp.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            status, body, kind = tools._request("http://127.0.0.1:8888/api/desktop/batch", {"actions": []})
        self.assertEqual(status, 503)
        self.assertEqual(kind, "application/json")
        self.assertIn("Service unreachable", json.loads(body)["error"])


class RuntimeWiringTests(unittest.TestCase):
    def test_codex_and_opencode_receive_the_same_server(self):
        spec = mcp_server_spec("desktop", "s1")
        with patch("codex_runner.shutil.which", return_value="codex.exe"):
            command = codex_command(mcp={"pliny_cu": spec})
        self.assertLess(command.index("mcp_servers.pliny_cu.command=" + json.dumps(spec["command"])), command.index("exec"))
        self.assertIn('mcp_servers.pliny_cu.default_tools_approval_mode="approve"', command)
        config = mcp_config({"pliny_cu": spec})["mcp"]["pliny_cu"]
        self.assertEqual(config["command"], [spec["command"], *spec["args"]])
        self.assertFalse(config["codemode"])
        with patch.dict(os.environ, {"PLINY_AGENT_PERMISSIONS": "workshop"}):
            merged = json.loads(config_env({"permission": "deny"}, {})["OPENCODE_CONFIG_CONTENT"])
        # An explicit permission value overrides the workshop profile; the
        # profile's hard-deny policies must survive the override.
        self.assertEqual(merged["permission"], "deny")
        self.assertTrue(merged["policies"])
        self.assertIn({"action": "tool.use", "resource": "shell:*Stop-Process*", "effect": "deny"},
                      merged["policies"])

    def test_only_computer_use_sessions_get_the_tools(self):
        session = server.Session("s1", "x", runtime="codex", computer_use=True, cu_mode="desktop")
        command = session._codex_command()
        self.assertEqual(json.loads(command[command.index("--mcp-json") + 1])["pliny_cu"]["args"][-3:], ["s1", "--port", str(server.PORT)])
        # OpenCode 2.2.7 exposes the same local stdio MCP tools as Codex.
        opencode = server.Session("s4", "x", runtime="opencode", computer_use=True, cu_mode="desktop")
        opencode_command = opencode._opencode_command()
        self.assertEqual(json.loads(opencode_command[opencode_command.index("--mcp-json") + 1]),
                         {"pliny_cu": mcp_server_spec("desktop", "s4", server.PORT)})
        instructions = server.desktop_instructions("s4", mcp_tools=False)
        self.assertIn("/api/desktop/batch", instructions)
        self.assertIn("PLINY_AGENT_TOKEN", instructions)
        self.assertNotIn("pliny_cu", instructions)
        plain = server.Session("s2", "x", runtime="codex")
        self.assertNotIn("--mcp-json", plain._codex_command())
        plain_opencode = server.Session("s5", "x", runtime="opencode")
        self.assertNotIn("--mcp-json", plain_opencode._opencode_command())
        claude = server.Session("s3", "x", runtime="claude", computer_use=True)
        self.assertIn("browser", claude._claude_mcp_args()[1])


if __name__ == "__main__":
    unittest.main()
