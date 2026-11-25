"""Make the repo root importable for every test in this directory.

The suite lives in tests/ but imports top-level modules (server, opencode_runner,
gauntlet, ...). Pytest's default import mode puts the test file's own directory
on sys.path, which used to work only because the tests sat at the repo root and
``python -m pytest`` also adds the current directory. Doing it here keeps
``pytest``, ``python -m pytest`` and a run from any working directory identical.
"""

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


import pytest


@pytest.fixture(autouse=True)
def _redteam_state_isolation():
    """Restore process-wide red-team state after every test.

    ``server._rt_state`` and ``Handler._rt_last_prompt`` are module-level
    mutable state. Tests that exercise the freeform red-team flow can leave
    ``cracked`` or prompt-pairing entries behind, which then corrupts
    unrelated structured-campaign tests running later in the same process.
    """
    try:
        import server
    except Exception:
        yield
        return
    with server._rt_state_lock:
        saved_state_obj = server._rt_state
        saved_state = dict(server._rt_state)
    saved_prompts = dict(server.Handler._rt_last_prompt)
    yield
    with server._rt_state_lock:
        # rt_start rebinds _rt_state to each new run object, so restoring
        # contents is not enough — rebind the original object as well, and
        # drop every run the test registered.
        server._rt_state = saved_state_obj
        server._rt_state.clear()
        server._rt_state.update(saved_state)
        if hasattr(server, "_rt_registry"):
            server._rt_registry.reset()
    server.Handler._rt_last_prompt.clear()
    server.Handler._rt_last_prompt.update(saved_prompts)
