"""Boundary tests for DELETE /api/file.

The workshop tree is served to the browser, so a delete handler that follows a
crafted path back out of it is remote file removal. These tests pin the
containment checks without starting a server: the handler only needs
_send_json, so a stub object drives it directly.
"""

import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402

WORKSHOP = server.WORKSHOP_DIR.resolve()


class StubHandler:
    def __init__(self):
        self.code = None
        self.body = None

    def _send_json(self, obj, code=200):
        self.code = code
        self.body = obj


def delete(path):
    handler = StubHandler()
    server.Handler._api_file_delete(handler, {"path": [str(path)]})
    return handler.code, handler.body


class FileDeleteBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.victim = WORKSHOP / "__delete_test__.tmp"
        self.victim.write_text("temporary", encoding="utf-8")
        self.victim_dir = WORKSHOP / "__delete_test_dir__"
        self.victim_dir.mkdir(exist_ok=True)

    def tearDown(self):
        for p in (self.victim,):
            if p.exists():
                p.unlink()
        if self.victim_dir.exists():
            self.victim_dir.rmdir()

    def test_deletes_a_file_inside_the_workshop(self):
        code, body = delete(self.victim)
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))
        self.assertFalse(self.victim.exists())

    def test_refuses_a_path_outside_the_workshop(self):
        # A real file on the other side of the boundary: a synthetic path proves
        # nothing about whether the delete actually happened. Kept in the temp
        # dir so this test runs the same on Windows and POSIX.
        with tempfile.TemporaryDirectory() as tmp:
            outside = pathlib.Path(tmp) / "outside.txt"
            outside.write_text("keep me", encoding="utf-8")
            code, body = delete(outside)
            self.assertEqual(code, 403)
            self.assertIn("access denied", body.get("error", ""))
            self.assertTrue(outside.exists())

    def test_refuses_traversal(self):
        code, body = delete(WORKSHOP / ".." / ".." / "server.py")
        self.assertEqual(code, 403)
        self.assertTrue((REPO_ROOT / "server.py").exists())

    def test_refuses_the_workshop_root(self):
        code, body = delete(WORKSHOP)
        self.assertEqual(code, 400)
        self.assertIn("workshop root", body.get("error", ""))
        self.assertTrue(WORKSHOP.exists())

    def test_refuses_a_directory(self):
        code, body = delete(self.victim_dir)
        self.assertEqual(code, 400)
        self.assertIn("directory", body.get("error", ""))
        self.assertTrue(self.victim_dir.exists())

    def test_missing_file_is_404(self):
        code, body = delete(WORKSHOP / "__no_such_file__.tmp")
        self.assertEqual(code, 404)

    def test_empty_path_is_400(self):
        handler = StubHandler()
        server.Handler._api_file_delete(handler, {"path": [""]})
        self.assertEqual(handler.code, 400)
        self.assertIn("path required", handler.body.get("error", ""))


if __name__ == "__main__":
    unittest.main()
