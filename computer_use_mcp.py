"""Computer Use tools over MCP (stdio) for Claude Code, Codex and OpenCode.

Each tool call runs a whole action sequence and returns the resulting
screenshot inline, so an agent needs one model turn per observed step instead
of a shell call (process start + HTTP) followed by a separate image read.
All safety checks (ownership, pause, focus) stay in the command server.
"""

import argparse
import atexit
import base64
import io
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
BROWSER_GET = {"elements", "status", "cloudflare", "cookie_health"}
BROWSER_ENDPOINTS = {"navigate", "click", "type", "paste", "press", "scroll",
                     "click_element", "fill", "text"} | BROWSER_GET

DESKTOP_ACTIONS_HELP = (
    "Steps run in order and stop at the first failure. Each step is an object with \"action\" plus fields: "
    "launch {app:\"notepad.exe\", args:[]}; activate {window_id} (maximizes it and makes it the default "
    "target for type/press); click {x,y,button?,count?,keys?}; double_click {x,y}; right_click {x,y}; move {x,y}; "
    "drag {x,y,to_x,to_y,keys?}; scroll {x,y,delta,keys?} (negative = down; delta is wheel notches, -20..20, "
    "not pixels: -3 is about a screen; keys holds CTRL/SHIFT/ALT for ctrl-click or shift-click); type {text, window_id?}; "
    "press {key:\"Ctrl+S\", window_id?}; wait {ms<=10000}; clear_target {} (allow a deliberate click outside "
    "the activated window). Coordinates are pixels of the latest returned screenshot. After activate, input is "
    "validated against that window. A failed step returns a code (focus_changed, target_mismatch, stale_frame, "
    "desktop_paused, cancelled, deadline_exceeded, desktop_unavailable) and a recovery hint: follow it."
)

TOOLS = {
    "desktop": [
        {"name": "desktop_screen",
         "description": "Capture the selected Windows monitor now. Returns the screenshot, the foreground window "
                        "and (by default) the visible window list with ids for activate.",
         "inputSchema": {"type": "object", "properties": {
             "windows": {"type": "boolean", "description": "Include the window list (default true)."}}}},
        {"name": "desktop_act",
         "description": "Perform a sequence of mouse/keyboard actions on the real Windows desktop in ONE call, "
                        "then return a fresh screenshot of the result. Batch every step you can predict "
                        "(e.g. activate, click a field, type, press Enter). " + DESKTOP_ACTIONS_HELP,
         "inputSchema": {"type": "object", "required": ["actions"], "properties": {
             "actions": {"type": "array", "maxItems": 40, "items": {"type": "object"}},
             "screenshot": {"type": "boolean", "description": "Return a screenshot afterwards (default true)."},
             "crop": {"type": "object", "description": "Return only this region of the screenshot, at native "
                                                      "resolution: {x,y,width,height,scale}. Coordinates are "
                                                      "screenshot pixels, like click. Use it to read toolbar "
                                                      "icons, ribbon labels and dialog text that the full frame "
                                                      "renders too small to identify."},
             "settle_ms": {"type": "integer", "description": "Wait before the screenshot so the UI can update (default 150, max 3000)."},
             "timeout_ms": {"type": "integer", "description": "Time budget for the whole sequence (default 60000, max 120000)."},
             "windows": {"type": "boolean", "description": "Also return the window list (default false)."}}}},
        {"name": "desktop_py",
         "description": "Run Python in a persistent namespace that drives the real Windows desktop through the "
                        "same validated batch API. Use it for multi-step or repetitive work (CAD, dialogs, forms): "
                        "one script can click, type, wait and check the result without a model turn per action. "
                        "Helpers: shot(windows=False) -> {'image','result'}; zoom(x,y,w,h,scale=2) -> the same "
                        "region at native resolution for labels the full frame cannot render; batch(actions) for "
                        "one request; click(x,y,keys=['CTRL']); double_click; "
                        "right_click; move; drag; scroll(x,y,delta); type_text; key; wait; activate; launch; "
                        "windows(); foreground(); find_window(text); focus_window(text); must(result); "
                        "note(text); read_notes(); display(value). Intermediate actions do not screenshot or "
                        "settle; only images passed to display() are returned, so do not display every "
                        "step; print() concise text. Variables persist between calls; a timeout restarts the worker "
                        "and loses them.",
         "inputSchema": {"type": "object", "required": ["code"], "properties": {
             "code": {"type": "string", "description": "Python source. print() text; display(shot()) for an image."},
             "timeout_ms": {"type": "integer", "description": "Execution budget (default 60000, max 120000)."}}}},
    ],
    "browser": [
        {"name": "browser_screen",
         "description": "Screenshot of the dedicated agent browser plus its current URL/title.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "browser_act",
         "description": "Run several dedicated-browser API calls in ONE tool call, then return a screenshot. "
                        "Each step is {endpoint, body}. Endpoints: navigate {url}, click_element {text|selector|index}, "
                        "fill {text|selector, value}, click {x,y}, type {text}, paste {text}, press {key}, "
                        "scroll {delta_y}, text {selector?}, elements, status, cloudflare, cookie_health. Prefer click_element/fill over coordinates.",
         "inputSchema": {"type": "object", "required": ["steps"], "properties": {
             "steps": {"type": "array", "maxItems": 20, "items": {"type": "object", "required": ["endpoint"], "properties": {
                 "endpoint": {"type": "string"}, "body": {"type": "object"}}}},
             "screenshot": {"type": "boolean", "description": "Return a screenshot afterwards (default true)."}}}},
    ],
}


POINTER_STEPS = {"click", "double_click", "right_click", "move", "drag", "scroll"}
PYTHON_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_worker.py")


class _PythonWorker:
    """One persistent desktop_worker.py child; restarted after a timeout or death."""

    def __init__(self, session_id, port):
        self.session_id = session_id
        self.port = port
        self.proc = None
        self.lines = None
        self.counter = 0
        self.lock = threading.Lock()

    def _start(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", PYTHON_WORKER,
             "--session", str(self.session_id), "--port", str(self.port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, process):
        for line in process.stdout:
            try:
                self.lines.put(json.loads(line))
            except ValueError:
                continue

    def run(self, code, timeout_ms):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.counter += 1
            request = {"id": self.counter, "code": code}
            try:
                self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.stop()
                return {"ok": False, "error": "desktop worker is unavailable; retry the call"}
            try:
                reply = self.lines.get(timeout=max(1.0, timeout_ms / 1000))
            except queue.Empty:
                self.stop()
                return {"ok": False,
                        "error": f"desktop_py timed out after {timeout_ms}ms; the worker restarted, so variables are gone"}
            if not isinstance(reply, dict) or reply.get("id") != self.counter:
                return {"ok": False, "error": "desktop worker returned an unexpected reply"}
            return reply

    def stop(self):
        process, self.proc = self.proc, None
        if process is None:
            return
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


class ComputerUseTools:
    def __init__(self, mode, session_id, server_port=8888, browser_port=8787, *, desktop_python=False):
        self.mode = mode
        self.desktop_python = mode == "desktop" and desktop_python is True
        self.session_id = session_id
        self.server_port = server_port
        # 127.0.0.1 avoids the IPv6-first localhost lookup on Windows.
        self.server = f"http://127.0.0.1:{server_port}"
        self.browser = f"http://127.0.0.1:{browser_port}"
        # The frame this agent last SAW. Pointer steps without an explicit
        # frame_id are pinned to it, so the server can tell a stale view from
        # a newer capture taken by the dashboard poller.
        self.last_frame_id = None
        self._pyworker = None

    def _request(self, url, payload=None, timeout=120):
        headers = {"Content-Type": "application/json"}
        capability = os.environ.get("PLINY_CU_TOKEN", "")
        if url.startswith(self.browser):
            # The browser sidecar accepts only this mission's capability.
            if capability:
                headers["Authorization"] = "Bearer " + capability
        else:
            token = os.environ.get("PLINY_AGENT_TOKEN") or os.environ.get("PLINY_AUTH_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
            if capability:
                headers["X-Pliny-CU-Token"] = capability
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            body = json.dumps({"ok": False, "error": f"Service unreachable: {error}"}).encode("utf-8")
            return 503, body, "application/json"

    def call(self, name, arguments):
        if name == "desktop_screen" and self.mode == "desktop":
            return self._desktop({"actions": [], "windows": arguments.get("windows", True)})
        if name == "desktop_act" and self.mode == "desktop":
            return self._desktop({key: arguments[key] for key in ("actions", "screenshot", "crop", "settle_ms", "timeout_ms", "windows") if key in arguments})
        if name == "desktop_py" and self.desktop_python:
            return self._python(arguments)
        if name == "browser_screen" and self.mode == "browser":
            return self._browser([{"endpoint": "status"}], True)
        if name == "browser_act" and self.mode == "browser":
            return self._browser(arguments.get("steps", []), arguments.get("screenshot", True))
        raise ValueError(f"Unknown tool for {self.mode} mode: {name}")

    def _desktop(self, payload):
        actions = payload.get("actions")
        if isinstance(actions, list) and self.last_frame_id:
            payload = {**payload, "actions": [
                {**step, "frame_id": self.last_frame_id}
                if isinstance(step, dict) and step.get("action") in POINTER_STEPS and "frame_id" not in step
                else step for step in actions]}
        status, body, _ = self._request(self.server + "/api/desktop/batch",
                                        {"session_id": self.session_id, "inline_image": True, **payload})
        try:
            result = json.loads(body or b"{}")
        except ValueError:
            result = {"ok": False, "error": body[:2000].decode("utf-8", "replace") if body else "empty response"}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "unexpected response"}
        image = result.pop("image", None)
        frame = result.get("frame") or {}
        if image and frame.get("frame_id"):
            self.last_frame_id = frame["frame_id"]
        failed = status >= 400 or not result.get("ok", False)
        content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        if image:
            content.append({"type": "image", "data": image, "mimeType": "image/jpeg"})
        return content, failed

    def python_worker(self):
        if self._pyworker is None:
            self._pyworker = _PythonWorker(self.session_id, self.server_port)
            atexit.register(self._pyworker.stop)
        return self._pyworker

    def _python(self, arguments):
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty string")
        if len(code) > 20000:
            raise ValueError("code is limited to 20000 characters")
        try:
            timeout = int(arguments.get("timeout_ms", 60000))
        except (TypeError, ValueError):
            raise ValueError("timeout_ms must be an integer")
        timeout = max(1000, min(timeout, 120000))
        result = self.python_worker().run(code, timeout)
        summary = {"ok": bool(result.get("ok")),
                   "stdout": result.get("stdout") or "",
                   "error": result.get("error")}
        content = [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]
        for image in (result.get("images") or [])[:3]:
            if isinstance(image, str) and image:
                content.append({"type": "image", "data": image, "mimeType": "image/jpeg"})
        return content, not summary["ok"]

    def _browser(self, steps, screenshot):
        if not isinstance(steps, list) or len(steps) > 20:
            raise ValueError("steps must be a list of at most 20 browser calls")
        results, failed = [], False
        for index, step in enumerate(steps):
            endpoint = str(step.get("endpoint", "")).strip("/") if isinstance(step, dict) else ""
            if endpoint not in BROWSER_ENDPOINTS:
                results.append({"index": index, "error": f"unsupported endpoint {endpoint!r}"})
                failed = True
                break
            body = None if endpoint in BROWSER_GET else (step.get("body") or {})
            status, raw, _ = self._request(f"{self.browser}/{endpoint}", body)
            try:
                parsed = json.loads(raw or b"{}")
            except ValueError:
                parsed = {"raw": raw[:2000].decode("utf-8", "replace")}
            results.append({"index": index, "endpoint": endpoint, "status": status, "result": parsed})
            if status >= 400 or (isinstance(parsed, dict) and parsed.get("error")):
                failed = True
                break
        content = [{"type": "text", "text": json.dumps({"ok": not failed, "results": results}, ensure_ascii=False)}]
        if screenshot and not any(r.get("status") == 423 for r in results):
            status, raw, kind = self._request(self.browser + "/screenshot")
            if status == 200 and kind.startswith("image/"):
                content.append({"type": "image", "data": to_jpeg(raw), "mimeType": "image/jpeg"})
        return content, failed


def to_jpeg(png):
    try:
        from PIL import Image
        out = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(out, "JPEG", quality=72)
        return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:
        return base64.b64encode(png).decode("ascii")


def mcp_server_spec(mode, session_id, server_port=8888, *, desktop_python=False):
    """Command line for this MCP server, shared by every agent runtime."""
    args = ["-X", "utf8", os.path.abspath(__file__), "--mode", mode,
            "--session", session_id, "--port", str(server_port)]
    if mode == "desktop" and desktop_python is True:
        args.append("--desktop-python")
    return {"command": sys.executable, "args": args}


def handle(tools, message):
    method = message.get("method")
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pliny_cu", "version": "1.0"}}
    if method == "tools/list":
        return {"tools": [tool for tool in TOOLS[tools.mode]
                          if tool["name"] != "desktop_py" or tools.desktop_python]}
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            content, failed = tools.call(params.get("name"), params.get("arguments") or {})
        except Exception as error:
            content, failed = [{"type": "text", "text": f"Error: {error}"}], True
        return {"content": content, "isError": failed}
    if method == "ping":
        return {}
    raise LookupError(method)


def serve(tools, stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # Notifications (initialized, cancelled) need no reply.
        try:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(tools, message)}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": message["id"],
                     "error": {"code": -32601, "message": "Method not found"}}
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("desktop", "browser"), required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--desktop-python", action="store_true")
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    serve(ComputerUseTools(args.mode, args.session, args.port, desktop_python=args.desktop_python))


if __name__ == "__main__":
    main()
