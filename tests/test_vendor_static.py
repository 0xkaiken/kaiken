"""/vendor/ static route (KaTeX) and /log-render.js."""
import io
import unittest

import server


class _Fake(server.Handler):
    def __init__(self, path):  # no socket
        self.path = path
        self.headers = {}
        self.wfile = io.BytesIO()
        self.sent = []

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, key, value):
        self.sent.append((key, value))

    def end_headers(self):
        pass


def fetch(path):
    handler = _Fake(path)
    handler._serve_vendor(path)
    return handler.status, dict(handler.sent), handler.wfile.getvalue()


class VendorRouteTests(unittest.TestCase):
    def test_katex_assets_are_served_with_their_types(self):
        status, headers, body = fetch("/vendor/katex/katex.min.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertIn(b"katex", body[:2000].lower())
        status, headers, _ = fetch("/vendor/katex/katex.min.css")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/css;charset=utf-8"))
        status, headers, body = fetch("/vendor/katex/fonts/KaTeX_Main-Regular.woff2")
        self.assertEqual((status, headers["Content-Type"]), (200, "font/woff2"))
        self.assertTrue(body.startswith(b"wOF2"))

    def test_every_font_the_css_names_in_woff2_exists(self):
        import re
        css = (server.BASE_DIR / "vendor" / "katex" / "katex.min.css").read_text(encoding="utf-8")
        fonts = set(re.findall(r"url\((fonts/[^)]+\.woff2)\)", css))
        self.assertGreater(len(fonts), 10)
        for rel in fonts:
            self.assertEqual(fetch("/vendor/katex/" + rel)[0], 200, rel)

    def test_traversal_unknown_types_and_dotfiles_are_404(self):
        for path in ("/vendor/../server.py", "/vendor/%2e%2e/server.py", "/vendor/..%5cserver.py",
                     "/vendor/katex/../../.env", "/vendor/katex/LICENSE", "/vendor/katex/VERSION",
                     "/vendor/katex/", "/vendor/katex/missing.js", "/vendor/.hidden.js",
                     "/vendor/C:/Windows/win.ini"):
            self.assertEqual(fetch(path)[0], 404, path)

    def test_log_render_route_is_wired(self):
        source = (server.BASE_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn('elif path == "/log-render.js":', source)
        self.assertIn('elif path.startswith("/vendor/"):', source)


if __name__ == "__main__":
    unittest.main()
