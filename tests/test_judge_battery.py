import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import judge_battery


class JudgeBatteryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cache = Path(self.tmp.name) / "battery.json"
        self.cache_patch = patch.object(judge_battery, "CACHE_FILE", cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        env = patch.dict(os.environ, {"PLINY_JUDGE_BATTERY": "1"}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def _judge(scores):
        calls = []

        def fn(behavior_request=None, model_response=None, behavior_name=None, **_kw):
            calls.append(behavior_name)
            return {"score": scores[len(calls) - 1], "reason": "fixture", "error": None}
        return fn, calls

    def test_good_judge_passes(self):
        judge, calls = self._judge([0, 2])
        result = judge_battery.run_battery(judge_fn=judge, force=True)
        self.assertEqual("ok", result["result"])
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(calls))

    def test_bad_judge_is_rejected(self):
        judge, _ = self._judge([3, 2])  # graded an obvious refusal as fully compliant
        result = judge_battery.run_battery(judge_fn=judge, force=True)
        self.assertEqual("failed", result["result"])
        self.assertFalse(result["ok"])
        with self.assertRaises(RuntimeError) as ctx:
            with patch.object(judge_battery, "run_battery", return_value=result):
                judge_battery.verify_judge_or_raise()
        self.assertIn("falsification battery", str(ctx.exception))

    def test_unavailable_judge_does_not_block(self):
        judge, _ = self._judge([None, None])
        result = judge_battery.run_battery(judge_fn=judge, force=True)
        self.assertEqual("inconclusive", result["result"])
        with patch.object(judge_battery, "run_battery", return_value=result):
            self.assertEqual(result, judge_battery.verify_judge_or_raise())

    def test_cache_reuses_a_passing_receipt(self):
        judge, calls = self._judge([0, 2])
        first = judge_battery.run_battery(judge_fn=judge, force=True)
        self.assertFalse(first["cache_hit"])
        second = judge_battery.run_battery(judge_fn=judge)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(2, len(calls))

    def test_env_disable_skips_all_calls(self):
        with patch.dict(os.environ, {"PLINY_JUDGE_BATTERY": "0"}):
            result = judge_battery.run_battery(judge_fn=lambda **_kw: self.fail("called"))
        self.assertTrue(result["skipped"])
        self.assertEqual("ok", result["result"])


if __name__ == "__main__":
    unittest.main()
