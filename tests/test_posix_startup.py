"""The server must import without the optional Windows indicator/Pillow stack."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import server


def test_server_import_does_not_load_windows_indicator_or_pillow():
    code = '''
import importlib.abc
import sys
class MissingDesktopDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"PIL", "desktop_glow", "computer_use_indicator"}:
            raise ModuleNotFoundError("Optional Windows desktop dependency: " + fullname)
sys.meta_path.insert(0, MissingDesktopDependencies())
import server
print("server import OK")
'''
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "server import OK" in result.stdout


def test_non_windows_indicator_is_a_noop():
    with patch.object(server.os, "name", "posix"), \
            patch.dict(sys.modules, {"computer_use_indicator": None}):
        assert server.start_indicator("unused", 8888) is None
