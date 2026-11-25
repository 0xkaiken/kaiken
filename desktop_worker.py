"""Persistent Python worker for desktop Computer Use (`desktop_py`).

One worker per Computer Use session. The MCP server sends a JSON line with
Python source, the worker executes it in a namespace that survives between
calls, and returns stdout plus only the screenshots the script passed to
``display()``.

Every desktop helper goes through the command server's batch endpoint with the
session's Computer Use capability, so ownership, pause, focus and target
validation stay authoritative. This is code execution as an *interface*, not a
bypass: raw filesystem or input access is no greater than the agent's shell.

Protocol (one JSON object per line, stdout):
  in:  {"id": 1, "code": "print('hi')"}
  out: {"id": 1, "ok": true, "stdout": "hi\n", "images": [], "error": null}
"""
import argparse
import base64
import contextlib
import io
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request

MAX_STDOUT = 8000
MAX_IMAGES = 3
POINTER_STEPS = {"click", "double_click", "right_click", "move", "drag", "scroll"}
HELPERS = """\
Desktop helpers (persistent across calls):
  act(actions, screenshot=False, settle_ms=0)          one HTTP request
  batch(actions, **options)                            alias for act; batch consecutive steps
  click(x, y, button="left", count=1, keys=None)     ctrl-click: keys=["CTRL"]
  double_click(x, y, keys=None) / right_click(x, y, keys=None)
  move(x, y) / drag(x, y, to_x, to_y, keys=None)
  scroll(x, y, delta=-3, horizontal=False, keys=None)  delta = wheel notches
  type_text(text, window_id=None) / key("Ctrl+S", window_id=None)
  wait(ms) / activate(window_id) / launch("app.exe", ["arg"])
  windows() / foreground() / find_window("title") / focus_window("title")
  shot(windows=False) -> {"image": b64|None, "result": dict}; display() it
  zoom(x, y, w, h, scale=2) -> same dict, but the region at native resolution.
      Use it before clicking a control whose label the full frame cannot render.
  note(text) / read_notes()   append/read the mission notes file
  must(result) -> raises unless result["ok"]
  display(value) -> text prints; dict with image / PIL Image attaches a JPEG
Intermediate actions never capture or settle. Only shot()/display() return an image.
"""


class DesktopAPI:
    def __init__(self, session, port):
        self.session = str(session)
        self.url = f"http://127.0.0.1:{int(port)}/api/desktop/batch"
        self.headers = {"Content-Type": "application/json"}
        self.last_frame_id = None
        agent = os.environ.get("PLINY_AGENT_TOKEN", "")
        capability = os.environ.get("PLINY_CU_TOKEN", "")
        if agent:
            self.headers["Authorization"] = "Bearer " + agent
        if capability:
            self.headers["X-Pliny-CU-Token"] = capability

    def batch(self, actions, *, screenshot=False, windows=False, settle_ms=0,
              timeout_ms=None, inline_image=True, crop=None, http_timeout=60):
        # Intermediate helpers must not pay for a frame they never read. Only
        # shot() asks for screenshot=True; the 150ms settle is opt-in too.
        steps = []
        for step in actions:
            if not isinstance(step, dict):
                steps.append(step)
                continue
            step = dict(step)
            if (self.last_frame_id and "frame_id" not in step
                    and step.get("action") in POINTER_STEPS):
                step["frame_id"] = self.last_frame_id
            steps.append(step)
        payload = {"session_id": self.session, "actions": steps,
                   "screenshot": bool(screenshot), "windows": bool(windows),
                   "settle_ms": int(settle_ms), "inline_image": bool(inline_image)}
        if isinstance(crop, dict):
            payload["crop"] = crop
        if timeout_ms is not None:
            payload["timeout_ms"] = int(timeout_ms)
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                result = json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                result = {"ok": False, "error": f"HTTP {error.code}"}
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            result = {"ok": False, "error": f"Desktop service unreachable: {error}"}
        if isinstance(result, dict):
            frame = result.get("frame")
            if isinstance(frame, dict) and frame.get("frame_id"):
                self.last_frame_id = str(frame["frame_id"])
        return result


def notes_path(session):
    workshop = os.environ.get("PLINY_WORKSHOP") or os.getcwd()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "session"))[:80]
    return os.path.join(workshop, f"cu-notes-{safe}.md")


def build_helpers(api, images, session):
    def act(actions, **options):
        return api.batch(actions, **options)

    def batch(actions, **options):
        return act(actions, **options)

    def pointer(name, x, y, **fields):
        step = {"action": name, "x": x, "y": y}
        step.update({key: value for key, value in fields.items() if value is not None})
        return act([step])

    def click(x, y, button="left", count=1, keys=None):
        return pointer("click", x, y, button=button, count=count, keys=keys)

    def double_click(x, y, keys=None):
        return pointer("click", x, y, count=2, keys=keys)

    def right_click(x, y, keys=None):
        return pointer("click", x, y, button="right", keys=keys)

    def move(x, y):
        return pointer("move", x, y)

    def drag(x, y, to_x, to_y, keys=None):
        return pointer("drag", x, y, to_x=to_x, to_y=to_y, keys=keys)

    def scroll(x, y, delta=-3, horizontal=False, keys=None):
        return pointer("scroll", x, y, delta=delta, horizontal=horizontal, keys=keys)

    def type_text(text, window_id=None):
        return act([{"action": "type", "text": text, "window_id": window_id}])

    def key(combo, window_id=None):
        return act([{"action": "press", "key": combo, "window_id": window_id}])

    def wait(ms):
        return act([{"action": "wait", "ms": ms}], screenshot=False)

    def activate(window_id):
        return act([{"action": "activate", "window_id": window_id}], screenshot=False)

    def launch(app, args=None):
        return act([{"action": "launch", "app": app, "args": list(args or [])}])

    def windows():
        return act([], screenshot=False, windows=True).get("windows", [])

    def foreground():
        return act([], screenshot=False).get("foreground", {})

    def find_window(title):
        needle = str(title).lower()
        for window in windows():
            if needle in str(window.get("title", "")).lower():
                return window
        return None

    def focus_window(title):
        window = find_window(title)
        if not window:
            return {"ok": False, "error": f"no window matching {title!r}"}
        return activate(window["id"])

    def shot(windows=False, settle_ms=0, crop=None, scale=2.0):
        options = {"screenshot": True, "windows": windows, "inline_image": True, "settle_ms": settle_ms}
        if isinstance(crop, dict):
            options["crop"] = {**crop, "scale": crop.get("scale", scale)}
        result = act([], **options)
        image = result.pop("image", None) if isinstance(result, dict) else None
        return {"image": image, "result": result}

    def zoom(x, y, width, height, scale=2.0, windows=False):
        """The region at native resolution: toolbar icons, dialog text, status bars.

        Use it before clicking any control whose label the full frame cannot
        render, and again when a click did something unexpected. Guessing the
        same control twice is the failure this exists to prevent.
        """
        return shot(windows=windows, crop={"x": x, "y": y, "width": width, "height": height,
                                           "scale": scale})

    def must(result):
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(json.dumps(result, ensure_ascii=False)[:2000])
        return result

    notes_file = notes_path(session)

    def note(text):
        line = str(text).strip()
        if not line:
            return {"ok": False, "error": "empty note"}
        with open(notes_file, "a", encoding="utf-8") as handle:
            handle.write("- " + line + "\n")
        return {"ok": True, "path": notes_file}

    def read_notes():
        try:
            with open(notes_file, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""

    def display(value):
        if isinstance(value, dict) and value.get("image"):
            if len(images) < MAX_IMAGES:
                images.append(value["image"])
            else:
                print("[display] image limit reached; not attached")
        elif hasattr(value, "save"):
            try:
                from PIL import Image
                if isinstance(value, Image.Image):
                    buffer = io.BytesIO()
                    value.convert("RGB").save(buffer, "JPEG", quality=75)
                    if len(images) < MAX_IMAGES:
                        images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
                    else:
                        print("[display] image limit reached; not attached")
                    return
            except Exception as error:
                print(f"[display] {error}")
            print(str(value))
        else:
            print(str(value))

    return {
        "act": act, "batch": batch, "click": click, "double_click": double_click,
        "right_click": right_click, "move": move, "drag": drag, "scroll": scroll,
        "type_text": type_text, "key": key, "wait": wait, "activate": activate,
        "launch": launch, "windows": windows, "foreground": foreground,
        "find_window": find_window, "focus_window": focus_window, "shot": shot, "zoom": zoom,
        "must": must, "display": display, "note": note, "read_notes": read_notes,
        "sleep": time.sleep, "time": time, "HELPERS": HELPERS,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--port", type=int, default=8888)
    options = parser.parse_args()

    api = DesktopAPI(options.session, options.port)
    images = []
    namespace = {"__name__": "desktop_py"}
    namespace.update(build_helpers(api, images, options.session))

    # The protocol owns stdin; a script calling input() must not eat the next
    # request, so execution sees an empty stream.
    protocol_in = sys.stdin
    for line in protocol_in:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        images.clear()
        out = io.StringIO()
        error = None
        try:
            sys.stdin = io.StringIO("")
            with contextlib.redirect_stdout(out):
                exec(compile(str(message.get("code", "")), "<desktop_py>", "exec"), namespace)
        except BaseException:
            error = traceback.format_exc(limit=8)
        finally:
            sys.stdin = protocol_in
        reply = {"id": message.get("id"), "ok": error is None,
                 "stdout": out.getvalue()[:MAX_STDOUT], "images": images[:MAX_IMAGES],
                 "error": error[:2000] if error else None}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
