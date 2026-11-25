import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import mission_attachments as ma
import server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class SaveAttachmentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_four_types_saved_with_sniffed_extension(self):
        saved = ma.save_attachments([
            {"name": "a.png", "data": b64(PNG)},
            {"name": "b.jpeg", "data": b64(JPEG)},
            {"name": "c.gif", "data": "data:image/gif;base64," + b64(GIF)},
            {"name": "d.webp", "data": b64(WEBP)},
        ], self.root)
        self.assertEqual([s["type"] for s in saved], ["png", "jpg", "gif", "webp"])
        for item, raw in zip(saved, (PNG, JPEG, GIF, WEBP)):
            path = Path(item["path"])
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(item["size"], len(raw))
            self.assertEqual(path.parent.parent, self.root / "attachments")

    def test_extension_comes_from_bytes_not_name(self):
        saved = ma.save_attachments([{"name": "payload.html", "data": b64(PNG)}], self.root)
        self.assertEqual(Path(saved[0]["path"]).name, "payload.png")

    def test_path_traversal_name_stays_in_batch_dir(self):
        saved = ma.save_attachments([{"name": "..\\..\\..\\evil.png", "data": b64(PNG)}], self.root)
        path = Path(saved[0]["path"])
        self.assertEqual(path.parent.parent, (self.root / "attachments").resolve())
        self.assertEqual(path.name, "evil.png")

    def test_duplicate_names_are_deduplicated(self):
        saved = ma.save_attachments([{"name": "x.png", "data": b64(PNG)}] * 3, self.root)
        self.assertEqual([Path(s["path"]).name for s in saved], ["x.png", "x-2.png", "x-3.png"])

    def test_turkish_names_are_transliterated_not_dropped(self):
        # Regression: "yağmur ormanı.png" landed as "ya-mur-orman-.png".
        names = ["yağmur ormanı.png", "ŞİMŞEK ığdır.png", "çöp üzüm.png", "Ekran görüntüsü.png"]
        saved = ma.save_attachments([{"name": n, "data": b64(PNG)} for n in names], self.root)
        self.assertEqual([Path(s["path"]).name for s in saved],
                         ["yagmur-ormani.png", "SIMSEK-igdir.png", "cop-uzum.png", "Ekran-goruntusu.png"])
        self.assertEqual([s["name"] for s in saved], names, "the label keeps the original letters")

    def test_decomposed_label_is_normalised_to_nfc(self):
        nfd = "yag\u0306mur.png"  # what a macOS picker hands out for "yağmur.png"
        saved = ma.save_attachments([{"name": nfd, "data": b64(PNG)}], self.root)
        self.assertEqual(saved[0]["name"], "yağmur.png")
        self.assertEqual(Path(saved[0]["path"]).name, "yagmur.png")

    def test_rejections_write_nothing(self):
        cases = [
            [{"name": "a.txt", "data": b64(b"hello world, not an image")}],
            [{"name": "a.png", "data": "!!not base64!!"}],
            [{"name": "a.png", "data": b64(PNG)}] * (ma.MAX_ATTACHMENTS + 1),
            [{"name": "big.png", "data": b64(PNG + b"\x00" * ma.MAX_TOTAL_BYTES)}],
            "not a list",
            [{"name": "ok.png", "data": b64(PNG)}, {"name": "bad.svg", "data": b64(b"<svg/>")}],
        ]
        for items in cases:
            with self.subTest(items=str(items)[:60]):
                with self.assertRaises(ValueError):
                    ma.save_attachments(items, self.root)
        self.assertFalse((self.root / "attachments").exists())

    def test_prompt_block_lists_every_path(self):
        saved = ma.save_attachments([{"name": "shot.png", "data": b64(PNG)}], self.root)
        block = ma.prompt_block(saved)
        self.assertIn("ATTACHED PHOTOS (1)", block)
        self.assertIn(saved[0]["path"], block)
        self.assertEqual(ma.prompt_block([]), "")


class LaunchHandlerTests(unittest.TestCase):
    def _launch(self, body, workshop):
        handler = server.Handler.__new__(server.Handler)
        handler._read_body = Mock(return_value=json.dumps(body))
        handler._send_json = Mock()
        with patch.object(server, "_require_auth", return_value=True), \
                patch.object(server, "WORKSHOP_DIR", workshop), \
                patch.object(server, "launch_session", return_value={"id": "sess-1"}) as launch, \
                patch.object(server, "audit_log") as audit:
            handler._api_launch()
        return handler._send_json.call_args, launch, audit

    def test_photos_reach_the_agent_prompt_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent, launch, audit = self._launch({
                "prompt": "Describe this screenshot.", "runtime": "codex",
                "attachments": [{"name": "screen.png", "data": b64(PNG)}],
            }, Path(tmp))
            prompt = launch.call_args.args[0]
            self.assertTrue(prompt.startswith("Describe this screenshot."))
            saved = list((Path(tmp) / "attachments").rglob("screen.png"))
            self.assertEqual(len(saved), 1)
            self.assertIn(str(saved[0].resolve()), prompt)
        self.assertEqual(sent.args[0]["attachments"], [{"name": "screen.png", "type": "png", "size": len(PNG)}])
        self.assertEqual(audit.call_args.kwargs["attachments"], 1)

    def test_bad_photo_is_a_400_and_no_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent, launch, _ = self._launch({
                "prompt": "x", "runtime": "codex",
                "attachments": [{"name": "a.png", "data": b64(b"not an image at all")}],
            }, Path(tmp))
        self.assertEqual(sent.args[1], 400)
        launch.assert_not_called()

    def test_no_attachments_leaves_prompt_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent, launch, _ = self._launch({"prompt": "plain", "runtime": "codex"}, Path(tmp))
        self.assertEqual(launch.call_args.args[0], "plain")
        self.assertNotIn("attachments", sent.args[0])


if __name__ == "__main__":
    unittest.main()
