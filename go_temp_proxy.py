"""Local transparent proxy for the OpenCode Go endpoint, with parameter pinning.

WHY
Four config placements for request parameters were probed and none reach the
provider: model-level `options`, provider-level `options` scalars, config model
`body`, and config custom-variant `body` are all accepted by validation but
silently dropped (probes/temp_pin_probe.py, probes/maxtoken_probe.py,
probes/variant_body_probe.py, probes/model_body_probe.py). The one honored
control point is provider `options.baseURL` (probes/baseurl_oracle.py: a dead
endpoint hangs instead of succeeding). So parameters are pinned by sitting
between the CLI and opencode.ai and editing the request body.

WHAT IT DOES
- ThreadingHTTPServer on 127.0.0.1 with an OS-picked port.
- Forwards the request path, auth headers, and body to the real Go endpoint.
- Mutates ONLY the caller's pinned params (default: temperature) into the JSON
  body. Body is edited, never stored; headers are forwarded, never logged.
- Streams the upstream response straight back (works for SSE).

The credential boundary is untouched: the Authorization header is created by
the OpenCode CLI itself and merely passed through, the same way the CLI would
talk to opencode.ai directly.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.request
import urllib.error

UPSTREAM = "https://opencode.ai/inference/go/openai/v1"

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


class _PinHandler(BaseHTTPRequestHandler):
    pinned: dict = {}

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence the default stderr chatter
        return

    def _upstream_url(self):
        return UPSTREAM + self.path

    def _forward_headers(self):
        """Pass through every request header except hop-by-hop ones. Auth and
        routing headers created by the CLI (x-opencode-*, authorization) are
        forwarded untouched and never logged."""
        out = {}
        for name, value in self.headers.items():
            if name.lower() in HOP_BY_HOP:
                continue
            out[name] = value
        return out

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            edited = body
            if body:
                try:
                    doc = json.loads(body)
                    if isinstance(doc, dict) and self.pinned:
                        doc.update(self.pinned)
                        edited = json.dumps(doc).encode("utf-8")
                except ValueError:
                    pass
            headers = self._forward_headers()
            headers["Content-Length"] = str(len(edited))
            req = urllib.request.Request(self._upstream_url(), edited, headers,
                                         method="POST")
            resp = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type",
                             exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        except Exception as exc:  # transport-level failure surfaced as 502
            text = json.dumps({"error": {"message": "proxy failure: %s" % exc}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)
            return

        try:
            self.send_response(resp.status)
            ctype = resp.headers.get("Content-Type", "application/json")
            self.send_header("Content-Type", ctype)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(("%X\r\n" % len(chunk)).encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        finally:
            resp.close()


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # The CLI closes sockets abruptly mid-stream; that is normal
        # connection teardown, not an error worth a traceback.
        pass


class GoTempProxy:
    """Start/stop once per process; remembers its URL in .url."""

    def __init__(self, pinned: dict | None = None):
        self.pinned = dict(pinned or {})
        self._server: _QuietServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None

    def start(self) -> "GoTempProxy":
        handler = type("PinnedHandler", (_PinHandler,), {"pinned": self.pinned})
        server = _QuietServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        self._server = server
        self.url = "http://127.0.0.1:%d" % server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever,
                                        kwargs={"poll_interval": 0.2},
                                        name="go-temp-proxy", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self._server = None
        self.url = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
