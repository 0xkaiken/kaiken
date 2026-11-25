import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import server


class ShipLogTests(unittest.TestCase):
    def read_log(self, rows, params=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SHIPPING_LOG.jsonl"
            original = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(original, encoding="utf-8")
            handler = Mock()
            with patch.object(server, "SHIPPING_LOG_FILE", path):
                server.Handler._api_ship_log_get(handler, params or {})
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            return handler._send_json.call_args.args[0]

    def test_legacy_entries_have_titles_and_group_with_current_entries(self):
        data = self.read_log([
            {"ts": "2026-11-03T10:00:00", "kind": "tweet", "what": "Legacy tweet"},
            {"ts": "2026-11-03T11:00:00", "kind": "research_note", "what": "Research findings"},
            {"ts": "2026-11-03T12:00:00", "kind": "gauntlet", "what": "Completed run"},
            {"ts": "2026-11-03T13:00:00", "type": "tool", "title": "Current tool",
             "kind": "tweet", "what": "Older title", "path": "tools/tool.py"},
        ])
        self.assertEqual(data["counts_by_type"], {"tweet": 1, "other": 1, "gauntlet-run": 1, "tool": 1})
        by_title = {entry["title"]: entry for entry in data["entries"]}
        self.assertEqual(set(by_title), {"Legacy tweet", "Research findings", "Completed run", "Current tool"})
        self.assertEqual(by_title["Current tool"]["path"], "tools/tool.py")

    def test_type_filter_includes_legacy_entries(self):
        data = self.read_log([
            {"ts": "2026-11-03T10:00:00", "kind": "tweet", "what": "Legacy tweet"},
            {"ts": "2026-11-03T11:00:00", "type": "tool", "title": "Tool"},
        ], {"type": ["tweet"]})
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["entries"][0]["title"], "Legacy tweet")

    def test_log_time_parses_naive_as_local_and_offsets_as_instants(self):
        naive = server._parse_log_time("2026-11-03T10:00:00")
        aware = server._parse_log_time("2026-11-03T10:00:00Z")
        self.assertEqual(naive.utcoffset(), datetime.now().astimezone().utcoffset())
        self.assertEqual(aware, datetime(2026, 11, 3, 10, tzinfo=timezone.utc))
        self.assertEqual(server._parse_log_time("2026-11-03T10:00:00+03:00").utcoffset(),
                         timedelta(hours=3))
        self.assertIsNone(server._parse_log_time(""))
        self.assertIsNone(server._parse_log_time("not a time"))

    def test_since_with_utc_offset_compares_real_instants(self):
        # Entries are written as naive local wall-clock time; the browser sends
        # `since` as UTC. A 10:30 local cutoff must exclude a 10:00 local entry
        # even though its string sorts after the UTC form.
        cutoff = datetime(2026, 11, 3, 10, 30)
        since_utc = cutoff.astimezone(timezone.utc).isoformat()
        data = self.read_log([
            {"ts": "2026-11-03T10:00:00", "type": "tool", "title": "Before cutoff"},
            {"ts": "2026-11-03T11:00:00", "type": "tool", "title": "After cutoff"},
        ], {"since": [since_utc]})
        self.assertEqual([entry["title"] for entry in data["entries"]], ["After cutoff"])

    def test_latest_entries_are_selected_by_timestamp_not_append_order(self):
        data = self.read_log([
            {"ts": "2026-11-03T12:00:00", "type": "tool", "title": "Newest"},
            {"ts": "2026-11-03T10:00:00", "type": "tool", "title": "Oldest"},
            {"ts": "2026-11-03T11:00:00", "type": "tool", "title": "Middle"},
        ], {"limit": ["1"], "since": ["2026-11-03T10:30:00"]})
        self.assertEqual(data["entries"][0]["title"], "Newest")


class ShipLogDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "SHIPPING_LOG.jsonl"
        self.rows = [
            {"ts": "2026-11-03T10:00:00", "type": "tool", "title": "A", "path": "a.py"},
            {"ts": "2026-11-03T11:00:00", "kind": "tweet", "what": "B"},
        ]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in self.rows), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def delete(self, params, authorized=True):
        handler = Mock()
        with patch.object(server, "SHIPPING_LOG_FILE", self.path), \
                patch.object(server, "_require_auth", return_value=authorized), \
                patch.object(server, "broadcast_sse") as broadcast:
            server.Handler._api_ship_log_delete(handler, params)
        return handler, broadcast

    def test_rows_carry_stable_ids(self):
        first = server._normalize_ship_entry(dict(self.rows[0]))["id"]
        second = server._normalize_ship_entry(dict(self.rows[1]))["id"]
        self.assertTrue(first and second)
        self.assertNotEqual(first, second)
        self.assertEqual(first, server._normalize_ship_entry(dict(self.rows[0]))["id"])

    def test_delete_one_by_id_keeps_the_rest(self):
        target = server._normalize_ship_entry(dict(self.rows[0]))["id"]
        handler, broadcast = self.delete({"id": [target]})
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True, "deleted": 1})
        left = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual([row.get("title") or row.get("what") for row in left], ["B"])
        self.assertEqual(broadcast.call_args.args[0], "ship_log_deleted")

    def test_delete_all_truncates(self):
        handler, _ = self.delete({"all": ["1"]})
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True, "deleted": 2})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "")

    def test_delete_requires_operator_auth(self):
        handler, broadcast = self.delete({"all": ["1"]}, authorized=False)
        handler._send_json.assert_not_called()
        broadcast.assert_not_called()
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)


class BriefingSummaryTests(unittest.TestCase):
    def handler(self, payload):
        handler = Mock()
        handler._read_body.return_value = json.dumps(payload)
        return handler

    def test_summary_uses_text_completion_without_publishing_redteam_events(self):
        handler = self.handler({"entries": [{"type": "tool", "title": "New tool", "status": "staged"}]})
        with patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "default_runtime", return_value="opencode"), \
                patch.object(server.agent_secrets, "resolve_key", return_value=""), \
                patch("rt_hermes.chat", return_value="- New tool is staged.") as completion, \
                patch.object(server, "broadcast_sse") as broadcast:
            server.Handler._api_ship_log_summary(handler)
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True, "summary": "- New tool is staged."})
        messages, _ = completion.call_args.args
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn('"status": "staged"', messages[1]["content"])
        broadcast.assert_not_called()

    def test_summary_requires_operator_auth(self):
        handler = self.handler({"entries": [{"title": "Tool"}]})
        with patch.object(server, "_require_auth", return_value=False), \
                patch("rt_hermes.chat") as completion:
            server.Handler._api_ship_log_summary(handler)
        handler._read_body.assert_not_called()
        completion.assert_not_called()

    def test_missing_key_and_model_failure_return_errors(self):
        for runtime, key, status in (("codex", "", 503), ("codex", "fixture-key", 502)):
            with self.subTest(runtime=runtime, has_key=bool(key)):
                handler = self.handler({"entries": [{"title": "Tool"}]})
                with patch.object(server, "_require_auth", return_value=True), \
                        patch.object(server, "default_runtime", return_value=runtime), \
                        patch.object(server.agent_secrets, "resolve_key", return_value=key), \
                        patch("rt_hermes.chat", side_effect=RuntimeError("transport failed")) as completion:
                    server.Handler._api_ship_log_summary(handler)
                self.assertEqual(handler._send_json.call_args.args[1], status)
                self.assertFalse(handler._send_json.call_args.args[0]["ok"])
                if not key:
                    completion.assert_not_called()

    def test_invalid_entries_do_not_call_a_model(self):
        for payload in ([], {"entries": []}, {"entries": ["bad"]}):
            handler = self.handler(payload)
            with patch.object(server, "_require_auth", return_value=True), \
                    patch("rt_hermes.chat") as completion:
                server.Handler._api_ship_log_summary(handler)
            self.assertEqual(handler._send_json.call_args.args[1], 400)
            completion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
