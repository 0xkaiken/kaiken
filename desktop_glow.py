"""Static, per-pixel transparent monitor-edge glow for the Computer Use HUD."""
import ctypes
import logging
import os
from ctypes import wintypes as w

from PIL import Image, ImageChops

# Edge opacity (0-255) and how fast it falls toward the center. The falloff
# reaches exactly zero in the middle of the monitor; corners are brighter
# because the horizontal and vertical glows are screen-blended.
EDGE_ALPHA = {"active": 152, "working": 98, "paused": 144}
FALLOFF = 2.7
WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = -1
SWP_KEEP_TOPMOST = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER


def edge_glow_image(width, height, paused=False, interacting=True):
    """Semi-transparent glow that is strongest at the edges and zero at the center."""
    strength = EDGE_ALPHA["paused" if paused else ("active" if interacting else "working")]
    if paused:
        palette = ((250, 184, 52), (242, 134, 78), (248, 206, 108))
    elif interacting:
        palette = ((238, 96, 76), (232, 186, 108), (242, 232, 212))
    else:
        palette = ((224, 198, 146), (112, 216, 156), (236, 232, 222))
    def gradient(length):
        colors = []
        for i in range(length):
            position = i * 2 / max(1, length-1)
            section = min(1, int(position))
            fraction = position-section
            colors.extend(round(palette[section][c]*(1-fraction)+palette[section+1][c]*fraction) for c in range(3))
        return bytes(colors)
    def falloff(length):
        half = max(1, (length-1) / 2)
        return bytes(round(strength * max(0.0, 1 - min(i, length-1-i) / half) ** FALLOFF) for i in range(length))
    across = Image.frombytes("RGB", (width, 1), gradient(width)).resize((width, height))
    down = Image.frombytes("RGB", (1, height), gradient(height)).resize((width, height))
    result = Image.blend(across, down, 0.5).convert("RGBA")
    horizontal = Image.frombytes("L", (width, 1), falloff(width)).resize((width, height))
    vertical = Image.frombytes("L", (1, height), falloff(height)).resize((width, height))
    result.putalpha(ImageChops.screen(horizontal, vertical))
    return result


def keep_above(user, *windows):
    """Re-assert topmost order; apps activated later must not cover the HUD."""
    user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
    for hwnd in windows:
        user.SetWindowPos(hwnd, w.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_KEEP_TOPMOST)


def exclude_from_capture(user, hwnd):
    """Keep the HUD visible to the operator but out of the agent's screenshots."""
    if os.environ.get("PLINY_CU_HUD_CAPTURE") == "1":
        return False
    user.SetWindowDisplayAffinity.argtypes = [w.HWND, w.DWORD]
    return bool(user.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


class ScreenEdgeGlow:
    """One disabled, click-through layered window, updated only on state changes."""
    def __init__(self, user, instance, owner):
        self.user = user
        self.instance = instance
        self.visible = False
        self.signature = None
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.class_name = f"Pliny{type(self).__name__}_{owner}"
        callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        @callback
        def procedure(hwnd, message, wp, lp):
            if message == 0x84:  # WM_NCHITTEST
                return -1  # HTTRANSPARENT
            return user.DefWindowProcW(hwnd, message, wp, lp)
        self.procedure = procedure
        class WindowClass(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("proc", callback), ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                        ("instance", w.HINSTANCE), ("icon", w.HANDLE), ("cursor", w.HANDLE), ("background", w.HANDLE),
                        ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
        description = WindowClass()
        description.proc, description.instance, description.name = procedure, instance, self.class_name
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = w.HWND
        if not user.RegisterClassW(ctypes.byref(description)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Disabled windows are ignored by WindowFromPoint as well as by input.
        self.hwnd = user.CreateWindowExW(0x20 | 0x80 | 0x08000000 | 0x8,
                                        self.class_name, "Pliny Computer Use Edge Glow", 0x80000000 | 0x08000000,
                                        0, 0, 1, 1, None, None, instance, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.capture_excluded = exclude_from_capture(user, self.hwnd)
        self._capture_warning = False
        # Per-pixel layered windows reject SetWindowDisplayAffinity with error 8.
        # Establish the affinity before enabling the layered surface.
        getter = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
        setter = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
        getter.argtypes, getter.restype = [w.HWND, ctypes.c_int], ctypes.c_ssize_t
        setter.argtypes, setter.restype = [w.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        setter(self.hwnd, -20, getter(self.hwnd, -20) | 0x80000)
        user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user.DestroyWindow.argtypes = [w.HWND]
        user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]

    def show(self, bounds, paused, interacting):
        signature = (tuple(bounds), paused, interacting)
        if signature != self.signature:
            left, top, width, height = bounds
            image = edge_glow_image(width, height, paused, interacting)
            self._paint(image, left, top)
            self.signature = signature
        if not self.visible:
            self.user.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.visible = True
        self._ensure_capture_excluded()
        keep_above(self.user, self.hwnd)

    def _ensure_capture_excluded(self):
        if os.environ.get("PLINY_CU_HUD_CAPTURE") == "1":
            self.capture_excluded = False
            return
        user = self.user
        user.GetWindowDisplayAffinity.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        def verified():
            affinity = w.DWORD()
            return bool(user.GetWindowDisplayAffinity(self.hwnd, ctypes.byref(affinity))) and affinity.value == WDA_EXCLUDEFROMCAPTURE
        self.capture_excluded = verified()
        if not self.capture_excluded:
            exclude_from_capture(user, self.hwnd)
            error = ctypes.get_last_error()
            self.capture_excluded = verified()
            if not self.capture_excluded and not self._capture_warning:
                logging.warning("%s capture exclusion could not be verified (HWND=%s, Win32 error=%s)",
                                type(self).__name__, self.hwnd, error)
        self._capture_warning = not self.capture_excluded

    def hide(self):
        if self.visible:
            self.user.ShowWindow(self.hwnd, 0)
            self.visible = False

    def close(self):
        self.user.DestroyWindow(self.hwnd)
        self.user.UnregisterClassW(self.class_name, self.instance)

    def _paint(self, image, left, top):
        class BitmapHeader(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG), ("planes", w.WORD), ("bits", w.WORD),
                        ("compression", w.DWORD), ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                        ("used", w.DWORD), ("important", w.DWORD)]
        class Blend(ctypes.Structure):
            _fields_ = [("operation", w.BYTE), ("flags", w.BYTE), ("alpha", w.BYTE), ("format", w.BYTE)]
        u, g = self.user, self.gdi
        u.GetDC.argtypes, u.GetDC.restype = [w.HWND], w.HDC
        u.ReleaseDC.argtypes = [w.HWND, w.HDC]
        g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [w.HDC], w.HDC
        g.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BitmapHeader), w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
        g.CreateDIBSection.restype = w.HANDLE
        g.SelectObject.argtypes, g.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
        g.DeleteObject.argtypes = [w.HANDLE]
        g.DeleteDC.argtypes = [w.HDC]
        u.UpdateLayeredWindow.argtypes = [w.HWND, w.HDC, ctypes.POINTER(w.POINT), ctypes.POINTER(w.SIZE), w.HDC, ctypes.POINTER(w.POINT), w.DWORD, ctypes.POINTER(Blend), w.DWORD]
        screen = u.GetDC(None)
        memory = g.CreateCompatibleDC(screen)
        bitmap = previous = None
        try:
            header = BitmapHeader()
            header.size, header.width, header.height = ctypes.sizeof(header), image.width, -image.height
            header.planes, header.bits = 1, 32
            pixels = ctypes.c_void_p()
            bitmap = g.CreateDIBSection(memory, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0)
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = g.SelectObject(memory, bitmap)
            raw = image.convert("RGBa").tobytes("raw", "BGRa")
            ctypes.memmove(pixels, raw, len(raw))
            destination, source, size = w.POINT(left, top), w.POINT(0, 0), w.SIZE(image.width, image.height)
            blend = Blend(0, 0, 255, 1)
            if not u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(destination), ctypes.byref(size), memory, ctypes.byref(source), 0, ctypes.byref(blend), 2):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.visible:
                self._ensure_capture_excluded()
        finally:
            if previous:
                g.SelectObject(memory, previous)
            if bitmap:
                g.DeleteObject(bitmap)
            g.DeleteDC(memory)
            u.ReleaseDC(None, screen)
