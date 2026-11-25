"""Regression tests for tools/pulsvivus.py control flow.

No network: opencode_runner.complete_text is the only path that spends a
request, and every test replaces it with a stand-in. --shout is never used.

The two bugs these lock down:
  1. main() returned 2 before --write ran, so a harness failure produced no
     diagnostic report — the one moment the report matters most.
  2. --only control returned 1 after a successful control ping, because the
     exit code was derived from "any upstream alive" while the control route
     is deliberately excluded from that flag.

A third, found 2026-11-05: the tool lives in the repo's tools/ and is installed
into <workshop>/tools/, so parents[1] is the install dir from only one of the
two. Paths now come from _paths (as `REPO_ROOT` and `workshop`), and the report
destination is `workshop() / "attempts"` — hence the tests patch `workshop`.
"""

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "pulsvivus_under_test", REPO_ROOT / "tools" / "pulsvivus.py")
pulsvivus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pulsvivus)

CONTROL_MODEL = pulsvivus.ROUTES[0][1]
UPSTREAM_MODELS = [model for _, model, _ in pulsvivus.ROUTES[1:]]

DEAD_402 = RuntimeError(
    "OpenCode Go request for %s did not complete "
    "(provider.quota: Insufficient account funds)")


def runner(alive_models=(), calls=None):
    """complete_text stand-in: PONG for the listed models, a 402 otherwise."""
    alive = set(alive_models)

    def fake(prompt, model=None, timeout=None):
        if calls is not None:
            calls.append(model)
        if model in alive:
            return "PONG"
        raise DEAD_402

    return fake


def run_main(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = pulsvivus.main(argv)
    return code, out.getvalue()


class PulsvivusControlFlowTests(unittest.TestCase):

    def test_harness_error_still_writes_report(self):
        """Bug 1: a failed control route must not skip --write."""
        with TemporaryDirectory() as tmp, \
                mock.patch.object(pulsvivus, "workshop", lambda: Path(tmp)), \
                mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                                  runner(alive_models=())):
            code, out = run_main(["--write"])

            self.assertEqual(code, 2)
            self.assertIn("HARNESS ERROR", out)

            reports = list(Path(tmp).glob(
                "attempts/access_status_*.md"))
            self.assertEqual(len(reports), 1, "report was not written on harness error")
            body = reports[0].read_text(encoding="utf-8")
            self.assertIn("HARNESS ERROR", body)
            self.assertIn("control/glm-5.3-flash", body)

    def test_only_control_alive_returns_zero(self):
        """Bug 2: a healthy filtered control ping is a success, not a 1."""
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=[CONTROL_MODEL])):
            code, out = run_main(["--only", "control"])

        self.assertEqual(code, 0, out)
        self.assertIn("all 1 selected route(s) ALIVE", out)

    def test_only_control_writes_report_and_returns_zero(self):
        with TemporaryDirectory() as tmp, \
                mock.patch.object(pulsvivus, "workshop", lambda: Path(tmp)), \
                mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                                  runner(alive_models=[CONTROL_MODEL])):
            code, out = run_main(["--only", "control", "--write"])

            self.assertEqual(code, 0, out)
            reports = list(Path(tmp).glob(
                "attempts/access_status_*.md"))
            self.assertEqual(len(reports), 1)
            self.assertIn("ALIVE", reports[0].read_text(encoding="utf-8"))

    def test_only_filters_to_matching_routes(self):
        """--only must ping the selected routes and nothing else."""
        calls = []
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=["opencode/gpt-6.1-sol"],
                                      calls=calls)):
            code, out = run_main(["--only", "opencode"])

        self.assertEqual(code, 0, out)
        self.assertEqual(calls, ["opencode/gpt-6.1-sol"])
        self.assertNotIn(CONTROL_MODEL, calls)
        self.assertIn("opencode/gpt-6.1-sol", out)
        self.assertNotIn("orcarouter/openai/gpt-5.6-sol", out)

    def test_only_dead_route_returns_one(self):
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=[CONTROL_MODEL])):
            code, out = run_main(["--only", "openrouter"])

        self.assertEqual(code, 1, out)
        self.assertIn("1/1 selected route(s) not alive", out)

    def test_only_no_match_returns_two(self):
        calls = []
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=[], calls=calls)):
            code, out = run_main(["--only", "does-not-exist"])

        self.assertEqual(code, 2, out)
        self.assertEqual(calls, [])

    def test_full_run_any_upstream_alive_returns_zero(self):
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=[CONTROL_MODEL, UPSTREAM_MODELS[0]])):
            code, out = run_main([])

        self.assertEqual(code, 0, out)
        self.assertIn("UPSTREAM ALIVE", out)

    def test_full_run_all_upstream_dead_returns_one(self):
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=[CONTROL_MODEL])):
            code, out = run_main([])

        self.assertEqual(code, 1, out)
        self.assertIn("ALL DEAD", out)

    def test_full_run_control_dead_returns_two(self):
        with mock.patch.object(pulsvivus.opencode_runner, "complete_text",
                               runner(alive_models=UPSTREAM_MODELS)):
            code, out = run_main([])

        self.assertEqual(code, 2, out)
        self.assertIn("HARNESS ERROR", out)


if __name__ == "__main__":
    unittest.main()
