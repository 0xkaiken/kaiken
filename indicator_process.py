"""Parent-side recovery for system cursors changed by the Windows HUD."""
import atexit
import ctypes
from ctypes import wintypes as w
import logging
import os
import subprocess
import threading
import time


def restore_system_cursors():
    if os.name != "nt":
        return True
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p, w.UINT]
        for attempt in range(3):
            if user.SystemParametersInfoW(0x57, 0, None, 0):  # SPI_SETCURSORS
                return True
            if attempt < 2:
                time.sleep(0.05)
        logging.warning("Could not restore system cursors after HUD exit")
    except OSError:
        logging.exception("Could not restore system cursors after HUD exit")
    return False


def _stop_process(process, force=False):
    if process.poll() is None:
        try:
            (process.kill if force else process.terminate)()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class IndicatorProcess:
    def __init__(self, process):
        self.process = process
        self._restore_lock = threading.Lock()
        self._restored = False
        self._watcher = threading.Thread(target=self._watch, daemon=True, name="hud-cursor-recovery")
        atexit.register(self.stop)
        self._watcher.start()

    def _restore(self):
        with self._restore_lock:
            if not self._restored:
                self._restored = restore_system_cursors()
            if self._restored:
                atexit.unregister(self.stop)

    def _watch(self):
        self.process.wait()
        self._restore()

    def stop(self, force=False):
        try:
            _stop_process(self.process, force=force)
        finally:
            if self.process.poll() is not None:
                self._restore()

    def terminate(self):
        self.stop()

    def kill(self):
        self.stop(force=True)


def stop_indicator(indicator):
    if indicator is None:
        return
    if isinstance(indicator, IndicatorProcess):
        indicator.stop()
    else:
        try:
            _stop_process(indicator)
        finally:
            if indicator.poll() is not None:
                restore_system_cursors()
