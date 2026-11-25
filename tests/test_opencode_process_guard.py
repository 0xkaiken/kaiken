"""Exercise the real Windows CLI against a local fake model, without a seat."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import opencode_runner


class ProcessGuardTests(unittest.TestCase):
    def test_custom_config_cannot_remove_workshop_process_denials(self):
        with patch.dict(os.environ, {"PLINY_AGENT_PERMISSIONS": "workshop"}):
            env = opencode_runner.config_env({"policies": []})
        rules = json.loads(env["OPENCODE_CONFIG_CONTENT"])["policies"]
        self.assertTrue(any(r["action"] == "tool.use" and "Stop-Process" in r["resource"]
                            and r["effect"] == "deny" for r in rules))

    @unittest.skipUnless(os.name == "nt", "Windows process-termination command")
    def test_real_cli_denies_a_model_requested_stop_process(self):
        try:
            opencode_runner.opencode_cli()
        except FileNotFoundError:
            self.skipTest("OpenCode CLI is not installed")
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        request_count = []

        class FakeModel(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request_count.append(self.path)
                commands = [f"Stop-Process -Id {victim.pid} -Force",
                            f"taskkill.exe /PID {victim.pid} /F", "Write-Output GUARD_SHELL_OK"]
                if len(request_count) <= len(commands):
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": f"call_guard_{len(request_count)}", "type": "function",
                        "function": {"name": "shell", "arguments": json.dumps({
                            "command": commands[len(request_count) - 1]})}}]}
                    finish = "tool_calls"
                else:
                    delta = {"role": "assistant", "content": "GUARD_TEST_COMPLETE"}
                    finish = "stop"
                chunks = []
                for change, reason in [(delta, None), ({}, finish)]:
                    chunks.append("data: " + json.dumps({
                        "id": "chatcmpl-guard", "object": "chat.completion.chunk",
                        "created": int(time.time()), "model": "fake",
                        "choices": [{"index": 0, "delta": change, "finish_reason": reason}],
                    }) + "\n\n")
                body = ("".join(chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        http = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            config = {"providers": {"process-guard-fixture": {
                "name": "Local process guard test", "env": ["PLINY_GUARD_TEST_KEY"],
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {"baseURL": f"http://127.0.0.1:{http.server_port}/v1", "apiKey": "fixture"},
                "models": {"fake": {"capabilities": {"tools": True, "input": ["text"], "output": ["text"]},
                                    "limit": {"context": 1048576, "output": 8192}}},
            }}}
            with patch.dict(os.environ, {"PLINY_AGENT_PERMISSIONS": "workshop"}):
                env = opencode_runner.config_env(config)
            env["PLINY_GUARD_TEST_KEY"] = "fixture"
            # Simulate a still-running dashboard that injected the old config.
            inherited = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            inherited["policies"] = []
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inherited)
            runner = str(Path(opencode_runner.__file__).resolve())
            with tempfile.TemporaryDirectory(prefix="pliny-guard-") as work:
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", runner, "--model", "process-guard-fixture/fake"],
                    input="Run the local process-guard diagnostic and finish.",
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    cwd=work, env=env, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            self.assertGreaterEqual(len(request_count), 1, "The real CLI must reach the local fake model")
            self.assertIsNone(victim.poll(), "A model-requested Stop-Process must not run")
            self.assertRegex(result.stdout.lower(), r"denied|blocked|policy")
            self.assertIn("GUARD_SHELL_OK", result.stdout, "Ordinary shell commands must remain usable")
        finally:
            http.shutdown()
            http.server_close()
            if victim.poll() is None:
                victim.kill()
            victim.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
