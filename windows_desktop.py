"""Native Windows desktop adapter. No shell, service, or administrator rights required."""
import ctypes
from contextlib import contextmanager
from ctypes import wintypes as w
import base64
from collections import OrderedDict
import io
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from cu_capabilities import RECOVERY


class DesktopUnavailable(RuntimeError):
    pass


class DesktopFocusChanged(DesktopUnavailable):
    """The target lost foreground focus; this is not an operator pause."""


class TargetMismatch(DesktopFocusChanged):
    """The pointer target is not on the active target window."""


class StaleFrame(ValueError):
    """Coordinates came from a screenshot that no longer matches the screen."""


class ActionCancelled(PermissionError):
    """Input stopped by mission stop/cancel; partial progress is reported."""


class DeadlineExceeded(TimeoutError):
    """The batch ran past its time budget."""


def _frame_size(raw):
    """Model-facing frame size: PLINY_CU_FRAME_SIZE="1600x1000".

    ~1280px wide keeps text legible while halving image tokens compared with
    1600px, which directly shortens every agent turn. Density-heavy UIs (CAD
    ribbons, trading terminals) lose their labels at that size, so the size is
    an operator knob rather than a constant. A bad value falls back silently:
    an unreadable override must not take Computer Use down.
    """
    match = re.fullmatch(r"\s*(\d{3,5})\s*[x*]\s*(\d{3,5})\s*", str(raw or ""))
    if not match:
        return (1280, 800)
    width, height = int(match.group(1)), int(match.group(2))
    if not (640 <= width <= 3840 and 480 <= height <= 2160):
        return (1280, 800)
    return (width, height)


FRAME_SIZE = _frame_size(os.environ.get("PLINY_CU_FRAME_SIZE"))
# Cap on a zoomed crop: one zoom must stay cheaper than one full frame
# (1280x720 = 921,600 pixels), so the long edge and the pixel budget both bind.
MAX_CROP_SIDE = 1400
MAX_CROP_PIXELS = 900_000
HUD_TITLE_PREFIX = "Pliny Computer Use"
BATCH_LIMIT = 40
# Recent frames an agent may still address by frame_id. The dashboard poller
# captures too, so the newest frame is not necessarily the one the agent saw.
FRAME_HISTORY = 8
FRAME_MAX_AGE_S = float(os.environ.get("PLINY_CU_FRAME_MAX_AGE", "") or 300)
BATCH_DEADLINE_S = 60.0
BATCH_DEADLINE_MAX_S = 120.0
SNAPSHOT_ROOT = Path(tempfile.gettempdir()) / "pliny-cu"
GA_ROOT, GA_ROOTOWNER = 2, 3
POINTER_ACTIONS = ("click", "move", "drag", "scroll")
# Held during a pointer action. OpenAI's computer-use contract calls this
# `keys`; older agents (and the SolidWorks run) send `modifiers`. Both work.
MODIFIER_KEYS = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "option": 0x12,
    "win": 0x5B, "meta": 0x5B, "cmd": 0x5B, "super": 0x5B,
}


def _session_dir_name(session_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "unowned"))[:80] or "unowned"


class WindowsDesktop:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.paused = True
        self.operator_paused = False
        self.cancelled = threading.Event()
        self.last_image = b""
        self.last_capture = None
        # Full-resolution grab behind the last capture. Zoom crops come from
        # here, so toolbar labels survive the downscale to FRAME_SIZE.
        self.last_native = None
        self.frame = None
        self.frames = OrderedDict()
        self._frame_counter = 0
        self.target_window = None
        self.user = None
        self.monitor_id = None
        self.snapshot_path = SNAPSHOT_ROOT / "unowned" / "desktop_screen.png"
        self._pointer_state = {}
        self._pointer_serial = 0

    def _init(self):
        if os.name != "nt":
            raise DesktopUnavailable("Windows Desktop requires Windows")
        if self.user is not None:
            return
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.user.OpenInputDesktop.restype = w.HANDLE
        self.user.CloseDesktop.argtypes = [w.HANDLE]
        self.user.SwitchDesktop.argtypes = [w.HANDLE]
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.IsWindow.argtypes = [w.HWND]
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.GetWindowTextLengthW.argtypes = [w.HWND]
        self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        self.user.SetForegroundWindow.argtypes = [w.HWND]
        self.user.BringWindowToTop.argtypes = [w.HWND]
        self.user.WindowFromPoint.argtypes = [w.POINT]
        self.user.WindowFromPoint.restype = w.HWND
        self.user.GetAncestor.argtypes = [w.HWND, w.UINT]
        self.user.GetAncestor.restype = w.HWND
        self.user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        self.user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.SendInput.argtypes = [w.UINT, ctypes.c_void_p, ctypes.c_int]
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            self.user.SetProcessDPIAware()

    def _interactive(self):
        self._init()
        desktop = self.user.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            raise DesktopUnavailable("Desktop is locked or a secure/UAC screen is active. Operator must unlock/handle it.")
        try:
            if not self.user.SwitchDesktop(desktop):
                raise DesktopUnavailable("Secure or locked desktop is not controllable")
        finally:
            self.user.CloseDesktop(desktop)

    def monitors(self):
        self._init()
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("rect", w.RECT), ("work", w.RECT), ("flags", w.DWORD), ("device", w.WCHAR * 32)]
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)
        self.user.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MonitorInfo)]
        result = []
        @callback_type
        def visit(handle, dc, rect, param):
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if self.user.GetMonitorInfoW(handle, ctypes.byref(info)):
                r = info.rect
                result.append({"id": info.device, "primary": bool(info.flags & 1), "left": r.left,
                               "top": r.top, "width": r.right-r.left, "height": r.bottom-r.top})
            return True
        self.user.EnumDisplayMonitors.argtypes = [w.HDC, ctypes.POINTER(w.RECT), callback_type, w.LPARAM]
        self.user.EnumDisplayMonitors(None, None, visit, 0)
        return result

    # ── Session lifecycle ────────────────────────────────────────────────

    def session_dir(self, session_id):
        return SNAPSHOT_ROOT / _session_dir_name(session_id)

    def _clear_captures(self):
        self.last_image = b""
        self.last_capture = None
        # Full-resolution grab behind the last capture. Zoom crops come from
        # here, so toolbar labels survive the downscale to FRAME_SIZE.
        self.last_native = None
        self.frame = None
        self.frames.clear()

    def enable(self, session_id, monitor_id=None):
        with self.lock:
            self._interactive()
            monitors = self.monitors()
            selected = next((m for m in monitors if m["id"] == monitor_id), None)
            if monitor_id and not selected:
                raise ValueError("Selected monitor is no longer connected")
            selected = selected or next((m for m in monitors if m["primary"]), monitors[0])
            if self.owner != session_id:
                # Captures belong to one mission; never carry them into another.
                self._clear_captures()
                self._pointer_state = {}
            self.monitor_id = selected["id"]
            self.owner = session_id
            self.snapshot_path = self.session_dir(session_id) / "desktop_screen.png"
            self.target_window = None
            self.cancelled.clear()
            # Take Control survives a re-enable; release() clears it when a mission ends.
            self.paused = self.operator_paused

    def release(self, session_id=None):
        """Mission ended: lock agent input, drop its captures and temp files."""
        with self.lock:
            if session_id is not None and self.owner not in (None, session_id):
                return False
            owner = self.owner
            self.paused = True
            self.operator_paused = False
            self.cancelled.set()
            self.owner = None
            self._pointer_state = {}
            self.target_window = None
            self._clear_captures()
            directory = self.session_dir(owner) if owner else None
            self.snapshot_path = SNAPSHOT_ROOT / "unowned" / "desktop_screen.png"
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        return True

    def pause(self, paused):
        if paused:
            self.emergency_pause()
        with self.lock:
            self.operator_paused = bool(paused)
            self.paused = bool(paused)
            if paused:
                self.cancelled.set()
            else:
                self.cancelled.clear()

    def emergency_pause(self):
        # Signal without waiting for an ongoing input operation's lock.
        self.operator_paused = True
        self.paused = True
        self.cancelled.set()
        self._pointer_state = {}

    def pointer_activity(self):
        """Immutable snapshots: indicator polling must not wait on the action lock."""
        return self._pointer_state

    @contextmanager
    def _pointer_action(self):
        try:
            yield
        finally:
            if self._pointer_state.get("running"):
                self._pointer_state = {**self._pointer_state, "running": False}

    def _set_pointer_position(self, x, y):
        result = self.user.SetCursorPos(x, y)
        if result and not self.paused:
            self._pointer_state = {**self._pointer_state, "position": (x, y), "updated_at": time.monotonic()}
        return result

    def status(self):
        with self.lock:
            self._init()
            # "paused" means the operator has control; an idle desktop is only input_locked.
            return {"available": True, "mode": "desktop", "paused": self.operator_paused,
                    "input_locked": self.paused, "session_id": self.owner, "frame": self.frame, "monitors": self.monitors(), "monitor_id": self.monitor_id}

    # ── Capture ──────────────────────────────────────────────────────────

    def screenshot(self, remember=False):
        """Capture for observers (dashboard poller, operator view).

        These frames are not addressable by frame_id: only frames returned to
        the agent are, so background captures cannot evict the frame the
        agent is acting on.
        """
        self.capture(remember=remember)
        return self.last_image

    def jpeg(self, quality=72):
        """The latest capture as base64 JPEG, small enough to return inline to a model."""
        with self.lock:
            if self.last_capture is None:
                raise DesktopUnavailable("No desktop capture yet")
            out = io.BytesIO()
            self.last_capture.convert("RGB").save(out, "JPEG", quality=quality)
            return base64.b64encode(out.getvalue()).decode("ascii")

    def crop(self, box, frame=None, scale=2.0, quality=78):
        """A region of the last capture at native resolution, base64 JPEG.

        ``box`` uses the same pixel space as ``click()``: x, y, width, height
        of the frame the agent is looking at. The pixels come from the
        full-resolution grab, not the downscaled frame, so a 2x zoom restores
        the detail a dense ribbon or dialog loses on the way to FRAME_SIZE.

        Zooming is the difference between "click where the Circle icon should
        be" and "click the Circle icon". Without it an agent guesses, misses,
        and restarts the document.
        """
        from PIL import Image
        if not isinstance(box, dict):
            raise TypeError("crop must be an object with x, y, width, height")
        with self.lock:
            native = self.last_native
            if native is None:
                raise DesktopUnavailable("No desktop capture yet")
            f = frame or self.frame
            if not f:
                raise ValueError("Take a desktop screenshot before zooming")
            try:
                x = float(box.get("x", 0))
                y = float(box.get("y", 0))
                width = float(box.get("width", box.get("w", 0)))
                height = float(box.get("height", box.get("h", 0)))
            except (TypeError, ValueError):
                raise ValueError("crop x, y, width and height must be numbers")
            if not all(math.isfinite(value) for value in (x, y, width, height)):
                raise ValueError("crop coordinates must be finite")
            frame_w, frame_h = max(1, f["width"] - 1), max(1, f["height"] - 1)
            native_w, native_h = native.size
            left = max(0, min(native_w - 1, round(x * (native_w - 1) / frame_w)))
            top = max(0, min(native_h - 1, round(y * (native_h - 1) / frame_h)))
            if width < 8 or height < 8:
                raise ValueError("crop must be at least 8x8 screenshot pixels")
            right = max(left + 1, min(native_w, round((x + width) * (native_w - 1) / frame_w)))
            bottom = max(top + 1, min(native_h, round((y + height) * (native_h - 1) / frame_h)))
            patch = native.crop((left, top, right, bottom)).convert("RGB")
            # One zoom must stay cheaper than one frame: scale up as asked,
            # then shrink until the long edge and the pixel budget both fit.
            longest = max(patch.size)
            area = max(1, patch.width * patch.height)
            factor = min(longest * max(1.0, float(scale or 1.0)), MAX_CROP_SIDE) / longest
            if area * factor * factor > MAX_CROP_PIXELS:
                factor = (MAX_CROP_PIXELS / area) ** 0.5
            if abs(factor - 1.0) > 0.01:
                width = max(1, int(patch.width * factor))
                height = max(1, int(patch.height * factor))
                if width * height > MAX_CROP_PIXELS:  # rounding can tip it over
                    shrink = (MAX_CROP_PIXELS / (width * height)) ** 0.5
                    width, height = max(1, int(width * shrink)), max(1, int(height * shrink))
                patch = patch.resize((width, height), Image.LANCZOS)
            out = io.BytesIO()
            patch.save(out, "JPEG", quality=quality)
            return base64.b64encode(out.getvalue()).decode("ascii")

    def _window_rect(self, hwnd):
        rect = w.RECT()
        try:
            if hwnd and self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                return [rect.left, rect.top, rect.right, rect.bottom]
        except (TypeError, ctypes.ArgumentError):
            pass
        return None

    def capture(self, remember=True):
        from PIL import ImageGrab
        with self.lock:
            self._interactive()
            self._follow_app_monitor()
            monitor = next((m for m in self.monitors() if m["id"] == self.monitor_id), None)
            if not monitor:
                raise DesktopUnavailable("Selected monitor disconnected; start a new desktop mission")
            left, top = monitor["left"], monitor["top"]
            native = ImageGrab.grab(bbox=(left, top, left+monitor["width"], top+monitor["height"]), all_screens=True, include_layered_windows=True)
            native_w, native_h = native.size
            image = native.copy()
            image.thumbnail(FRAME_SIZE)
            self._frame_counter += 1
            frame_id = f"f{self._frame_counter}-{secrets.token_hex(3)}"
            self.frame = {"frame_id": frame_id, "width": image.width, "height": image.height,
                          "native_width": native_w, "native_height": native_h,
                          "left": left, "top": top, "monitor_id": self.monitor_id,
                          "captured_at": time.time()}
            if remember:
                self.frames[frame_id] = {**self.frame, "_mono": time.monotonic(),
                                         "_target": self.target_window,
                                         "_target_rect": self._window_rect(self.target_window)}
                while len(self.frames) > FRAME_HISTORY:
                    self.frames.popitem(last=False)
            out = io.BytesIO()
            image.save(out, "PNG", compress_level=1)
            self.last_capture = image
            self.last_native = native
            self.last_image = out.getvalue()
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.snapshot_path.parent, prefix="pliny-frame-", suffix=".png", delete=False) as snapshot:
                snapshot.write(self.last_image)
                temporary = snapshot.name
            try:
                os.replace(temporary, self.snapshot_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return image

    def _frame_for(self, data):
        """Frame the step's coordinates refer to, or StaleFrame."""
        frame_id = data.get("frame_id")
        if frame_id is None:
            if not self.frame:
                raise ValueError("Take a desktop screenshot before using coordinates")
            frame = self.frames.get(self.frame.get("frame_id"), self.frame)
        else:
            frame = self.frames.get(str(frame_id))
            if frame is None:
                raise StaleFrame("Unknown or expired frame_id; take a fresh screenshot")
        mono = frame.get("_mono")
        if mono is not None and time.monotonic() - mono > FRAME_MAX_AGE_S:
            raise StaleFrame(f"Screenshot is older than {int(FRAME_MAX_AGE_S)}s; take a fresh screenshot")
        try:
            monitor = next((m for m in self.monitors() if m["id"] == frame.get("monitor_id")), None)
        except (TypeError, AttributeError):
            monitor = None
        if monitor is not None and (monitor["left"], monitor["top"], monitor["width"], monitor["height"]) != (
                frame["left"], frame["top"], frame["native_width"], frame["native_height"]):
            raise StaleFrame("Monitor geometry changed since the screenshot; take a fresh screenshot")
        if frame_id is not None and frame.get("_target") is not None and frame.get("_target") == self.target_window:
            before, now = frame.get("_target_rect"), self._window_rect(self.target_window)
            if before and now and before != now:
                raise StaleFrame("The target window moved or resized since the screenshot; take a fresh screenshot")
        return frame

    def point(self, x, y, frame=None):
        f = frame or self.frame
        if not f:
            raise ValueError("Take a desktop screenshot before using coordinates")
        x, y = float(x), float(y)
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < f["width"] and 0 <= y < f["height"]):
            raise ValueError("Coordinates must be inside the latest screenshot")
        return (f["left"] + round(x * (f["native_width"] - 1) / max(1, f["width"] - 1)),
                f["top"] + round(y * (f["native_height"] - 1) / max(1, f["height"] - 1)))

    def windows(self):
        self._interactive()
        result = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        @callback_type
        def visit(hwnd, _):
            length = self.user.GetWindowTextLengthW(hwnd)
            if length and self.user.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(length + 1)
                self.user.GetWindowTextW(hwnd, title, length + 1)
                if title.value.startswith(HUD_TITLE_PREFIX):
                    return True  # Our click-through badge/glow is not an app.
                pid = w.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                result.append({"id": int(hwnd), "title": title.value, "pid": pid.value,
                               "foreground": hwnd == self.user.GetForegroundWindow()})
            return True
        self.user.EnumWindows.argtypes = [callback_type, w.LPARAM]
        self.user.EnumWindows(visit, 0)
        return result

    # ── Input ────────────────────────────────────────────────────────────

    def _send(self, keyboard=None, mouse=None):
        class Keyboard(ctypes.Structure):
            _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD), ("time", w.DWORD), ("extra", ctypes.c_size_t)]
        class Mouse(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("data", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD), ("extra", ctypes.c_size_t)]
        class Payload(ctypes.Union):
            _fields_ = [("ki", Keyboard), ("mi", Mouse)]
        class Input(ctypes.Structure):
            _fields_ = [("type", w.DWORD), ("payload", Payload)]
        item = Input()
        if keyboard is not None:
            item.type = 1
            item.payload.ki = Keyboard(*keyboard, 0, 0)
        else:
            item.type = 0
            item.payload.mi = Mouse(0, 0, *mouse, 0, 0)
        if self.user.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) != 1:
            raise DesktopUnavailable("Windows rejected input. Elevated applications and UAC require operator control.")
        if mouse is not None and self._pointer_state and not self.paused:
            self._pointer_state = {**self._pointer_state, "updated_at": time.monotonic()}

    def _belongs(self, hwnd, target):
        """hwnd is the target window, a child of it, or a window it owns (a dialog/menu)."""
        try:
            hwnd, target = int(hwnd or 0), int(target)
        except (TypeError, ValueError):
            return False
        if not hwnd:
            return False
        if hwnd == target:
            return True
        for relation in (GA_ROOT, GA_ROOTOWNER):
            try:
                ancestor = int(self.user.GetAncestor(hwnd, relation) or 0)
            except (TypeError, ValueError, ctypes.ArgumentError):
                continue
            if ancestor == target:
                return True
        # Native dialogs carry an owner, but Electron/Java popups can be
        # independent top-level windows of the same process. Input still stays
        # inside the application the operator asked for.
        pid = self._window_pid(hwnd)
        return bool(pid) and pid == self._window_pid(target)

    def _window_pid(self, hwnd):
        try:
            pid = w.DWORD()
            self.user.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except (TypeError, ValueError, ctypes.ArgumentError):
            return 0

    def _monitor_for_window(self, hwnd):
        rect = w.RECT()
        try:
            if not self.user.GetWindowRect(int(hwnd), ctypes.byref(rect)):
                return None
            center_x = (rect.left + rect.right) / 2
            center_y = (rect.top + rect.bottom) / 2
        except (TypeError, ValueError, AttributeError, ctypes.ArgumentError):
            return None
        for monitor in self.monitors():
            if (monitor["left"] <= center_x < monitor["left"] + monitor["width"]
                    and monitor["top"] <= center_y < monitor["top"] + monitor["height"]):
                return monitor
        return None

    def _follow_app_monitor(self):
        """Capture the monitor holding a dialog the target app just opened.

        The frame is one monitor. A modal that opens on the other screen is
        invisible to the agent, which then clicks a view that no longer matches
        the app. Follow the foreground only while it belongs to the target's
        process, so an operator app never drags the capture away.
        """
        target = self.target_window
        if not target:
            return
        foreground = self.user.GetForegroundWindow()
        if not foreground:
            return
        pid = self._window_pid(foreground)
        if not pid or pid != self._window_pid(target):
            return
        monitor = self._monitor_for_window(foreground)
        if monitor and monitor["id"] != self.monitor_id:
            self.monitor_id = monitor["id"]

    def _checkpoint(self, deadline=None, what="input"):
        """Stop between input units on pause, cancel or deadline."""
        if self.paused:
            raise PermissionError(f"Desktop emergency pause during {what}")
        if self.cancelled.is_set():
            raise ActionCancelled(f"Input cancelled during {what}")
        if deadline is not None and time.monotonic() > deadline:
            raise DeadlineExceeded(f"Batch time limit reached during {what}")

    def _glide_pointer(self, x, y, deadline=None):
        """Move in a short eased path, without treating our own path as operator input."""
        current = w.POINT()
        if not self.user.GetCursorPos(ctypes.byref(current)):
            raise DesktopUnavailable("Could not read pointer position")
        self._pointer_serial += 1
        self._pointer_state = {"serial": self._pointer_serial, "owner": self.owner, "running": True,
                               "position": (current.x, current.y), "updated_at": time.monotonic()}
        distance = math.hypot(x-current.x, y-current.y)
        duration = min(0.25, max(0.08, distance/4000))
        steps = max(2, round(duration*60)) if distance > 3 and os.environ.get("PLINY_CU_POINTER_GLIDE") != "0" else 1
        last_position = (current.x, current.y)
        for step in range(1, steps+1):
            self._pointer_checkpoint(last_position, deadline, "pointer glide")
            fraction = step/steps
            eased = fraction*fraction*(3-2*fraction)
            px, py = (x, y) if step == steps else (round(current.x+(x-current.x)*eased), round(current.y+(y-current.y)*eased))
            if not self._set_pointer_position(px, py):
                raise DesktopUnavailable("Could not move pointer")
            last_position = (px, py)
            if step < steps:
                time.sleep(duration/steps)
        self._pointer_checkpoint(last_position, deadline, "pointer glide")

    def _check_emergency_corner(self, expected=None):
        cursor = w.POINT()
        self.user.GetCursorPos(ctypes.byref(cursor))
        if cursor.x == 0 and cursor.y == 0:
            if expected is None or max(abs(expected[0]), abs(expected[1])) > 2:
                self.emergency_pause()
                raise PermissionError("Emergency pause: pointer is at the primary screen's top-left corner")

    def _pointer_checkpoint(self, expected, deadline, what):
        self._check_emergency_corner(expected)
        self._checkpoint(deadline, what)

    def _check_pointer_input(self, action, data, x, y, deadline):
        if data.get("frame_id") is not None:
            self._frame_for(data)
        self._check_target(action, data, x, y)
        self._pointer_checkpoint((x, y), deadline, action)

    def _check_target(self, action, data, x=None, y=None):
        """Foreground and pointer-target validation shared by mouse and keyboard."""
        target = data.get("window_id", self.target_window)
        if target is None:
            return None
        if not self._belongs(self.user.GetForegroundWindow(), target):
            raise DesktopFocusChanged("Foreground changed. Inspect and activate the intended window before input.")
        if action in ("click", "drag", "scroll") and x is not None:
            under = self.user.WindowFromPoint(w.POINT(x, y))
            root = self.user.GetAncestor(under, GA_ROOT) if under else None
            if not self._belongs(root, target):
                raise TargetMismatch("The point is outside the target window or covered by another window.")
        return target

    @contextmanager
    def _modifiers(self, data):
        """Hold Ctrl/Shift/Alt/Win for the pointer action inside the block."""
        raw = data.get("keys") or data.get("modifiers") or ()
        if isinstance(raw, str):
            raw = [raw]
        codes = []
        for name in raw:
            code = MODIFIER_KEYS.get(str(name).strip().lower())
            if code is None:
                raise ValueError("Unsupported modifier: " + str(name))
            codes.append(code)
        held = []
        try:
            for code in codes:
                self._send(keyboard=(code, 0, 0))
                held.append(code)
            yield
        finally:
            for code in reversed(held):
                self._send(keyboard=(code, 0, 2))

    def press(self, key, deadline=None):
        aliases = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
                   "enter": 0x0D, "return": 0x0D, "tab": 9, "escape": 0x1B, "esc": 0x1B,
                   "space": 0x20, "backspace": 8, "insert": 0x2D, "ins": 0x2D,
                   "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
                   "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
                   "capslock": 0x14, "numlock": 0x90, "printscreen": 0x2C, "prtsc": 0x2C,
                   "apps": 0x5D, "menu": 0x5D,
                   "plus": 0xBB, "=": 0xBB, "equal": 0xBB, "minus": 0xBD, "-": 0xBD,
                   ",": 0xBC, "comma": 0xBC, ".": 0xBE, "period": 0xBE,
                   "/": 0xBF, "slash": 0xBF, ";": 0xBA, "semicolon": 0xBA,
                   "[": 0xDB, "]": 0xDD, "\\": 0xDC, "'": 0xDE, "`": 0xC0}
        extended = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E}
        raw = str(key).strip().lower()
        if raw == "+":
            parts = ["plus"]
        elif raw.endswith("++"):
            parts = [*raw[:-2].split("+"), "plus"]
        else:
            parts = raw.split("+")
        codes = []
        for part in parts:
            part = part.strip()
            if part in aliases:
                codes.append(aliases[part])
            elif len(part) == 1 and part.isascii() and part.isalnum():
                codes.append(ord(part.upper()))
            elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
                codes.append(0x6F + int(part[1:]))
            else:
                raise ValueError("Unsupported key: " + part)
        held = []
        try:
            for code in codes:
                self._checkpoint(deadline, "key press")
                down_flag = 0x0001 if code in extended else 0
                self._send(keyboard=(code, 0, down_flag))
                held.append(code)
        finally:
            # Always release what was pressed, even when cancelled mid-chord.
            for code in reversed(held):
                up_flag = 0x0003 if code in extended else 0x0002
                self._send(keyboard=(code, 0, up_flag))

    def action(self, action, data, session_id, deadline=None):
        with self.lock, self._pointer_action():
            self._interactive()
            if self.paused or self.owner != session_id:
                raise PermissionError("Desktop paused or owned by another session")
            if self.cancelled.is_set():
                raise ActionCancelled("Input cancelled; re-observe before continuing")
            if action in ("type", "press"):
                if data.get("window_id") is None:
                    if self.target_window is None:
                        raise ValueError("window_id required: activate the target window first")
                    data = {**data, "window_id": self.target_window}
                self._check_target(action, data)
            self._check_emergency_corner()
            if action in POINTER_ACTIONS:
                frame = self._frame_for(data)
                x, y = self.point(data["x"], data["y"], frame)
                if action != "move":
                    self._check_target(action, data, x, y)
                self._glide_pointer(x, y, deadline)
            if action == "click":
                flags = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}.get(data.get("button", "left"))
                if not flags:
                    raise ValueError("Invalid mouse button")
                count = int(data.get("count", 1))
                if count not in (1, 2):
                    raise ValueError("Click count must be 1 or 2")
                with self._modifiers(data):
                    for _ in range(count):
                        self._check_pointer_input(action, data, x, y, deadline)
                        self._send(mouse=(0, flags[0]))
                        self._send(mouse=(0, flags[1]))
                        if count == 2:
                            time.sleep(0.06)
            elif action == "drag":
                target_x, target_y = self.point(data["to_x"], data["to_y"], frame)
                with self._modifiers(data):
                    self._check_pointer_input(action, data, x, y, deadline)
                    self._send(mouse=(0, 2))
                    try:
                        for step in range(1, 21):
                            self._checkpoint(deadline, "drag")
                            self._set_pointer_position(round(x + (target_x - x) * step / 20), round(y + (target_y - y) * step / 20))
                            time.sleep(0.02)
                    finally:
                        self._send(mouse=(0, 4))
            elif action == "scroll":
                delta = int(data.get("delta", -3))
                if not -20 <= delta <= 20:
                    raise ValueError("Scroll delta must be between -20 and 20")
                with self._modifiers(data):
                    self._check_pointer_input(action, data, x, y, deadline)
                    self._send(mouse=((delta * 120) & 0xFFFFFFFF, 0x1000 if data.get("horizontal") else 0x800))
            elif action == "type":
                text = data.get("text")
                if not isinstance(text, str) or len(text) > 10000:
                    raise ValueError("Text must be a string up to 10000 characters")
                units = text.encode("utf-16-le")
                sent = 0
                try:
                    for i in range(0, len(units), 2):
                        self._checkpoint(deadline, "typing")
                        # Every unit: a focus thief must not receive even a few characters.
                        if not self._belongs(self.user.GetForegroundWindow(), data["window_id"]):
                            raise DesktopFocusChanged("Foreground changed during typing; remaining text was not sent")
                        unit = int.from_bytes(units[i:i + 2], "little")
                        self._send(keyboard=(0, unit, 4))
                        self._send(keyboard=(0, unit, 6))
                        sent += 1
                except Exception as error:
                    error.progress = {"sent_units": sent, "total_units": len(units) // 2}
                    raise
            elif action == "press":
                self.press(data["key"], deadline)
            elif action == "activate":
                hwnd = int(data["window_id"])
                if not self.user.IsWindow(hwnd):
                    raise ValueError("Window no longer exists; list windows again")
                monitor = next((m for m in self.monitors() if m["id"] == self.monitor_id), None)
                rect = w.RECT()
                if monitor and self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                    if not (monitor["left"] <= (rect.left+rect.right)/2 < monitor["left"]+monitor["width"] and monitor["top"] <= (rect.top+rect.bottom)/2 < monitor["top"]+monitor["height"]):
                        self.user.ShowWindow(hwnd, 9)
                        self.user.SetWindowPos(hwnd, None, monitor["left"]+20, monitor["top"]+20,
                                               min(rect.right-rect.left, monitor["width"]-40),
                                               min(rect.bottom-rect.top, monitor["height"]-80), 4)
                # Keep the application maximized; SW_RESTORE on every activation
                # was shrinking windows the operator had already maximized.
                self.user.ShowWindow(hwnd, 3)
                self.user.SetForegroundWindow(hwnd)
                if self.user.GetForegroundWindow() != hwnd:
                    # A background server may not call SetForegroundWindow while
                    # another app has focus. Use a normal title-bar click, only
                    # after verifying the visible point belongs to this window.
                    self.user.BringWindowToTop(hwnd)
                    self.user.GetWindowRect(hwnd, ctypes.byref(rect))
                    title_y = max(rect.top + 1, min(rect.top + 12, (rect.top + rect.bottom) // 2))
                    point = w.POINT((rect.left + rect.right) // 2, title_y)
                    title_rect = [rect.left, rect.top, rect.right, rect.bottom]
                    under_pointer = self.user.WindowFromPoint(point)
                    if self.user.GetAncestor(under_pointer, GA_ROOT) != hwnd:
                        raise DesktopFocusChanged("Target window is occluded; cannot safely focus it")
                    self._glide_pointer(point.x, point.y, deadline)
                    under_pointer = self.user.WindowFromPoint(point)
                    if self.user.GetAncestor(under_pointer, GA_ROOT) != hwnd:
                        raise DesktopFocusChanged("Target window is occluded; cannot safely focus it")
                    if self._window_rect(hwnd) != title_rect:
                        raise StaleFrame("Target window moved during activation; inspect and activate it again")
                    if data.get("frame_id") is not None:
                        self._frame_for(data)
                    self._pointer_checkpoint((point.x, point.y), deadline, "activate")
                    self._send(mouse=(0, 2))
                    self._send(mouse=(0, 4))
                    time.sleep(0.15)
                    if self.user.GetForegroundWindow() != hwnd:
                        raise DesktopFocusChanged("Windows refused focus; operator must activate the application")
                self.target_window = hwnd
            elif action == "clear_target":
                # Deliberate: later pointer actions are no longer tied to one window.
                self.target_window = None
            elif action == "launch":
                app = data.get("app")
                args = data.get("args", [])
                if isinstance(args, str):
                    # One URL or flag arrives as a bare string far more often
                    # than as a JSON array; rejecting it cost a whole turn.
                    args = [args]
                if not isinstance(app, str) or not app.lower().endswith(".exe") or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    raise ValueError("Provide an .exe application and an array of arguments")
                try:
                    proc = subprocess.Popen([app, *args], shell=False)
                except FileNotFoundError as error:
                    raise ValueError(f"Application not found: {app}") from error
                return {"ok": True, "pid": proc.pid}
            elif action != "move":
                raise ValueError("Unsupported desktop action")
            return {"ok": True}

    def _wait(self, ms, deadline):
        end = time.monotonic() + ms / 1000
        while True:
            # A wait sends no input; pause/ESC/stop all set `cancelled`.
            if self.cancelled.is_set():
                raise ActionCancelled("Input cancelled during wait")
            if deadline is not None and time.monotonic() > deadline:
                raise DeadlineExceeded("Batch time limit reached during wait")
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            # Wake early on pause/cancel instead of sleeping through an ESC.
            self.cancelled.wait(min(0.05, remaining))

    def batch(self, steps, session_id, settle_ms=150, timeout_ms=None):
        """Run several actions in one request and stop at the first failure.

        One agent tool call per observed sequence (instead of one call per
        click/keypress plus a separate image read) is the main speed win. The
        whole batch shares one deadline; pause, cancel and focus loss stop it
        between input units and report how far it got.
        """
        if not isinstance(steps, list) or len(steps) > BATCH_LIMIT:
            raise ValueError(f"actions must be a list of at most {BATCH_LIMIT} steps")
        budget = BATCH_DEADLINE_S if timeout_ms is None else float(timeout_ms) / 1000
        if not 0 < budget <= BATCH_DEADLINE_MAX_S:
            raise ValueError(f"timeout_ms must be between 1 and {int(BATCH_DEADLINE_MAX_S * 1000)}")
        deadline = time.monotonic() + budget
        aliases = {"double_click": ("click", {"count": 2}), "right_click": ("click", {"button": "right"}),
                   "key": ("press", {})}
        results = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("action"), str):
                raise ValueError(f"step {index}: each step needs an action")
            data = {key: value for key, value in step.items() if key != "action"}
            name = step["action"]
            if name in aliases:
                name, extra = aliases[name]
                data = {**extra, **data}
            started = time.monotonic()
            try:
                if time.monotonic() > deadline:
                    raise DeadlineExceeded("Batch time limit reached before this step")
                if name == "wait":
                    ms = float(data.get("ms", 500))
                    if not 0 <= ms <= 10000:
                        raise ValueError("wait ms must be between 0 and 10000")
                    self._wait(ms, deadline)
                    result = {"ok": True}
                else:
                    result = self.action(name, data, session_id, deadline=deadline)
            except Exception as error:
                # "paused" in codes means the operator has control, not a mission end.
                code = desktop_error_code(error, self.operator_paused)
                entry = {"index": index, "action": name, "error": str(error), "code": code,
                         "recovery": RECOVERY.get(code, ""), "completed_steps": index}
                if getattr(error, "progress", None):
                    entry["progress"] = error.progress
                results.append(entry)
                return {"ok": False, "results": results, "code": code}
            results.append({"index": index, "action": name, "ms": round((time.monotonic() - started) * 1000), **result})
        if steps and settle_ms:
            try:
                self._wait(min(max(float(settle_ms), 0), 3000), None)
            except (PermissionError, ActionCancelled):
                pass  # Every step already ran; the screenshot shows the paused state.
        return {"ok": True, "results": results}

    def foreground(self):
        self._init()
        hwnd = self.user.GetForegroundWindow()
        length = self.user.GetWindowTextLengthW(hwnd) if hwnd else 0
        title = ctypes.create_unicode_buffer(length + 1)
        if length:
            self.user.GetWindowTextW(hwnd, title, length + 1)
        return {"id": int(hwnd or 0), "title": title.value}


def desktop_error_code(error, paused):
    if isinstance(error, TargetMismatch):
        return "target_mismatch"
    if isinstance(error, DesktopFocusChanged):
        return "focus_changed"
    if isinstance(error, StaleFrame):
        return "stale_frame"
    if isinstance(error, TimeoutError):
        return "deadline_exceeded"
    if isinstance(error, ActionCancelled):
        return "desktop_paused" if paused else "cancelled"
    if isinstance(error, PermissionError):
        return "desktop_paused" if paused else "desktop_ownership_changed"
    if isinstance(error, DesktopUnavailable):
        return "desktop_unavailable"
    return "invalid_action"


# HTTP status for each structured code (server and MCP share these).
ERROR_STATUS = {"focus_changed": 409, "target_mismatch": 409, "stale_frame": 409,
                "desktop_paused": 423, "desktop_ownership_changed": 409, "cancelled": 409,
                "deadline_exceeded": 408, "desktop_unavailable": 503, "invalid_action": 400}


desktop = WindowsDesktop()
