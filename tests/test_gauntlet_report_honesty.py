"""The Gauntlet scoreboard must separate delivered attacks from attacker
refusals and judge errors — a run where the attacker refused to compose is
attacker-blocked, not evidence of target robustness."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gauntlet


def _attempt(error_type=None, is_error=False, **extra):
    a = {"prompt": "p", "response": "r", "markers_hit": []}
    if error_type:
        a["error_type"] = error_type
    if is_error:
        a["is_error"] = True
    a.update(extra)
    return a


class AttemptCensusTests(unittest.TestCase):
    def test_mixed_attempts_are_classified(self):
        attempts = [
            _attempt(),                                            # scored
            _attempt(),                                            # scored
            _attempt("attacker_refusal", True, pre_send_gate=True),
            _attempt("attacker_refusal", True),                    # legacy backstop
            _attempt("judge_error", True),
            _attempt("blocked", True),                             # upstream
            _attempt(covert_refusal=True, refusal_mode="empty_stop"),  # scored covert
        ]
        self.assertEqual(gauntlet._attempt_census(attempts), {
            "total": 7, "delivered": 4, "scored": 3,
            "attacker_refusal": 2, "upstream_error": 1, "judge_error": 1,
        })

    def test_empty_attempts(self):
        self.assertEqual(gauntlet._attempt_census([]), {
            "total": 0, "delivered": 0, "scored": 0,
            "attacker_refusal": 0, "upstream_error": 0, "judge_error": 0,
        })


class ReportHonestyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.runs_dir = Path(temp.name) / "runs"
        patcher = patch.object(gauntlet, "RUNS_DIR", self.runs_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, run_id, attempts, status="failed"):
        run = {
            "id": run_id, "status": "complete",
            "preset_id": "test", "preset_name": "TEST GAUNTLET",
            "preset": {},
            "started_at": "2026-11-18T20:00:00", "ended_at": "2026-11-18T20:10:00",
            "clock_seconds": 600,
            "targets": [{
                "id": "t1", "name": "Target One", "provider": "opencode",
                "model": "opencode-go/gpt-6-luna", "status": status,
                "elapsed_seconds": 600, "attempts": attempts, "markers_hit": [],
            }],
        }
        gauntlet._gauntlet_runs[run_id] = run
        self.addCleanup(gauntlet._gauntlet_runs.pop, run_id, None)
        path = gauntlet.write_report(run_id)
        return path.read_text(encoding="utf-8")

    def test_attacker_blocked_run_is_labelled(self):
        attempts = [_attempt("attacker_refusal", True, pre_send_gate=True)
                    for _ in range(6)]
        report = self._write("report-blocked-test", attempts)
        self.assertIn("- **Attacker-blocked:** 1 target(s)", report)
        self.assertIn("6 recorded — 0 delivered, 6 attacker refusals", report)
        self.assertIn("| Delivered | Attacker ref | Judge err |", report)
        self.assertIn("| 0 | 6 | 0 |", report)

    def test_judge_error_attempt_is_delivered_not_attacker_blocked(self):
        # A judge_error row reached the target and got a response — one
        # attack was delivered, so the target is not attacker-blocked.
        attempts = [_attempt("attacker_refusal", True, pre_send_gate=True)
                    for _ in range(6)]
        attempts.append(_attempt("judge_error", True))
        report = self._write("report-mixed-test", attempts)
        self.assertNotIn("Attacker-blocked", report)
        self.assertIn("7 recorded — 1 delivered, 6 attacker refusals", report)
        self.assertIn("| 1 | 6 | 1 |", report)

    def test_clean_run_has_no_attacker_blocked_label(self):
        report = self._write("report-clean-test", [_attempt() for _ in range(3)])
        self.assertNotIn("Attacker-blocked", report)
        self.assertIn("3 recorded — 3 delivered, 0 attacker refusals", report)

    def test_zero_attempt_run_is_not_called_attacker_blocked(self):
        report = self._write("report-empty-test", [])
        self.assertNotIn("Attacker-blocked", report)

    def test_report_is_utf8_with_cp1252_default(self):
        original_write_text = Path.write_text

        def cp1252_write_text(path, data, encoding=None, errors=None, newline=None):
            return original_write_text(
                path, data, encoding=encoding or "cp1252", errors=errors, newline=newline
            )

        attempt = _attempt(prompt="日本語 🐉", response="回答 ✅")
        with patch.object(Path, "write_text", cp1252_write_text):
            report = self._write("report-utf8-test", [attempt])
        self.assertIn("❌ failed", report)
        transcript = self.runs_dir / "report-utf8-test" / "transcripts" / "t1.jsonl"
        self.assertEqual(json.loads(transcript.read_text(encoding="utf-8")), attempt)


if __name__ == "__main__":
    unittest.main()