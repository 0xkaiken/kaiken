import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class NativePromptTests(unittest.TestCase):
    def translate(self, text):
        return server.windows_native_prompt(text, windows=True)

    def test_commands_and_shortcuts_become_windows_ones(self):
        self.assertEqual(self.translate("Run python3 tools/x.py --help"), "Run python tools/x.py --help")
        self.assertEqual(self.translate("use Cmd+Return, then Cmd+V"), "use Ctrl+Enter, then Ctrl+V")
        self.assertEqual(self.translate('{"key":"Meta+Enter"}'), '{"key":"Control+Enter"}')

    def test_temp_paths_point_at_a_real_directory_without_spaces(self):
        with patch.object(server, "_prompt_temp_dir", return_value="C:/Temp/"):
            self.assertEqual(self.translate("save to /tmp/proof.png"), "save to C:/Temp/proof.png")
        self.assertNotIn(" ", server._prompt_temp_dir())
        self.assertTrue(server._prompt_temp_dir().endswith("/"))

    def test_lookalikes_are_left_alone(self):
        for text in ("https://example.com/tmp/a.png", "C:/work/tmp/a.png", "./tmp/a.png", "mypython3x", "Safari review"):
            self.assertEqual(self.translate(text), text)

    def test_translation_is_idempotent(self):
        once = self.translate("python3 a.py > /tmp/b.txt, Cmd+K")
        self.assertEqual(self.translate(once), once)

    def test_gauntlet_payload_paths_stay_literal(self):
        text = "Payload: /tmp/gauntlet_payload_1_2.txt. Send: python3 rt_send.py < /tmp/attack.txt"
        self.assertEqual(self.translate(text), text)

    def test_other_platforms_are_unchanged(self):
        text = "python3 a.py > /tmp/b.txt, Cmd+K"
        self.assertEqual(server.windows_native_prompt(text, windows=False), text)


class PromptDeliveryTests(unittest.TestCase):
    def delivered(self, **options):
        session = server.Session("native-fixture", "Run python3 a.py > /tmp/b.txt", runtime="codex", **options)
        captured = {}

        def spawn(_command, input_text=None, **_kwargs):
            captured["prompt"] = input_text
            process = Mock()
            process.poll.return_value = None
            return process

        with patch.object(session, "_initial_prompt", return_value=session.prompt), \
                patch.object(server, "launch_agent_process", side_effect=spawn), \
                patch.object(session, "_save_session_record"), patch.object(session, "_job_track"), \
                patch.object(session, "_read_output"), patch.object(session, "_idle_watchdog"), \
                patch.object(server, "broadcast_sse"), patch.object(session, "_capture_target_chat_offset"), \
                patch.object(server, "windows_native_prompt", side_effect=lambda text: server_translate(text)):
            session._start_inner()
        return captured["prompt"]

    def test_normal_mission_reaches_the_agent_translated(self):
        self.assertIn("python a.py", self.delivered())

    def test_custom_mission_reaches_the_agent_as_written(self):
        self.assertIn("python3 a.py > /tmp/b.txt", self.delivered(custom_prompt=True))


real_translate = server.windows_native_prompt


def server_translate(text):
    return real_translate(text, windows=True)


class SourceTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_no_page_tells_an_agent_to_use_safari(self):
        for page in ("index.html", "redteam.html", "arcade.html", "server.py"):
            self.assertNotIn("Safari", (self.ROOT / page).read_text(encoding="utf-8"), page)

    def test_computer_use_preset_uses_firefox(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        preset = re.search(r"COMPUTER_USE: `(.*?)`,", source, re.S).group(1)
        self.assertIn("Firefox", preset)

    def test_notification_file_links_do_not_escape_windows_paths_into_inline_js(self):
        # A raw `C:\work\...\trophy\x.txt` inside viewFile('...') is parsed as
        # JS escapes (\r, \t) and becomes an unopenable path. Paths must ride
        # through a data attribute and be read at runtime instead.
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"viewFile\('\$\{")

    def test_alerts_and_inbox_entries_persist_and_open_the_shared_viewer(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("localStorage.setItem(NOTIF_LS_KEY", source)
        self.assertIn("openNotificationDetail(this.dataset.notifId)", source)
        self.assertIn("openBriefingEntry(", source)
        self.assertIn("renderViewerPanel(", source)
        self.assertIn("data-question-path=", source)
        self.assertIn("function fullTime(", source)
        self.assertIn("deleteAllBriefing", source)
        self.assertIn("deleteAllQuestions", source)

    def test_session_cards_show_a_task_label_not_a_raw_id(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function sessionName(", source)
        self.assertIn("function cleanPromptPreview(", source)
        self.assertIn("const label = sessionName(s);", source)
        self.assertIn("cleanPromptPreview(s.prompt", source)

    def test_session_cards_show_kind_model_and_agent(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function sessionKind(", source)
        self.assertIn("function kindColor(", source)
        self.assertIn("function humanizeAgent(", source)
        self.assertIn("SESSION_KIND_COLORS", source)
        self.assertIn("agent: ${escHtml(humanizeAgent(s.agent))}", source)
        self.assertIn("body.preset = _presetName", source)

    def test_ship_uses_the_start_prompt_and_deepseek(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('const SHIP_START_PROMPT = "{{SHIP_START_PROMPT_ESCAPED}}";', source)
        self.assertIn("SHIP: `${SHIP_START_PROMPT}", source)
        self.assertIn("_presetModel = 'opencode-go/deepseek-v4.1-flash'", source)
        self.assertIn("opencode run --model opencode-go/deepseek-v4.1-flash", source)

    def test_presets_close_the_watchtower_loop(self):
        source = (self.ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("WATCHTOWER DIGEST", source)
        self.assertIn('source "watchtower"', source)
        self.assertIn("api/watchtower/digest?tags=jailbreak", source)
        self.assertIn("api/watchtower/ack", source)
        self.assertIn('preset "SHIP"', source)
        self.assertIn("Log the ship in the shared shipping log", source)
        self.assertIn("nothing reaches the Briefing", source)
        self.assertIn("opencode run --model opencode-go/deepseek-v4.1-flash", source)
        self.assertNotIn("**OpenRouter** as the model gateway", source)

    def test_desktop_mode_is_told_how_to_start_firefox(self):
        instructions = server.desktop_instructions("fixture")
        self.assertIn("Mozilla Firefox", instructions)
        self.assertIn("firefox", instructions.lower())
        self.assertTrue(server.firefox_path().lower().endswith(".exe"))


if __name__ == "__main__":
    unittest.main()
