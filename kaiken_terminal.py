"""KAIKEN terminal presentation, ANSI truecolor support, and live blade schematic animation.

Stdlib-only helper used by ``server.py`` and ``install_windows.py``.
Design goals:
- Exact Unicode display-width alignment (including CJK ``懐剣`` and Turkish letters)
  so box borders never drift or break regardless of Windows path length.
- Continuous, low-CPU 24-bit ANSI TrueColor animation mirroring Command UI's
  ``KAIKEN // CMD-01`` tanto blade schematic:
    1. Boot sequence (~1.1s): Iai-Draw unsheathing + vermilion progress fill.
    2. Live pinned dashboard (while server runs): circular kintsugi-gold radar
       sweep around the calibration reticle, specular hamon glint gliding along
       the 180MM blade, breathing vermilion mekugi pin, periodic Iai-Draw pulse,
       and live server uptime ticker — pinned cleanly above a DECSTBM scrolling
       log region so live server logs scroll underneath without pushing the
       blade off-screen.
- Automatic graceful fallback (static plain text, zero ANSI codes, zero threads)
  when stdout is redirected, piped, or ``NO_COLOR`` / ``KAIKEN_NO_ANIM`` is set.
- Thread-safe console lock shared between boot prints, the live animator, and
  ``logging.StreamHandler`` so daemon logs never collide mid-line.
"""

from __future__ import annotations

import atexit
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
from typing import Optional

# Shared lock preventing stdout/stderr/animator interleaving.
CONSOLE_LOCK = threading.RLock()

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# KAIKEN URUSHI 24-bit ANSI palette (matches index.html / redteam.html / lab.html)
RST = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

SHU = "\x1b[38;2;224;62;45m"          # #e03e2d vermilion seal / mekugi pin
SHU_BRIGHT = "\x1b[38;2;236;75;58m"   # #ec4b3a flash highlight
GOLD = "\x1b[38;2;201;163;94m"        # #c9a35e kintsugi gold / habaki collar
GOLD_BRIGHT = "\x1b[38;2;228;190;120m"
IVORY = "\x1b[38;2;239;236;229m"      # #efece5 primary warm bone
TEXT = "\x1b[38;2;200;196;187m"       # #c8c4bb secondary text
ASH = "\x1b[38;2;133;129;120m"        # #858178 muted slate
BORDER = "\x1b[38;2;68;65;57m"        # subtle matte hairline border
RETICLE = "\x1b[38;2;56;54;48m"       # calibration reticle ring
BLADE_EDGE = "\x1b[38;2;218;214;206m" # polished steel spine
BLADE_BODY = "\x1b[38;2;118;114;106m" # matte steel fuller
BLADE_GLINT = "\x1b[38;2;255;250;240m"
JADE = "\x1b[38;2;90;196;129m"        # #5ac481 online / ready dot
TSUKA = "\x1b[38;2;92;88;81m"         # wrapped hilt

BANNER_ROWS = 15  # 1 top + 13 interior + 1 bottom


def _rgb(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{max(0, min(255, int(r)))};{max(0, min(255, int(g)))};{max(0, min(255, int(b)))}m"


def _lerp_rgb(c0: tuple[int, int, int], c1: tuple[int, int, int], k: float) -> str:
    k = max(0.0, min(1.0, k))
    return _rgb(
        round(c0[0] + (c1[0] - c0[0]) * k),
        round(c0[1] + (c1[1] - c0[1]) * k),
        round(c0[2] + (c1[2] - c0[2]) * k),
    )


def enable_windows_ansi() -> bool:
    """Enable Virtual Terminal Processing on Windows stdout/stderr when interactive."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VT = 0x0004
        ok = False
        for std_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(std_id)
            if not handle or handle == -1:
                continue
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                if kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT):
                    ok = True
        return ok
    except Exception:
        return False


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        return enable_windows_ansi() or bool(os.environ.get("WT_SESSION"))
    return True


def supports_animation(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("KAIKEN_NO_ANIM") in ("1", "true", "yes"):
        return False
    if os.environ.get("CI") or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return supports_color(stream)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def display_width(text: str) -> int:
    """Monospace terminal column width (ANSI-stripped, CJK wide chars = 2)."""
    plain = strip_ansi(text)
    width = 0
    for ch in plain:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad_right(text: str, width: int) -> str:
    w = display_width(text)
    return text + (" " * max(0, width - w))


def pad_center(text: str, width: int) -> str:
    w = display_width(text)
    if w >= width:
        return text
    left = (width - w) // 2
    right = width - w - left
    return (" " * left) + text + (" " * right)


def truncate_display(text: str, max_width: int) -> str:
    """Truncate plain text cleanly to fit ``max_width`` terminal columns."""
    if display_width(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    out = []
    used = 0
    limit = max_width - 1  # reserve 1 col for ellipsis
    for ch in text:
        cw = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def console_print(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = True) -> None:
    """Atomic, newline-safe print synchronized with ``LockedStreamHandler``."""
    target = file or sys.stdout
    text = sep.join(str(a) for a in args) + end
    with CONSOLE_LOCK:
        print(text, end="", file=target, flush=flush)


class LockedStreamHandler(logging.StreamHandler):
    """StreamHandler synchronized with ``CONSOLE_LOCK`` so log records never split stdout lines."""

    def emit(self, record: logging.LogRecord) -> None:
        with CONSOLE_LOCK:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            super().emit(record)


class KaikenLogFormatter(logging.Formatter):
    """Urushi-styled terminal log formatter (plain text when color is disabled)."""

    def __init__(self, use_color: bool):
        super().__init__(datefmt="%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        lvl = record.levelname
        name = record.name
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + "\n" + self.formatException(record.exc_info)
        if not self.use_color:
            return f"{ts} {lvl:<7s} {name}: {msg}"
        lvl_color = (
            SHU_BRIGHT if record.levelno >= logging.ERROR
            else GOLD if record.levelno >= logging.WARNING
            else ASH
        )
        return f"  {ASH}{ts}{RST} {lvl_color}{lvl:<7s}{RST} {GOLD}{name}{ASH}:{RST} {TEXT}{msg}{RST}"


def format_status_line(tag: str, message: str, *, kind: str = "info", use_color: Optional[bool] = None) -> str:
    color = supports_color() if use_color is None else use_color
    if not color:
        return f"[{tag}] {message}"
    if kind == "ok":
        badge_c, msg_c = JADE, IVORY
    elif kind == "accent":
        badge_c, msg_c = SHU_BRIGHT, IVORY
    elif kind == "warn":
        badge_c, msg_c = GOLD_BRIGHT, TEXT
    elif kind == "err":
        badge_c, msg_c = SHU_BRIGHT, SHU_BRIGHT
    else:
        badge_c, msg_c = GOLD, TEXT
    return f"  {BORDER}[{RST}{badge_c}{BOLD}{tag}{RST}{BORDER}]{RST} {msg_c}{message}{RST}"


def format_pairing_banner(code: str, reason: str = "", *, use_color: Optional[bool] = None) -> str:
    """Clean Urushi pairing card that preserves exact ``pairing code: <code>`` text."""
    color = supports_color() if use_color is None else use_color
    if not color:
        note = f"    ({reason})\n" if reason else ""
        return (
            "\n    Pair a browser: open the dashboard and paste this one-time code\n"
            f"{note}"
            f"    pairing code: {code}\n"
        )
    lines = [
        f"{TEXT}Pair a browser: open the dashboard and paste this one-time code{RST}",
    ]
    if reason:
        lines.append(f"{ASH}({reason}){RST}")
    lines.append(f"{ASH}pairing code:{RST} {GOLD_BRIGHT}{BOLD}{code}{RST}")
    inner_w = max(display_width(ln) for ln in lines) + 4
    top = f"  {BORDER}╭{'─' * inner_w}╮{RST}"
    bot = f"  {BORDER}╰{'─' * inner_w}╯{RST}"
    body = [f"  {BORDER}│{RST}  {pad_right(ln, inner_w - 2)}{BORDER}│{RST}" for ln in lines]
    return "\n" + "\n".join([top, *body, bot]) + "\n"


def _colorize_reticle_segment(text: str, row_idx: int, col_start: int, sweep_angle: float,
                              flash: bool, use_color: bool) -> str:
    """Apply a smooth circular 24-bit radar sheen across reticle box characters."""
    if not use_color:
        return text
    if flash:
        return f"{GOLD_BRIGHT}{text}{RST}"
    base_rgb = (56, 54, 48)
    gold_rgb = (224, 184, 114)
    out = []
    last_code = ""
    for offset, ch in enumerate(text):
        if ch == " ":
            if last_code:
                out.append(RST)
                last_code = ""
            out.append(" ")
            continue
        col = col_start + offset
        # Aspect-corrected circular angle around blade center (col=16, row=3.5)
        ang = math.atan2((row_idx - 3.5) * 2.05, col - 16.0)
        # Trailing wedge behind sweep_angle (clockwise)
        diff = (sweep_angle - ang) % (2.0 * math.pi)
        if diff < 1.35:
            intensity = (1.0 - diff / 1.35) ** 1.85
            code = _lerp_rgb(base_rgb, gold_rgb, intensity)
        else:
            code = RETICLE
        if code != last_code:
            out.append(code)
            last_code = code
        out.append(ch)
    if last_code:
        out.append(RST)
    return "".join(out)


def _blade_frame_lines(t: float, use_color: bool, status_label: str, *, elapsed: float = 0.0) -> list[str]:
    """Render one 13-row × 33-col dead-center symmetric KAIKEN // CMD-01 blade schematic.

    - During boot (``t < 1.0``): ``STANDBY`` -> ``DRAWING`` (Iai-Draw) -> ``ONLINE``.
    - Once online (``t >= 1.0``, driven by ``elapsed`` seconds):
      * Smooth clockwise circular kintsugi-gold radar sweep across the reticle ring
      * Travelling specular glint gliding up the 180MM tanto spine every ~2.6s
      * Sinusoidal breathing vermilion mekugi pin (``●``)
      * Periodic crisp Iai-Draw unsheathing pulse every 7.5s (~0.85s hold)
    """
    W = 33  # odd column width -> exact center spine at col 16

    def c(code: str, s: str) -> str:
        return f"{code}{s}{RST}" if use_color else s

    live = t >= 1.0 and elapsed > 0.0
    if not live:
        sweep_angle = t * 2.0 * math.pi * 1.15 - math.pi / 2.0
        if t < 0.42:
            lift = 0
            glint_pos = (t / 0.42) * 5.0
            flash = False
            phase = "STANDBY"
        elif t < 0.78:
            u = (t - 0.42) / 0.36
            lift = 1 if u > 0.16 else 0
            glint_pos = 4.5 - u * 5.5
            flash = 0.22 <= u <= 0.64
            phase = "DRAWING"
        else:
            lift = 0
            glint_pos = -9.0
            flash = False
            phase = status_label
        pin_k = 1.0 if flash else 0.5 + 0.5 * math.sin(t * math.pi * 6.0)
    else:
        # Continuous calm living schematic matching Command UI's 144 FPS idle blade
        sweep_angle = (elapsed * 2.2) % (2.0 * math.pi)
        # Periodic subtle Iai-Draw breath every 7.5s
        cycle = elapsed % 7.5
        if 5.8 <= cycle <= 6.75:
            u = (cycle - 5.8) / 0.95
            lift = 1
            flash = 0.15 <= u <= 0.48
            glint_pos = 4.5 - u * 5.5
            phase = "DRAWN"
        else:
            lift = 0
            flash = False
            # Gentle glint travels from habaki (row 4) to kissaki tip (row 0) every 2.8s
            g_cyc = elapsed % 2.8
            glint_pos = (4.8 - (g_cyc / 1.15) * 5.6) if g_cyc < 1.15 else -9.0
            phase = status_label
        pin_k = 1.0 if flash else 0.35 + 0.65 * (0.5 + 0.5 * math.sin(elapsed * 2.6))

    pin_color = _lerp_rgb((128, 32, 22), (244, 78, 60), pin_k)
    status_c = JADE if phase == "ONLINE" else (SHU_BRIGHT if phase in ("DRAWING", "DRAWN") else GOLD)

    def blade_row(idx: int, glyph: str) -> str:
        dist = abs(idx - glint_pos)
        if flash and dist < 1.25:
            return c(BLADE_GLINT + BOLD, glyph)
        if dist < 0.75:
            return c(BLADE_GLINT + BOLD, glyph)
        if dist < 1.55:
            return c(GOLD_BRIGHT, glyph)
        return c(BLADE_EDGE, glyph)

    tip = blade_row(0, "╱│╲")
    b1 = blade_row(1, "│░│")
    b2 = blade_row(2, "│▒│")
    b3 = blade_row(3, "│░│")
    b4 = blade_row(4, "│▒│")
    guard_k = 1.0 if (flash or lift) else (0.3 + 0.4 * (0.5 + 0.5 * math.sin(sweep_angle)))
    guard = c(_lerp_rgb((168, 134, 74), (238, 202, 130), guard_k), "══╬══")
    hilt1 = c(TSUKA, "│") + c(pin_color, "●") + c(TSUKA, "│")
    hilt2 = c(TSUKA, "╰─╯")

    header = (
        c(ASH, "KAIKEN // CMD-01")
        + " " * max(1, W - 16 - len(phase))
        + c(status_c + BOLD, phase)
    )
    tag_left = c(GOLD_BRIGHT if lift else ASH, " DRAWN " if lift else "SHEATH ")
    tag_right = c(ASH, "180MM")

    def ret(row_i: int, col_s: int, seg: str) -> str:
        return _colorize_reticle_segment(seg, row_i, col_s, sweep_angle, flash, use_color)

    # Every row sums to EXACTLY 33 display columns (blade spine locked to cols 15..17)
    if lift:
        mid_rows = [
            "      " + ret(0, 6, "· · ╭─── ") + tip + ret(0, 18, " ───╮ · ·") + "      ",
            "     " + ret(1, 5, "╭──╯      ") + b1 + ret(1, 18, "      ╰──╮") + "     ",
            "     " + ret(2, 5, "│         ") + b2 + "  " + tag_right + "  " + ret(2, 27, "│") + "     ",
            ret(3, 0, "─────┼──────── ") + b3 + ret(3, 18, " ────────┼─────"),
            "     " + ret(4, 5, "│ ") + tag_left + " " + b4 + "         " + ret(4, 27, "│") + "     ",
            "     " + ret(5, 5, "╰──╮     ") + guard + ret(5, 19, "     ╭──╯") + "     ",
            "      " + ret(6, 6, "· · ╰─── ") + hilt1 + ret(6, 18, " ───╯ · ·") + "      ",
            "               " + hilt2 + "               ",
        ]
    else:
        mid_rows = [
            "      " + ret(0, 6, "· · ╭───────────╮ · ·") + "      ",
            "     " + ret(1, 5, "╭──╯      ") + tip + ret(1, 18, "      ╰──╮") + "     ",
            "     " + ret(2, 5, "│         ") + b1 + "  " + tag_right + "  " + ret(2, 27, "│") + "     ",
            ret(3, 0, "─────┼──────── ") + b2 + ret(3, 18, " ────────┼─────"),
            "     " + ret(4, 5, "│ ") + tag_left + " " + b3 + "         " + ret(4, 27, "│") + "     ",
            "     " + ret(5, 5, "╰──╮     ") + guard + ret(5, 19, "     ╭──╯") + "     ",
            "      " + ret(6, 6, "· · ╰─── ") + hilt1 + ret(6, 18, " ───╯ · ·") + "      ",
            "               " + hilt2 + "               ",
        ]

    if phase == "ONLINE":
        footer_text = "ONLINE"
    elif phase in ("DRAWING", "DRAWN"):
        footer_text = "IAI-DRAW ACTIVE" if live else "UNSHEATHING"
    else:
        footer_text = "AWAITING DIRECTIVE"
    footer = pad_center(
        c(IVORY, "懐剣") + c(SHU, " · ") + c(GOLD_BRIGHT if phase in ("ONLINE", "DRAWN") else ASH, footer_text),
        W,
    )

    lines = [
        header,
        " " * W,
        *mid_rows,
        " " * W,
        footer,
        " " * W,
    ]
    return [pad_right(ln, W) for ln in lines[:13]]


def _fmt_uptime(secs: float) -> str:
    s = max(0, int(secs))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def build_startup_frame(
    *,
    port: int,
    version: str,
    workshop: str,
    state_dir: str,
    log_path: str,
    runtime: str = "",
    t: float = 1.0,
    elapsed: float = 0.0,
    use_color: bool = True,
    term_cols: Optional[int] = None,
) -> str:
    """Build the complete side-by-side KAIKEN blade + telemetry box with exact alignment."""
    if term_cols is None:
        term_cols = shutil.get_terminal_size((108, 30)).columns

    def c(code: str, s: str) -> str:
        return f"{code}{s}{RST}" if use_color else s

    url = f"http://localhost:{port}"
    status_label = "ONLINE" if t >= 0.78 else ("DRAWING" if t >= 0.42 else "STANDBY")

    left_w = 33
    compact = term_cols < 84
    max_right_w = max(48, term_cols - 6) if compact else max(38, term_cols - left_w - 9)

    def kv_row(label: str, value: str, val_color: str = TEXT) -> str:
        prefix = f"{label:<10s}"
        avail = max(12, max_right_w - len(prefix))
        trimmed = truncate_display(str(value), avail)
        return c(ASH, prefix) + c(val_color, trimmed)

    seal = c(SHU_BRIGHT + BOLD, "懐剣")
    title_line = f"{seal}  {c(IVORY + BOLD, 'KAIKEN')} {c(ASH, '—')} {c(GOLD, 'Mission Control')}"
    sub_text = truncate_display("Local Agent Command · Blood Agent · Carrier Lab", max_right_w)
    sub_line = c(ASH, sub_text)
    rt_val = runtime or os.environ.get("PLINY_AGENT_RUNTIME", "codex")

    if t >= 1.0 and elapsed > 0.0:
        pulse_dot = c(_lerp_rgb((58, 145, 92), (105, 224, 148), 0.5 + 0.5 * math.sin(elapsed * 2.8)), "●")
        prog_line = f"{pulse_dot} {c(JADE + BOLD, 'ONLINE')}  {c(ASH, 'uptime')} {c(IVORY, _fmt_uptime(elapsed))}"
    else:
        progress_cells = 20
        filled = int(round(max(0.0, min(1.0, t)) * progress_cells))
        bar = c(SHU_BRIGHT, "━" * filled) + c(BORDER, "─" * (progress_cells - filled))
        pct = f"{int(round(t * 100)):3d}%"
        boot_label = "READY  " if t >= 1.0 else "BOOTING"
        boot_color = JADE if t >= 1.0 else GOLD
        prog_line = f"{c(boot_color + BOLD, boot_label)} {bar} {c(ASH, pct)}"

    right_raw = [
        title_line,
        sub_line,
        "",
        kv_row("Dashboard", url, IVORY + BOLD),
        kv_row("Version", version, GOLD_BRIGHT),
        kv_row("Runtime", rt_val, TEXT),
        "",
        kv_row("Workshop", workshop, TEXT),
        kv_row("State", state_dir, TEXT),
        kv_row("Logs", log_path, ASH),
        "",
        prog_line,
        c(ASH, truncate_display("Ctrl+C to stop cleanly · live log stream below", max_right_w)),
    ]

    right_w = max(44, max(display_width(r) for r in right_raw))
    right_w = min(right_w, max_right_w)

    if compact:
        box_w = right_w
        top = f"  {c(BORDER, '╭' + '─' * (box_w + 2) + '╮')}"
        bot = f"  {c(BORDER, '╰' + '─' * (box_w + 2) + '╯')}"
        out = [top]
        for r in right_raw:
            out.append(f"  {c(BORDER, '│')} {pad_right(r, box_w)} {c(BORDER, '│')}")
        out.append(bot)
        return "\n".join(out)

    left_rows = _blade_frame_lines(t, use_color, status_label, elapsed=elapsed)
    top = f"  {c(BORDER, '╭' + '─' * (left_w + 2) + '┬' + '─' * (right_w + 2) + '╮')}"
    bot = f"  {c(BORDER, '╰' + '─' * (left_w + 2) + '┴' + '─' * (right_w + 2) + '╯')}"
    out = [top]
    for lr, rr in zip(left_rows, right_raw):
        out.append(
            f"  {c(BORDER, '│')} {pad_right(lr, left_w)} {c(BORDER, '│')} {pad_right(rr, right_w)} {c(BORDER, '│')}"
        )
    out.append(bot)
    return "\n".join(out)


def play_startup_animation(
    *,
    port: int,
    version: str,
    workshop: str,
    state_dir: str,
    log_path: str,
    runtime: str = "",
    duration: float = 1.15,
    fps: int = 40,
) -> None:
    """Animate the KAIKEN blade schematic smoothly in-place on TTYs, or print static box."""
    use_color = supports_color()
    animate = supports_animation()
    with CONSOLE_LOCK:
        if not animate:
            frame = build_startup_frame(
                port=port,
                version=version,
                workshop=workshop,
                state_dir=state_dir,
                log_path=log_path,
                runtime=runtime,
                t=1.0,
                use_color=use_color,
            )
            sys.stdout.write("\n" + frame + "\n\n")
            sys.stdout.flush()
            return

        total_frames = max(12, int(duration * fps))
        frame_dt = duration / total_frames
        prev_lines = 0
        try:
            sys.stdout.write("\n\x1b[?25l")
            sys.stdout.flush()
            start = time.perf_counter()
            for idx in range(total_frames + 1):
                t = idx / total_frames
                frame = build_startup_frame(
                    port=port,
                    version=version,
                    workshop=workshop,
                    state_dir=state_dir,
                    log_path=log_path,
                    runtime=runtime,
                    t=t,
                    use_color=True,
                )
                lines = frame.splitlines()
                buf = []
                if prev_lines:
                    buf.append(f"\x1b[{prev_lines}A\r")
                for line in lines:
                    buf.append("\x1b[2K" + line + "\n")
                sys.stdout.write("".join(buf))
                sys.stdout.flush()
                prev_lines = len(lines)
                if idx < total_frames:
                    target_t = start + (idx + 1) * frame_dt
                    rem = target_t - time.perf_counter()
                    if rem > 0:
                        time.sleep(rem)
            sys.stdout.write("\n")
            sys.stdout.flush()
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()


class LiveBannerAnimator:
    """Pinned live KAIKEN blade schematic over a DECSTBM scrolling log region.

    Keeps the top 15 rows continuously animated (~20 FPS) while all server logs,
    pairing codes, and daemon messages stream naturally in rows 17..bottom.
    """

    def __init__(
        self,
        *,
        port: int,
        version: str,
        workshop: str,
        state_dir: str,
        log_path: str,
        runtime: str = "",
        fps: int = 20,
    ) -> None:
        self.port = port
        self.version = version
        self.workshop = workshop
        self.state_dir = state_dir
        self.log_path = log_path
        self.runtime = runtime
        self.fps = max(8, min(30, int(fps)))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = time.monotonic()
        self._scroll_region_active = False
        self._last_size = (0, 0)

    def _can_pin(self) -> tuple[bool, int, int]:
        sz = shutil.get_terminal_size((108, 30))
        return (sz.columns >= 84 and sz.lines >= BANNER_ROWS + 7), sz.columns, sz.lines

    def _draw_top_locked(self, t: float, elapsed: float, cols: int) -> None:
        frame = build_startup_frame(
            port=self.port,
            version=self.version,
            workshop=self.workshop,
            state_dir=self.state_dir,
            log_path=self.log_path,
            runtime=self.runtime,
            t=t,
            elapsed=elapsed,
            use_color=True,
            term_cols=cols,
        )
        lines = frame.splitlines()
        buf = ["\x1b[1;1H"]
        for row_idx, line in enumerate(lines, 1):
            buf.append(f"\x1b[{row_idx};1H\x1b[2K{line}")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def start(self, boot_duration: float = 1.05) -> "LiveBannerAnimator":
        """Play the boot Iai-Draw intro, pin the header, and launch the live idle animator."""
        if not supports_animation():
            play_startup_animation(
                port=self.port,
                version=self.version,
                workshop=self.workshop,
                state_dir=self.state_dir,
                log_path=self.log_path,
                runtime=self.runtime,
            )
            return self

        can_pin, cols, rows = self._can_pin()
        if not can_pin:
            play_startup_animation(
                port=self.port,
                version=self.version,
                workshop=self.workshop,
                state_dir=self.state_dir,
                log_path=self.log_path,
                runtime=self.runtime,
                duration=boot_duration,
            )
            return self

        self._started_at = time.monotonic()
        self._last_size = (cols, rows)
        with CONSOLE_LOCK:
            try:
                # Clear viewport, draw pinned top region (rows 1..15), reserve rows 17..bottom for logs
                sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l")
                sys.stdout.flush()
                steps = max(14, int(boot_duration * 36))
                step_dt = boot_duration / steps
                t0 = time.perf_counter()
                for idx in range(steps + 1):
                    self._draw_top_locked(idx / steps, 0.0, cols)
                    if idx < steps:
                        rem = (t0 + (idx + 1) * step_dt) - time.perf_counter()
                        if rem > 0:
                            time.sleep(rem)
                # Set DECSTBM scroll region strictly BELOW the 15-row blade schematic
                log_top = BANNER_ROWS + 2
                sys.stdout.write(f"\x1b[{log_top};{rows}r\x1b[{log_top};1H\x1b[?25h")
                sys.stdout.flush()
                self._scroll_region_active = True
            except Exception:
                sys.stdout.write("\x1b[r\x1b[?25h")
                sys.stdout.flush()
                self._scroll_region_active = False
                return self

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="kaiken-blade-anim", daemon=True)
        self._thread.start()
        atexit.register(self.stop)
        return self

    def _loop(self) -> None:
        interval = 1.0 / self.fps
        while not self._stop_event.wait(interval):
            can_pin, cols, rows = self._can_pin()
            with CONSOLE_LOCK:
                if self._stop_event.is_set():
                    break
                if not can_pin:
                    if self._scroll_region_active:
                        sys.stdout.write("\x1b[r\x1b[?25h")
                        sys.stdout.flush()
                        self._scroll_region_active = False
                    continue
                try:
                    resized = (cols, rows) != self._last_size or not self._scroll_region_active
                    elapsed = time.monotonic() - self._started_at
                    if resized:
                        self._last_size = (cols, rows)
                        log_top = BANNER_ROWS + 2
                        sys.stdout.write(f"\x1b[?25l\x1b[{log_top};{rows}r")
                        self._scroll_region_active = True
                        self._draw_top_locked(1.0, elapsed, cols)
                        sys.stdout.write(f"\x1b[{log_top};1H\x1b[?25h")
                    else:
                        sys.stdout.write("\x1b7\x1b[?25l")
                        self._draw_top_locked(1.0, elapsed, cols)
                        sys.stdout.write("\x1b8\x1b[?25h")
                    sys.stdout.flush()
                except Exception:
                    break

    def stop(self) -> None:
        """Stop animation cleanly and release the DECSTBM scroll region."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.5)
        with CONSOLE_LOCK:
            if self._scroll_region_active:
                self._scroll_region_active = False
                try:
                    rows = shutil.get_terminal_size((108, 30)).lines
                    sys.stdout.write(f"\x1b[r\x1b[{rows};1H\x1b[?25h\n")
                    sys.stdout.flush()
                except Exception:
                    pass
            elif supports_color():
                try:
                    sys.stdout.write("\x1b[?25h")
                    sys.stdout.flush()
                except Exception:
                    pass


def start_live_banner(
    *,
    port: int,
    version: str,
    workshop: str,
    state_dir: str,
    log_path: str,
    runtime: str = "",
) -> LiveBannerAnimator:
    """Start the live pinned KAIKEN blade schematic banner (static fallback when non-TTY)."""
    animator = LiveBannerAnimator(
        port=port,
        version=version,
        workshop=workshop,
        state_dir=state_dir,
        log_path=log_path,
        runtime=runtime,
    )
    return animator.start()
