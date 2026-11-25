"""Project-scoped restarts from the Windows launcher."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import install_windows
import process_control


class ServerDiscoveryTests(unittest.TestCase):
    def test_matches_this_checkout_and_legacy_launcher_but_preserves_other_processes(self):
        root = Path(tempfile.gettempdir()) / "Pliny Ş project"
        server = str(root / "server.py")
        installer = str(root / "install_windows.py")
        indicator = str(root / "computer_use_indicator.py")
        other = str(root.parent / "other" / "server.py")
        rows = [
            (100, 1, "python.exe", f'python.exe -X utf8 "{server}"'),
            (101, 1, "python.exe", f'python.exe "{installer}" --run --restart'),
            (102, 1, "python.exe", f'python.exe "{installer}"'),
            (103, 1, "python.exe", f'python.exe "{other}"'),
            (104, 1, "python.exe", f'python.exe -c "print(\'{server}\')"'),
            (105, 1, "python.exe", 'python.exe -X utf8 -u install_windows.py --run'),
            (106, 105, "python.exe", f'python.exe "{indicator}" --port 8888 --parent 105'),
            (107, 1, "python.exe", 'python.exe -u install_windows.py --run'),
            (108, 1, "python.exe", 'python.exe -u install_windows.py --run'),
            (109, 108, "python.exe", f'python.exe "{indicator}" --parent 999'),
            (110, 1, "powershell.exe", f'powershell.exe "{server}"'),
            (500, 501, "python.exe", f'python.exe "{installer}" --run --restart'),
            (501, 1, "python.exe", f'python.exe "{server}"'),
        ]
        with patch.object(process_control, "_process_snapshot", return_value=rows), \
                patch.object(process_control.os, "getpid", return_value=500):
            self.assertEqual(process_control.windows_project_server_pids(root), {100, 101, 105})


@unittest.skipUnless(os.name == "nt", "native Windows launcher")
class WindowsRestartTests(unittest.TestCase):
    def test_restart_stops_the_old_server_releases_port_and_preserves_other_checkout(self):
        with tempfile.TemporaryDirectory(prefix="Pliny Ş restart ") as tmp:
            root = Path(tmp)
            other = root / "other-checkout"
            other.mkdir()
            script = (
                "import socket, time\n"
                "s = socket.socket()\n"
                "s.bind(('127.0.0.1', 0))\n"
                "s.listen()\n"
                "print(s.getsockname()[1], flush=True)\n"
                "time.sleep(30)\n"
            )
            children = []
            try:
                for folder in (root, other):
                    (folder / "server.py").write_text(script, encoding="utf-8")
                    child = subprocess.Popen(
                        [sys.executable, "-X", "utf8", str(folder / "server.py")],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    children.append(child)
                    port = int(child.stdout.readline())
                    if folder == root:
                        old_port = port
                with patch.object(install_windows, "restore_system_cursors"):
                    install_windows.restart_existing_server(root)
                self.assertIsNotNone(children[0].wait(timeout=5))
                self.assertIsNone(children[1].poll(), "Other checkouts must remain running")
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", old_port))
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)
                    child.stdout.close()
                    child.stderr.close()

    def test_failure_to_stop_old_server_aborts_startup(self):
        with patch.object(install_windows.importlib.util, "find_spec", return_value=Mock()), \
                patch.object(install_windows, "initialize_files"), \
                patch.object(install_windows.os, "chdir"), \
                patch.object(install_windows, "windows_project_server_pids", return_value={123}), \
                patch.object(install_windows, "signal_process_group", side_effect=OSError("access denied")), \
                patch.object(install_windows, "restore_system_cursors"), \
                patch.object(install_windows.runpy, "run_path") as run:
            with self.assertRaises(SystemExit) as error:
                install_windows.main(["--run", "--restart"])
            self.assertEqual(error.exception.code, 1)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
