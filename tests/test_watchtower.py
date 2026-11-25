import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class _Response:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ATOM = """<feed>
<entry>
  <title>Roleplay jailbreak moves the guardrail</title>
  <id>https://example.test/atom/1</id>
  <summary>A new LLM jailbreak technique.</summary>
</entry>
<entry>
  <title>Sourdough for beginners</title>
  <id>https://example.test/atom/2</id>
  <summary>Bread.</summary>
</entry>
</feed>"""

RSS = """<rss><channel>
<item>
  <title>Prompt injection through tool descriptions</title>
  <link>https://example.test/rss/1</link>
  <description>Agent tool attack.</description>
</item>
<item>
  <title>Weather in Istanbul</title>
  <link>https://example.test/rss/2</link>
  <description>Sunny.</description>
</item>
</channel></rss>"""


class WatchtowerSourceTests(unittest.TestCase):
    def check(self, body, source, signals=None):
        signals = [] if signals is None else signals
        with patch("urllib.request.urlopen", return_value=_Response(body)), \
                patch.object(server, "broadcast_sse") as broadcast:
            server._check_watchtower_source(source, signals)
        return signals, broadcast

    def test_atom_match_filters_and_tags(self):
        signals, broadcast = self.check(ATOM, {
            "name": "Fixture Atom", "url": "https://example.test/feed.atom",
            "tag": "jailbreak", "match": ["jailbreak"],
        })
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["title"], "Roleplay jailbreak moves the guardrail")
        self.assertEqual(signals[0]["tag"], "jailbreak")
        self.assertEqual(signals[0]["url"], "https://example.test/atom/1")
        broadcast.assert_called_once()
        self.assertEqual(broadcast.call_args.args[0], "watchtower_signal")
        self.assertEqual(broadcast.call_args.args[1]["tag"], "jailbreak")

    def test_rss_without_match_keeps_everything(self):
        signals, _ = self.check(RSS, {
            "name": "Fixture RSS", "url": "https://example.test/feed.xml",
        })
        self.assertEqual([s["title"] for s in signals],
                         ["Prompt injection through tool descriptions", "Weather in Istanbul"])
        self.assertEqual(signals[0]["tag"], "")

    def test_existing_titles_are_deduplicated(self):
        signals = [{"title": "Prompt injection through tool descriptions"}]
        result, _ = self.check(RSS, {
            "name": "Fixture RSS", "url": "https://example.test/feed.xml",
        }, signals=signals)
        self.assertEqual(len(result), 2)

    def test_default_watch_list_covers_llm_jailbreak_and_cves(self):
        names = [source["name"] for source in server.WATCHTOWER_DEFAULT_SOURCES]
        tags = {source.get("tag") for source in server.WATCHTOWER_DEFAULT_SOURCES}
        self.assertIn("arXiv cs.CR Jailbreak", names)
        self.assertIn("arXiv cs.AI Jailbreak", names)
        self.assertIn("HN LLM Jailbreak", names)
        self.assertIn("NVD Prompt Injection", names)
        self.assertIn("NVD LLM", names)
        self.assertIn("NVD AI Agent", names)
        self.assertIn("NVD MCP", names)
        self.assertIn("jailbreak", tags)
        self.assertIn("prompt-injection", tags)
        self.assertIn("red-team", tags)
        self.assertIn("cve", tags)
        simon = next(s for s in server.WATCHTOWER_DEFAULT_SOURCES if s["name"] == "Simon Willison")
        self.assertIn("jailbreak", simon["match"])

    def test_nvd_source_appends_tagged_cve_signals(self):
        payload = json.dumps({"vulnerabilities": [
            {"cve": {"id": "CVE-2026-1", "published": "2026-11-04T00:00:00",
                     "descriptions": [{"lang": "en",
                                       "value": "Prompt injection in a model tool."}]}},
            {"cve": {"id": "CVE-2026-2", "published": "2026-11-04T00:00:00",
                     "descriptions": [{"lang": "en", "value": "Unrelated."}]}},
        ]})
        signals = []
        with patch("urllib.request.urlopen", return_value=_Response(payload)), \
                patch.object(server, "broadcast_sse") as broadcast:
            server._check_watchtower_source({
                "type": "nvd", "name": "NVD Prompt Injection",
                "keyword": "prompt injection", "tag": "cve",
            }, signals)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0]["tag"], "cve")
        self.assertIn("CVE-2026-1", signals[0]["title"])
        self.assertEqual(signals[0]["url"],
                         "https://nvd.nist.gov/vuln/detail/CVE-2026-1")
        self.assertEqual(broadcast.call_count, 2)


class WatchtowerDigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.signals = self.dir / "signals.json"
        self.cursor = self.dir / "cursor.json"
        self.signals.write_text(json.dumps([
            {"source": "A", "tag": "jailbreak", "title": "old", "time": "2026-11-04T10:00:00"},
            {"source": "A", "tag": "jailbreak", "title": "new", "time": "2026-11-04T12:00:00"},
            {"source": "B", "tag": "other", "title": "noise", "time": "2026-11-04T13:00:00"},
        ]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def digest(self, since="", tags=None):
        with patch.object(server, "WATCHTOWER_SIGNALS", self.signals), \
                patch.object(server, "WATCHTOWER_CURSOR", self.cursor):
            return server.watchtower_digest(since, tags=tags)

    def test_digest_filters_by_tag_and_orders_oldest_first(self):
        self.assertEqual([s["title"] for s in self.digest(tags=["jailbreak"])], ["old", "new"])

    def test_cursor_hides_processed_signals(self):
        self.cursor.write_text(json.dumps({"last_processed": "2026-11-04T10:00:00"}),
                               encoding="utf-8")
        self.assertEqual([s["title"] for s in self.digest(tags=["jailbreak"])], ["new"])

    def test_ack_never_moves_cursor_backwards(self):
        self.cursor.write_text(json.dumps({"last_processed": "2026-11-04T12:00:00"}),
                               encoding="utf-8")
        handler = Mock()
        handler._read_body.return_value = json.dumps({"since": "2026-11-04T10:00:00"})
        with patch.object(server, "WATCHTOWER_CURSOR", self.cursor), \
                patch.object(server, "WATCHTOWER_DIR", self.dir), \
                patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "broadcast_sse") as broadcast:
            server.Handler._api_watchtower_ack(handler)
        payload = handler._send_json.call_args.args[0]
        self.assertEqual(payload["cursor"], "2026-11-04T12:00:00")
        self.assertTrue(payload["unchanged"])
        broadcast.assert_not_called()

    def test_ack_advances_cursor(self):
        handler = Mock()
        handler._read_body.return_value = json.dumps({"since": "2026-11-04T12:00:00"})
        with patch.object(server, "WATCHTOWER_CURSOR", self.cursor), \
                patch.object(server, "WATCHTOWER_DIR", self.dir), \
                patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "broadcast_sse"):
            server.Handler._api_watchtower_ack(handler)
        saved = json.loads(self.cursor.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_processed"], "2026-11-04T12:00:00")

    def test_digest_endpoint_reports_count(self):
        handler = Mock()
        with patch.object(server, "WATCHTOWER_SIGNALS", self.signals), \
                patch.object(server, "WATCHTOWER_CURSOR", self.cursor):
            server.Handler._api_watchtower_digest(handler, {"tags": ["jailbreak"]})
        payload = handler._send_json.call_args.args[0]
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["signals"][0]["title"], "old")


if __name__ == "__main__":
    unittest.main()
