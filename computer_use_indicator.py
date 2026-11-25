"""Non-activating Windows status badge and physical Escape emergency pause."""
import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import json
import urllib.request
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from process_control import process_group_options
from desktop_glow import ScreenEdgeGlow, exclude_from_capture, keep_above
from desktop_cursor import AgentCursor
from desktop_native_cursor import NativeCursor, PointerDisplay

_FONT_DIR = Path(__file__).resolve().parent / "vendor" / "fonts"
BADGE_WIDTH = 472
BADGE_HEIGHT = 66
BADGE_PAD = 12  # soft drop-shadow margin around the pill


@lru_cache(maxsize=16)
def _load_font(filename, size, fallbacks=()):
    candidates = [_FONT_DIR / filename]
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for fb in fallbacks:
        candidates.append(win_fonts / fb)
    for path in candidates:
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return ImageFont.load_default()


def badge_content(state):
    paused = bool(state.get("paused"))
    interacting = state.get("interacting", True)
    title = "PLINY PAUSED" if paused else ("PLINY USING COMPUTER USE" if interacting else "PLINY WORKING")
    mode = "WINDOWS DESKTOP" if state.get("mode") == "desktop" else "FIREFOX BROWSER"
    detail = "YOU HAVE CONTROL  |  RELEASE IN PANEL" if paused else f"{mode if interacting else 'DESKTOP READY'}  |  ESC TO PAUSE"
    return title, detail, "#ffb000" if paused else ("#39ff14" if interacting else "#40c8ff")


def badge_visual_spec(state):
    """Bright-balanced KAIKEN URUSHI visual palette & copy for the Computer Use HUD pill."""
    paused = bool(state.get("paused"))
    interacting = state.get("interacting", True)
    mode_label = "Windows Desktop" if state.get("mode") == "desktop" else "Firefox Browser"
    if paused:
        return {
            "title": "Kaiken  ·  Paused",
            "detail": "You have control  ·  Release in panel",
            "keycap": "YOU",
            "keycap_sub": "",
            "bg": (62, 54, 40, 244),
            "border": (246, 192, 78, 220),
            "top_highlight": (255, 240, 200, 38),
            "dot": (252, 188, 56, 255),
            "dot_halo": (252, 188, 56, 68),
            "title_fg": (255, 249, 236, 255),
            "detail_fg": (240, 222, 180, 255),
            "chip_bg": (248, 188, 62, 255),
            "chip_border": (255, 214, 120, 255),
            "chip_fg": (36, 30, 18, 255),
        }
    if interacting:
        return {
            "title": "Kaiken  ·  Computer Use",
            "detail": f"{mode_label}  ·  Agent active",
            "keycap": "ESC",
            "keycap_sub": "Pause",
            "bg": (52, 49, 44, 242),
            "border": (222, 214, 198, 175),
            "top_highlight": (255, 252, 245, 36),
            "dot": (242, 92, 72, 255),
            "dot_halo": (242, 92, 72, 68),
            "title_fg": (250, 247, 240, 255),
            "detail_fg": (218, 212, 200, 255),
            "chip_bg": (244, 240, 232, 255),
            "chip_border": (255, 252, 246, 255),
            "chip_fg": (38, 36, 32, 255),
        }
    return {
        "title": "Kaiken  ·  Working",
        "detail": "Desktop ready  ·  Standing by",
        "keycap": "ESC",
        "keycap_sub": "Pause",
        "bg": (52, 55, 56, 240),
        "border": (188, 214, 198, 170),
        "top_highlight": (245, 252, 248, 34),
        "dot": (100, 218, 144, 255),
        "dot_halo": (100, 218, 144, 62),
        "title_fg": (248, 247, 243, 255),
        "detail_fg": (212, 220, 216, 255),
        "chip_bg": (240, 242, 238, 255),
        "chip_border": (252, 254, 250, 255),
        "chip_fg": (36, 38, 36, 255),
    }


def render_badge_image(state):
    """Render an anti-aliased KAIKEN floating pill HUD using Cal Sans, Geist, and Geist Mono."""
    spec = badge_visual_spec(state)
    scale = 2
    cw = (BADGE_WIDTH + BADGE_PAD * 2) * scale
    ch = (BADGE_HEIGHT + BADGE_PAD * 2) * scale
    px = BADGE_PAD * scale
    py = BADGE_PAD * scale
    bw = BADGE_WIDTH * scale
    bh = BADGE_HEIGHT * scale
    radius = bh // 2

    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    # Soft diffused elevation shadow
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle(
        (px, py + 4 * scale, px + bw, py + bh + 4 * scale),
        radius=radius,
        fill=(14, 13, 11, 125),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(7 * scale))
    canvas = Image.alpha_composite(canvas, shadow)

    # Pill body + crisp hairline border
    body = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(body)
    draw.rounded_rectangle(
        (px, py, px + bw, py + bh),
        radius=radius,
        fill=spec["bg"],
        outline=spec["border"],
        width=max(2, round(1.25 * scale)),
    )
    # Subtle inner luminous rim for tactile depth (keeps pill from looking flat/dark)
    inset = 2 * scale
    draw.rounded_rectangle(
        (px + inset, py + inset, px + bw - inset, py + bh - inset),
        radius=max(4, radius - inset),
        outline=spec["top_highlight"],
        width=scale,
    )

    # Left status beacon (soft halo + solid dot)
    dot_cx = px + 26 * scale
    dot_cy = py + bh // 2
    halo_r = 11 * scale
    dot_r = 5.5 * scale
    draw.ellipse((dot_cx - halo_r, dot_cy - halo_r, dot_cx + halo_r, dot_cy + halo_r), fill=spec["dot_halo"])
    draw.ellipse((dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r), fill=spec["dot"])

    # Typography (Cal Sans display title · Geist medium detail · Geist Mono keycap)
    font_title = _load_font("CalSans-Regular.ttf", 16 * scale, ("seguisb.ttf", "segoeui.ttf"))
    font_detail = _load_font("Geist-Medium.ttf", 11 * scale, ("segoeui.ttf", "arial.ttf"))
    font_key = _load_font("GeistMono-SemiBold.ttf", 10.5 * scale, ("CascadiaMono.ttf", "consolab.ttf"))
    font_key_sub = _load_font("Geist-SemiBold.ttf", 10.5 * scale, ("seguisb.ttf", "segoeui.ttf"))

    text_x = px + 48 * scale
    title_y = py + 12 * scale
    detail_y = py + 36 * scale
    draw.text((text_x, title_y), spec["title"], font=font_title, fill=spec["title_fg"])
    draw.text((text_x, detail_y), spec["detail"], font=font_detail, fill=spec["detail_fg"])

    # Right-side action chip (high-contrast ESC keycap + Pause label, or YOU pill when paused)
    key_bbox = draw.textbbox((0, 0), spec["keycap"], font=font_key)
    key_tw = key_bbox[2] - key_bbox[0]
    key_th = key_bbox[3] - key_bbox[1]
    chip_h = 26 * scale
    chip_w = max(44 * scale, key_tw + 20 * scale)

    if spec["keycap_sub"]:
        sub_bbox = draw.textbbox((0, 0), spec["keycap_sub"], font=font_key_sub)
        sub_tw = sub_bbox[2] - sub_bbox[0]
        sub_th = sub_bbox[3] - sub_bbox[1]
        group_right = px + bw - 18 * scale
        sub_x = group_right - sub_tw
        chip_x1 = sub_x - 8 * scale
        chip_x0 = chip_x1 - chip_w
        chip_y0 = dot_cy - chip_h // 2
        chip_y1 = chip_y0 + chip_h
        draw.rounded_rectangle(
            (chip_x0, chip_y0, chip_x1, chip_y1),
            radius=chip_h // 2,
            fill=spec["chip_bg"],
            outline=spec["chip_border"],
            width=scale,
        )
        draw.text(
            (chip_x0 + (chip_w - key_tw) / 2 - key_bbox[0], chip_y0 + (chip_h - key_th) / 2 - key_bbox[1]),
            spec["keycap"],
            font=font_key,
            fill=spec["chip_fg"],
        )
        draw.text(
            (sub_x - sub_bbox[0], dot_cy - sub_th / 2 - sub_bbox[1]),
            spec["keycap_sub"],
            font=font_key_sub,
            fill=spec["detail_fg"],
        )
    else:
        chip_x1 = px + bw - 18 * scale
        chip_x0 = chip_x1 - max(56 * scale, chip_w)
        chip_y0 = dot_cy - chip_h // 2
        chip_y1 = chip_y0 + chip_h
        actual_w = chip_x1 - chip_x0
        draw.rounded_rectangle(
            (chip_x0, chip_y0, chip_x1, chip_y1),
            radius=chip_h // 2,
            fill=spec["chip_bg"],
            outline=spec["chip_border"],
            width=scale,
        )
        draw.text(
            (chip_x0 + (actual_w - key_tw) / 2 - key_bbox[0], chip_y0 + (chip_h - key_th) / 2 - key_bbox[1]),
            spec["keycap"],
            font=font_key,
            fill=spec["chip_fg"],
        )

    canvas = Image.alpha_composite(canvas, body)
    return canvas.resize((BADGE_WIDTH + BADGE_PAD * 2, BADGE_HEIGHT + BADGE_PAD * 2), Image.Resampling.LANCZOS)


class StatusBadgeOverlay(ScreenEdgeGlow):
    """Per-pixel anti-aliased KAIKEN floating pill badge, excluded from agent captures."""

    def show_badge(self, bounds, state):
        spec_key = (
            bool(state.get("paused")),
            bool(state.get("interacting", True)),
            state.get("mode"),
            tuple(bounds),
        )
        if spec_key != self.signature or not self.visible:
            left, top, width, _ = bounds
            image = render_badge_image(state)
            pos_x = left + max(0, (width - image.width) // 2)
            pos_y = top + max(4, 16 - BADGE_PAD)
            self._paint(image, pos_x, pos_y)
            self.signature = spec_key
        if not self.visible:
            self.user.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.visible = True
        self._ensure_capture_excluded()
        keep_above(self.user, self.hwnd)


class ComputerUseIndicator:
    def __init__(self, state_provider, emergency_pause):
        self.state_provider = state_provider
        self.emergency_pause = emergency_pause
        self._armed = False
        self._stop = threading.Event()
        self._hook = None
        self._pointer_display = None

    def close(self):
        self._armed = False
        self._stop.set()

    def physical_escape(self, vk, flags):
        # Ignore injected keystrokes, so an agent can still use Escape in menus.
        if vk == 0x1B and not flags & 0x10 and self._armed:
            self._armed = False
            self.emergency_pause()
            if self._pointer_display is not None:
                self._pointer_display.hide()
            return True
        return False

    def _run(self):
        import tkinter as tk
        root = None
        glow = None
        badge_hud = None
        cursor = None
        native = None
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, w.WPARAM, w.LPARAM)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, w.HINSTANCE, w.DWORD]
        user.SetWindowsHookExW.restype = w.HANDLE
        user.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [w.HANDLE]
        class KeyboardEvent(ctypes.Structure):
            _fields_ = [("vk", w.DWORD), ("scan", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD), ("extra", ctypes.c_size_t)]
        @callback_type
        def keyboard_hook(code, message, address):
            try:
                if code >= 0 and message in (0x100, 0x104):
                    key = ctypes.cast(address, ctypes.POINTER(KeyboardEvent)).contents
                    if self.physical_escape(key.vk, key.flags):
                        return 1
            except Exception:
                logging.exception("Computer Use Escape hook failed")
            return user.CallNextHookEx(self._hook, code, message, address)
        self._hook_callback = keyboard_hook
        try:
            native = NativeCursor(user)
            root = tk.Tk()
            root.withdraw()
            root.title("Kaiken Computer Use Indicator")
            root.update_idletasks()
            user.GetAncestor.argtypes = [w.HWND, w.UINT]
            user.GetAncestor.restype = w.HWND
            hwnd = user.GetAncestor(root.winfo_id(), 2)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
            kernel.GetModuleHandleW.restype = w.HMODULE
            self._hook = user.SetWindowsHookExW(13, keyboard_hook, kernel.GetModuleHandleW(None), 0)
            if not self._hook:
                raise ctypes.WinError(ctypes.get_last_error())
            hinstance = kernel.GetModuleHandleW(None)
            glow = ScreenEdgeGlow(user, hinstance, hwnd)
            badge_hud = StatusBadgeOverlay(user, hinstance, hwnd)
            cursor = AgentCursor(user, hinstance, hwnd)
            self._pointer_display = PointerDisplay(cursor, native, user)
            previous = None
            visible = False
            ticks = 0
            def cursor_tick():
                if self._stop.is_set():
                    return
                try:
                    state = self.state_provider()
                    self._pointer_display.update(state)
                    if cursor.visible:
                        keep_above(user, cursor.hwnd, badge_hud.hwnd)
                except Exception:
                    self._pointer_display.hide()
                    logging.exception("Computer Use cursor refresh failed")
                root.after(16, cursor_tick)
            def tick():
                nonlocal previous, visible, ticks
                if self._stop.is_set():
                    root.quit()
                    return
                try:
                    state = self.state_provider()
                    active = bool(state.get("active"))
                    self._armed = active and not state.get("paused")
                    if not active:
                        if visible:
                            badge_hud.hide()
                            glow.hide()
                            visible = False
                    else:
                        content = badge_content(state)
                        bounds = state["bounds"]
                        signature = (content, tuple(bounds))
                        if signature != previous or not visible:
                            glow.show(bounds, bool(state.get("paused")), bool(state.get("interacting", True)))
                            badge_hud.show_badge(bounds, state)
                            previous = signature
                            visible = True
                        ticks += 1
                        if ticks % 5 == 0:
                            # Glow first, cursor middle, badge last: all stay above active apps.
                            keep_above(user, glow.hwnd, *([cursor.hwnd] if cursor.visible else []), badge_hud.hwnd)
                except Exception:
                    self._armed = False
                    logging.exception("Computer Use indicator refresh failed")
                root.after(50, tick)
            tick()
            cursor_tick()
            root.mainloop()
        except Exception:
            logging.exception("Computer Use indicator could not start")
        finally:
            self._armed = False
            if self._pointer_display is not None:
                self._pointer_display.close()
                self._pointer_display = None
            elif native is not None:
                native.close()
            if self._hook:
                user.UnhookWindowsHookEx(self._hook)
                self._hook = None
            if badge_hud is not None:
                badge_hud.close()
            if glow is not None:
                glow.close()
            if cursor is not None:
                cursor.close()
            if root is not None:
                root.destroy()


def start_indicator(base_dir, port, env=None):
    """Tk owns its process main thread; the server never calls Tk from workers."""
    if os.name != "nt":
        return None
    log_path = Path(base_dir) / "logs" / "indicator.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        return subprocess.Popen(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
             "--port", str(port), "--parent", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            env=env,
            **process_group_options(),
        )


def run_client(port, parent_pid):
    state = {"active": False}
    escape = threading.Event()
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("PLINY_AGENT_TOKEN") or os.environ.get("PLINY_AUTH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    base = f"http://127.0.0.1:{port}/api/computer-use/indicator"
    def pause():
        state["paused"] = True
        escape.set()
    badge = ComputerUseIndicator(lambda: dict(state), pause)
    def poll():
        nonlocal state
        misses = 0
        while not badge._stop.is_set():
            try:
                req = urllib.request.Request(base, headers=headers)
                with urllib.request.urlopen(req, timeout=0.5) as response:
                    current = json.load(response)
                if current.get("server_pid") != parent_pid:
                    badge.close()
                    return
                state = current
                misses = 0
            except Exception as error:
                state["active"] = False
                misses += 1
                if misses == 1 or misses >= 3:
                    logging.warning("Indicator poll failed (%d/3): %s", misses, error)
                if misses >= 3:
                    badge.close()
                    return
            badge._stop.wait(0.016)
    def send_pause():
        while not badge._stop.is_set():
            if not escape.wait(0.2):
                continue
            escape.clear()
            try:
                req = urllib.request.Request(base, data=b"{}", headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=1).close()
            except Exception:
                logging.exception("Could not send emergency pause to the command center")
    threading.Thread(target=poll, daemon=True).start()
    threading.Thread(target=send_pause, daemon=True).start()
    badge._run()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent", type=int, required=True)
    args = parser.parse_args()
    run_client(args.port, args.parent)
