import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import rt_send
import rt_hermes
import gauntlet
import server


class WindowsPromptTransportTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell file transport")
    def test_windows_powershell_passes_a_unicode_file_path_without_a_text_pipeline(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş transport ") as tmp:
            root = Path(tmp)
            prompt = "Aylın — İstanbul 你好 🐉 ???\r\n"
            source = root / "input Ş.txt"
            source.write_bytes(prompt.encode("utf-8"))
            wrapper = root / "local_fixture.py"
            wrapper.write_text(
                "import sys,hashlib\n"
                f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n"
                "import rt_send\n"
                "rt_send.post_to_dashboard=lambda data:None\n"
                "rt_send.send_to_target=lambda provider,model,prompt:hashlib.sha256(prompt.encode('utf-8')).hexdigest()\n"
                "rt_send.main()\n", encoding="utf-8")
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = f"& {quote(sys.executable)} -X utf8 {quote(wrapper)} 1 opencode opencode-go/fixture --prompt-file {quote(source)}"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                    capture_output=True, encoding="utf-8", check=True, timeout=10)
            self.assertEqual(result.stdout.strip(), hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def test_gauntlet_rt_send_pipe_preserves_unicode_through_the_real_bridge(self):
        prompt = "Aylın — İstanbul 你好 🐉 ???"
        with tempfile.TemporaryDirectory(prefix="Gauntlet Ş pipe ") as tmp:
            root = Path(tmp)
            stub = root / "rt_send.py"
            stub.write_text(
                "import sys,hashlib\n"
                "rt_text = sys.stdin.read()\n"
                "print(hashlib.sha256(rt_text.encode('utf-8')).hexdigest(), end='')\n",
                encoding="utf-8")
            with patch.object(gauntlet, "BASE_DIR", root):
                out, meta = gauntlet._run_rt_send_meta("opencode", "opencode-go/fixture", 7, prompt)
        self.assertEqual(meta, {})
        self.assertEqual(out, hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def test_hermes_fallback_pipe_preserves_unicode_to_rt_send(self):
        prompt = "Aylın — İstanbul 你好 🐉 ???"
        with tempfile.TemporaryDirectory(prefix="Hermes Ş pipe ") as tmp:
            stub = Path(tmp) / "stub_rt_send.py"
            stub.write_text(
                "import sys,hashlib\n"
                "rt_text = sys.stdin.read()\n"
                "print(hashlib.sha256(rt_text.encode('utf-8')).hexdigest(), end='')\n",
                encoding="utf-8")
            with patch.object(rt_hermes, "RT_SEND", str(stub)):
                result = rt_hermes.run_rt_send(3, "opencode", "opencode-go/fixture", prompt)
        self.assertEqual(result, hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def test_file_input_preserves_unicode_whitespace_and_literal_question_marks(self):
        text = "  Aylın — İstanbul 你好 🐉 ???\r\n"
        with tempfile.TemporaryDirectory(prefix="Prompt Ş file ") as tmp:
            path = Path(tmp) / "input.txt"
            path.write_bytes(text.encode("utf-8"))
            argv = ["rt_send.py", "1", "opencode", "opencode-go/fixture", "--prompt-file", str(path)]
            with patch.object(sys, "argv", argv), patch.object(sys, "stdin", io.StringIO("DAMAGED PIPE")), \
                    patch.object(rt_send, "send_to_target", return_value="Fixture response") as send, \
                    patch.object(rt_send, "post_to_dashboard"), patch("builtins.print"):
                rt_send.main()
            self.assertEqual(send.call_args.args[2], text)

    def test_probe_helper_uses_the_same_utf8_file_input(self):
        helper = Path(__file__).resolve().parents[1] / "state" / "workshop" / "attempts" / "probe_send.py"
        if not helper.is_file():
            self.skipTest("Optional workshop helper is not installed")
        spec = importlib.util.spec_from_file_location("probe_transport_fixture", helper)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        with tempfile.TemporaryDirectory(prefix="Probe Ş file ") as tmp:
            path = Path(tmp) / "input.txt"
            text = "Aylın — 你好 ???"
            path.write_text(text, encoding="utf-8")
            argv = ["probe_send.py", "1", "opencode", "opencode-go/fixture", "--prompt-file", str(path)]
            with patch.object(sys, "argv", argv), patch.object(sys, "stdin", io.StringIO("DAMAGED PIPE")), \
                    patch.object(probe, "complete_text", return_value="Fixture response") as complete, \
                    patch.object(probe, "post_to_dashboard"), patch("builtins.print"):
                probe.main()
            self.assertEqual(complete.call_args.args[0], text)

    @unittest.skipUnless(os.name == "nt" and shutil.which("node"), "Windows paths and Node parser")
    def test_served_paths_survive_javascript_template_literal_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "fixture.html"
            template.write_text("globalThis.paths = [`{{WORKSHOP_DIR}}`, `{{PLINY_HOME}}`];", encoding="utf-8")
            workshop, home = Path(r"C:\Test Ş Folder\workshop"), Path(r"C:\Test Ş Folder\repo")
            handler = server.Handler.__new__(server.Handler)
            handler._send_text = Mock()
            with patch.object(server, "BASE_DIR", Path(tmp)), patch.object(server, "WORKSHOP_DIR", workshop), \
                    patch.object(server, "PLINY_HOME", home):
                handler._serve_html_file("fixture.html")
            script = handler._send_text.call_args.args[0]
            # _serve_html_file appends the pairing overlay script; the parser
            # only cares about the template-substituted body above it.
            script = script.split("<script", 1)[0]
            result = subprocess.run(["node", "-e",
                "const fs=require('fs'),vm=require('vm'),ctx={}; vm.runInNewContext(fs.readFileSync(0,'utf8'),ctx); process.stdout.write(JSON.stringify(ctx.paths));"],
                input=script, capture_output=True, encoding="utf-8", check=True)
            self.assertEqual(json.loads(result.stdout), [workshop.as_posix(), home.as_posix()])

    def test_invalid_utf8_is_rejected_before_any_target_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.txt"
            path.write_bytes(b"invalid\xff")
            with patch.object(sys, "argv", ["rt_send.py", "1", "opencode", "opencode-go/fixture", "--prompt-file", str(path)]), \
                    patch.object(rt_send, "send_to_target") as send, \
                    patch.object(rt_send, "post_to_dashboard") as post, patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    rt_send.main()
            send.assert_not_called()
            post.assert_not_called()

    def test_legacy_stdin_mode_remains_available(self):
        with patch.object(sys, "stdin", io.StringIO("  Legacy input\n")):
            args = rt_send.parse_prompt_request(["1", "opencode", "opencode-go/fixture"])
        self.assertEqual(args.prompt, "Legacy input")


if __name__ == "__main__":
    unittest.main()
