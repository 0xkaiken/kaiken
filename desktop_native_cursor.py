"""Reversible system-cursor replacement owned by the desktop HUD process."""
import atexit
import ctypes
from ctypes import wintypes as w
import logging
import time

STANDARD_CURSORS = (32512, 32513, 32514, 32515, 32516, 32642, 32643,
                    32644, 32645, 32646, 32648, 32649, 32650, 32651)
CURSOR_LINGER = 1.5


class NativeCursor:
    def __init__(self, user):
        self.user = user
        self.hidden = False
        self._restore_pending = True
        user.CreateCursor.argtypes = [w.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_void_p]
        user.CreateCursor.restype = w.HANDLE
        user.SetSystemCursor.argtypes = [w.HANDLE, w.DWORD]
        user.DestroyCursor.argtypes = [w.HANDLE]
        user.LoadCursorW.argtypes, user.LoadCursorW.restype = [w.HINSTANCE, ctypes.c_void_p], w.HANDLE
        user.SetCursor.argtypes = [w.HANDLE]
        user.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p, w.UINT]
        self.restore(force=True)
        atexit.register(self.close)

    def hide(self):
        if self.hidden:
            return True
        and_mask = ctypes.create_string_buffer(b"\xff"*128)
        xor_mask = ctypes.create_string_buffer(b"\x00"*128)
        self.hidden = True
        try:
            for shape in STANDARD_CURSORS:
                cursor = self.user.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
                if not cursor:
                    raise OSError("CreateCursor failed")
                if not self.user.SetSystemCursor(cursor, shape):
                    self.user.DestroyCursor(cursor)
                    raise OSError(f"SetSystemCursor failed for {shape}")
            self.user.SetCursor(self.user.LoadCursorW(None, ctypes.c_void_p(32512)))
            return True
        except Exception:
            logging.exception("Could not replace native cursors; restoring system scheme")
            self.restore(force=True)
            return False

    def restore(self, force=False):
        if not self.hidden and not self._restore_pending and not force:
            return True
        self._restore_pending = True
        if self.user.SystemParametersInfoW(0x57, 0, None, 0):  # SPI_SETCURSORS
            self.hidden = False
            self._restore_pending = False
            self.user.SetCursor(self.user.LoadCursorW(None, ctypes.c_void_p(32512)))
            return True
        logging.warning("Could not restore native system cursors")
        return False

    def close(self):
        if self.restore(force=True):
            atexit.unregister(self.close)


class PointerDisplay:
    """Replace the native cursor only for fresh agent pointer activity."""
    def __init__(self, cursor, native, user):
        self.cursor, self.native, self.user = cursor, native, user
        self.blocked = None

    def hide(self):
        try:
            self.cursor.hide()
        finally:
            self.native.restore()

    def update(self, state):
        activity = state.get("pointer") or {}
        token = (activity.get("owner"), activity.get("serial"))
        position = activity.get("position")
        fresh = 0 <= time.monotonic()-activity.get("updated_at", 0) < CURSOR_LINGER
        eligible = (state.get("active") and state.get("mode") == "desktop" and not state.get("paused")
                    and position is not None and fresh and token != self.blocked)
        if eligible and not activity.get("running"):
            current = w.POINT()
            if not self.user.GetCursorPos(ctypes.byref(current)) or max(abs(current.x-position[0]), abs(current.y-position[1])) > 2:
                self.blocked = token
                eligible = False
        if not eligible:
            self.hide()
        elif self.native.hide():
            self.cursor.update()
        else:
            self.hide()

    def close(self):
        try:
            self.cursor.hide()
        finally:
            self.native.close()
