"""Regression: runpy __main__ + lazy ``import server`` must not fork the registry.

install_windows.py starts the dashboard with
``runpy.run_path(server.py, run_name="__main__")``. Python then registers the
file as ``__main__`` and never as ``server``. gauntlet.launch_session() and
fixer's watchdog reach for the globals lazily with ``import server`` /
``from server import ...``, which without an alias builds a *second* module
object. Its ``active_sessions`` is a different, empty dict, so
``target_call_gate()`` in the real HTTP handler rejects every probe with
"Session is not running; no target request was sent." That is what killed run
20261117-233908-49b3a3 before attempt 1 (memory/20261118-035352-6a9109.md).

Two checks:
1. server.py aliases itself into sys.modules before importing gauntlet/fixer.
2. The alias actually prevents the fork, and the same construction without the
   alias still forks - so the test can fail, not just pass.
"""

import ast
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server.py"

GUARD = "sys.modules.setdefault(\"server\", sys.modules[__name__])"


def test_server_aliases_itself_before_importing_gauntlet():
    source = SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    guard_line = None
    gauntlet_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            condition = ast.dump(node.test)
            if "__name__" in condition and "__main__" in condition:
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                if "attr='modules'" in body and "attr='setdefault'" in body:
                    guard_line = node.lineno
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("gauntlet", "fixer") and gauntlet_line is None:
                    gauntlet_line = node.lineno
    assert guard_line is not None, "server.py must alias itself into sys.modules"
    assert gauntlet_line is not None
    assert guard_line < gauntlet_line, (
        "the alias must be installed before gauntlet/fixer are imported"
    )
    assert "setdefault(\"server\"" in source or "setdefault('server'" in source


SCENARIO = r'''
import runpy, sys, tempfile, pathlib

root = pathlib.Path(tempfile.mkdtemp())
sys.path.insert(0, str(root))
(root / "leafmod.py").write_text(
    "def touch():\n"
    "    from coremod import registry\n"
    "    registry['hit'] = True\n",
    encoding="utf-8",
)


def build(entry_text, name):
    path = root / name
    path.write_text(entry_text, encoding="utf-8")
    return path


ENTRY = """\
import sys
{guard}
import leafmod

registry = {{}}
leafmod.touch()
assert sys.modules['coremod'] is sys.modules['__main__'], 'module forked'
assert sys.modules['coremod'].registry is registry
print('ALIASED')
"""

FORKS = """\
import sys
import leafmod

registry = {{}}
leafmod.touch()
assert sys.modules['coremod'] is not sys.modules['__main__'], 'unexpected: no fork'
print('FORKED')
"""

guard = "if __name__ == '__main__':\n    sys.modules.setdefault('coremod', sys.modules[__name__])"

ok = runpy.run_path(str(build(ENTRY.format(guard=guard), "aliased.py")), run_name="__main__")
del ok

# No alias: a second coremod instance is exactly the failure mode we fixed.
control = build(FORKS.format(), "forked.py")
runpy.run_path(str(control), run_name="__main__")
print("SCENARIO OK")
'''


def test_alias_blocks_the_fork_and_control_forks():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", SCENARIO],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ALIASED" in result.stdout
    assert "FORKED" in result.stdout
    assert "SCENARIO OK" in result.stdout
