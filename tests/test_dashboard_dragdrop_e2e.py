"""Real-browser check: Attach photos drop zone, Turkish labels, agent-log math.

Serves the working tree with a throwaway static server (no dashboard API, no
credentials) and drives headless Chromium. Skips when Playwright or its
browser is missing.

Run: python -m pytest -q tests/test_dashboard_dragdrop_e2e.py
"""

import base64
import functools
import http.server
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

# 1x1 PNG.
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

DROP_JS = """
async ([sel, name, b64]) => {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bytes], name, { type: 'image/png' }));
  const el = document.querySelector(sel);
  for (const type of ['dragenter', 'dragover']) {
    el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
  }
  const highlighted = el.classList.contains('is-dragover') || el.classList.contains('mission-attach-drop');
  const drop = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
  el.dispatchEvent(drop);
  return { highlighted, prevented: drop.defaultPrevented,
           cleared: !el.classList.contains('is-dragover') && !el.classList.contains('mission-attach-drop') };
}
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class DashboardDragDropE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(_Quiet, directory=str(ROOT))
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/index.html"
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.pw.stop()
            cls.httpd.shutdown()
            raise unittest.SkipTest(f"chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.goto(self.url, wait_until="domcontentloaded")
        self.page.wait_for_function("typeof MISSION_ATTACH !== 'undefined' && !!window.LogRender")
        self.page.evaluate("MISSION_ATTACH.clear()")

    def tearDown(self):
        self.page.close()

    def _wait_count(self, n):
        self.page.wait_for_function(f"MISSION_ATTACH.count() === {n}", timeout=5000)

    def test_drop_on_attach_zone_keeps_turkish_name(self):
        hint = self.page.evaluate(
            "getComputedStyle(document.getElementById('mission-attach-summary'), '::before').content")
        self.assertIn("drag & drop", hint)
        res = self.page.evaluate(DROP_JS, ["#mission-attach-zone", "örnek dosya ğüçöşıİ.png", PNG_B64])
        self.assertEqual(res, {"highlighted": True, "prevented": True, "cleared": True})
        self._wait_count(1)
        label = self.page.text_content("#mission-attach-list .mission-attach-name")
        self.assertEqual(label, "örnek dosya ğüçöşıİ.png")
        payload = self.page.evaluate("MISSION_ATTACH.payload()")
        self.assertEqual(payload[0]["name"], "örnek dosya ğüçöşıİ.png")

    def test_drop_on_task_box_still_works(self):
        self.page.evaluate(DROP_JS, ["#prompt-input", "şema.png", PNG_B64])
        self._wait_count(1)

    def test_stray_drop_does_not_navigate(self):
        res = self.page.evaluate(DROP_JS, ["body", "x.png", PNG_B64])
        self.assertTrue(res["prevented"])
        self.assertEqual(self.page.evaluate("MISSION_ATTACH.count()"), 0)

    def test_log_markdown_turkish_and_katex(self):
        text = ("## Çözüm — ğüşıöç İĞÜŞÖÇ\n\n**kalın** ve `kod` ve $e^{i\\pi}+1=0$\n\n"
                "$$\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}$$\n\n- madde ğ\n- <img src=x onerror=alert(1)>")
        self.page.evaluate("""t => { const d = document.createElement('div'); d.id = 'mdt';
                                      document.body.appendChild(d); LogRender.fill(d, t); }""", text)
        self.page.wait_for_selector("#mdt .katex", timeout=5000)
        html = self.page.inner_html("#mdt")
        self.assertIn("Çözüm — ğüşıöç İĞÜŞÖÇ", self.page.text_content("#mdt .md-h2"))
        self.assertIn("<strong>kalın</strong>", html)
        self.assertEqual(self.page.locator("#mdt .katex-display").count(), 1)
        self.assertEqual(self.page.locator("#mdt img").count(), 0)


if __name__ == "__main__":
    unittest.main()
