import json
import os
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
from pathlib import Path
from unittest.mock import patch

import install_windows
import process_control
import server


class BusyFileTests(unittest.TestCase):
    def test_state_file_survives_constant_concurrent_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            server.save_json(path, [{"value": 0}])
            done = threading.Event()
            errors = []

            def reader():
                while not done.is_set():
                    try:
                        server.load_json(path)
                    except Exception as error:
                        errors.append(error)
                        return

            readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
            for thread in readers:
                thread.start()
            try:
                for value in range(1, 60):
                    server.save_json(path, [{"value": value, "padding": "x" * 2000}])
            finally:
                done.set()
                for thread in readers:
                    thread.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(server.load_json(path)[0]["value"], 59)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["state.json"])

    def test_retry_gives_up_eventually_and_reports_the_error(self):
        def always_busy():
            raise PermissionError("busy")

        with self.assertRaises(PermissionError):
            process_control.retry_while_file_busy(always_busy, attempts=2, delay=0)

    def test_retry_returns_the_result_once_the_file_is_free(self):
        calls = []

        def busy_once():
            calls.append(1)
            if len(calls) == 1 and os.name == "nt":
                raise PermissionError("busy")
            return "done"

        self.assertEqual(process_control.retry_while_file_busy(busy_once, delay=0), "done")


class PathTests(unittest.TestCase):
    def test_file_tree_paths_use_forward_slashes_for_the_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "deep-research").mkdir()
            (workshop / "deep-research" / "note.md").write_text("x", encoding="utf-8")
            with patch.object(server, "WORKSHOP_DIR", workshop):
                tree = server.scan_file_tree(workshop)
            folder = next(item for item in tree if item["name"] == "deep-research")
            self.assertEqual(folder["children"][0]["rel_path"], "deep-research/note.md")

    def test_hermes_is_found_on_path_first(self):
        with patch("shutil.which", return_value="C:/Hermes/hermes.exe"):
            self.assertEqual(server.hermes_cli(), "C:/Hermes/hermes.exe")


class BrowserStatusTests(unittest.TestCase):
    class FakeHandler:
        def __init__(self):
            self.sent = None

        def _send_json(self, data, status=200):
            self.sent = (status, data)

    def poll(self, error):
        handler = self.FakeHandler()
        with patch("urllib.request.urlopen", side_effect=error):
            server.Handler._proxy_pw_browser(handler, "/pause_status", "GET", when_down={"paused": False})
        return handler.sent

    def test_status_poll_is_not_an_error_when_the_browser_is_off(self):
        self.assertEqual(self.poll(urllib.error.URLError("refused")), (200, {"paused": False}))
        self.assertEqual(self.poll(TimeoutError()), (200, {"paused": False}))

    def test_commands_still_report_a_browser_that_is_off(self):
        handler = self.FakeHandler()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            server.Handler._proxy_pw_browser(handler, "/pause")
        self.assertEqual(handler.sent[0], 502)


class InstallerTests(unittest.TestCase):
    def test_missing_optional_packages_are_installed_without_blocking_startup(self):
        runs = []

        def fake_run(command, check=False):
            runs.append(command)
            return unittest.mock.Mock(returncode=1)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install_windows.importlib.util, "find_spec", return_value=None), \
                patch.object(install_windows.subprocess, "run", side_effect=fake_run), \
                patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": directory}):
            install_windows.install_optional_features()
        self.assertEqual([command[-1] for command in runs], ["pillow", "playwright"])

    def test_firefox_is_downloaded_only_when_absent(self):
        runs = []

        def fake_run(command, check=False):
            runs.append(command)
            return unittest.mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install_windows.importlib.util, "find_spec", return_value=object()), \
                patch.object(install_windows.subprocess, "run", side_effect=fake_run), \
                patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": directory}):
            install_windows.install_optional_features()
            self.assertEqual(runs[-1][-2:], ["install", "firefox"])
            runs.clear()
            (Path(directory) / "firefox-1").mkdir()
            install_windows.install_optional_features()
        self.assertEqual(runs, [])


if __name__ == "__main__":
    unittest.main()
