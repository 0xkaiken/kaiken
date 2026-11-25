"""Small, click-through desktop cursor HUD; image builders are platform independent."""
import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import math
import time

from PIL import Image, ImageDraw, ImageFilter

from desktop_glow import ScreenEdgeGlow

HOTSPOT = (12, 12)
TRAIL_SECONDS = 0.4
RIPPLE_SECONDS = 0.5


@lru_cache(maxsize=2)
def cursor_image(paused=False):
    """A crisp KAIKEN blade-inspired pointer; HOTSPOT is the exact opaque tip."""
    scale = 3
    size = (76, 80)
    w_px, h_px = size[0] * scale, size[1] * scale
    mask = Image.new("L", (w_px, h_px), 0)
    # Sleek modern architectural arrow silhouette
    points = [(12, 12), (14, 60), (26, 48), (35, 65), (43, 61), (34, 44), (51, 43)]
    draw = ImageDraw.Draw(mask)
    draw.polygon([(x * scale, y * scale) for x, y in points], fill=255)

    outline = mask.filter(ImageFilter.MaxFilter(11))
    glow = outline.filter(ImageFilter.GaussianBlur(4 * scale))

    # Bright warm ambient halo (high legibility on both light and dark screens)
    halo_rgb = (255, 186, 56) if paused else (240, 102, 82)
    result = Image.new("RGBA", mask.size, (*halo_rgb, 0))
    result.putalpha(glow.point(lambda a: round(a * 0.52)))

    # Crisp warm graphite contour (not too dark, clean contrast on light & dark apps)
    contour_rgb = (48, 41, 28, 255) if paused else (46, 43, 38, 255)
    result.paste(Image.new("RGBA", mask.size, contour_rgb), (0, 0), outline)

    # Luminous warm body fill: sunlit gold when paused; bright warm ivory -> champagne with vermilion apex when active
    if paused:
        palette = ((255, 186, 42), (255, 204, 86), (244, 168, 44))
    else:
        palette = ((238, 86, 66), (248, 244, 236), (232, 212, 174))
    fill = Image.new("RGBA", mask.size)
    lines = ImageDraw.Draw(fill)
    for y in range(fill.height):
        p = max(0.0, min(2.0, (y / scale - 12) / 53 * 2))
        if not paused and p < 0.42:
            # Crisp vermilion leading tip transitioning smoothly into luminous alabaster body
            f = p / 0.42
            color = tuple(round(palette[0][c] * (1 - f) + palette[1][c] * f) for c in range(3))
        elif not paused:
            f = (p - 0.42) / 1.58
            color = tuple(round(palette[1][c] * (1 - f) + palette[2][c] * f) for c in range(3))
        else:
            section = min(1, int(p))
            f = p - section
            color = tuple(round(palette[section][c] * (1 - f) + palette[section + 1][c] * f) for c in range(3))
        lines.line((0, y, fill.width, y), fill=(*color, 255))
    result.paste(fill, (0, 0), mask)

    # Subtle inner spine bevel highlight on active pointer
    if not paused:
        bevel = Image.new("RGBA", mask.size, (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bevel)
        bdraw.line([(14 * scale, 16 * scale), (27 * scale, 45 * scale)], fill=(255, 255, 255, 115), width=2)
        result = Image.alpha_composite(result, bevel)

    result = result.resize(size, Image.Resampling.LANCZOS)
    result.putpixel(HOTSPOT, (255, 176, 0, 255) if paused else (238, 84, 64, 255))
    return result


def compose_frame(position, points, ripples, now, paused=False):
    """Return (RGBA image, screen origin) for timestamped (x, y, time) samples."""
    points = [p for p in points if 0 <= now-p[2] < TRAIL_SECONDS]
    ripples = [p for p in ripples if 0 <= now-p[2] < RIPPLE_SECONDS]
    x, y = position
    extents = [(x-12, y-12, x+64, y+68)]
    extents += [(px-10, py-10, px+10, py+10) for px, py, _ in points]
    extents += [(px-44, py-44, px+44, py+44) for px, py, _ in ripples]
    left, top = math.floor(min(e[0] for e in extents))-2, math.floor(min(e[1] for e in extents))-2
    right, bottom = math.ceil(max(e[2] for e in extents))+2, math.ceil(max(e[3] for e in extents))+2
    frame = Image.new("RGBA", (right-left, bottom-top))
    color = (255, 184, 48) if paused else (240, 96, 74)
    inner_color = (255, 232, 168) if paused else (248, 240, 224)
    for i, (px, py, stamp) in enumerate(points):
        freshness = 1 - (now - stamp) / TRAIL_SECONDS
        alpha = round(175 * freshness)
        w_line = max(2, round(4 * freshness))
        layer = Image.new("RGBA", frame.size)
        draw = ImageDraw.Draw(layer)
        end = points[i+1][:2] if i+1 < len(points) else position
        draw.line((px-left, py-top, end[0]-left, end[1]-top), fill=(*color, alpha), width=w_line)
        r_dot = max(1.5, 2.5 * freshness)
        draw.ellipse((px-left-r_dot, py-top-r_dot, px-left+r_dot, py-top+r_dot), fill=(*color, alpha))
        frame.alpha_composite(layer)
    for px, py, stamp in ripples:
        fraction = (now-stamp)/RIPPLE_SECONDS
        radius = 7+34*fraction
        layer = Image.new("RGBA", frame.size)
        rdraw = ImageDraw.Draw(layer)
        rdraw.ellipse((px-left-radius, py-top-radius, px-left+radius, py-top+radius),
                      outline=(*color, round(235*(1-fraction))), width=3)
        if radius > 12:
            r2 = radius * 0.62
            rdraw.ellipse((px-left-r2, py-top-r2, px-left+r2, py-top+r2),
                          outline=(*inner_color, round(155*(1-fraction))), width=2)
        frame.alpha_composite(layer)
    frame.alpha_composite(cursor_image(paused), (x-left-HOTSPOT[0], y-top-HOTSPOT[1]))
    return frame, (left, top)


class AgentCursor(ScreenEdgeGlow):
    """Reuse the glow's disabled layered-window transport, not its monitor canvas."""
    def __init__(self, user, instance, owner):
        super().__init__(user, instance, owner)
        user.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
        user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user.GetAsyncKeyState.restype = ctypes.c_short
        self.points = []
        self.ripples = []
        self.position = None
        self.buttons = (False,)*3
        self.paused = None

    def hide(self):
        super().hide()
        self.points.clear()
        self.ripples.clear()
        self.position = None
        self.buttons = tuple(bool(self.user.GetAsyncKeyState(key) & 0x8000) for key in (1, 2, 4))

    def update(self, paused=False):
        now = time.monotonic()
        point = w.POINT()
        if not self.user.GetCursorPos(ctypes.byref(point)):
            return
        position = (point.x, point.y)
        states = tuple(self.user.GetAsyncKeyState(key) for key in (1, 2, 4))
        buttons = tuple(bool(value & 0x8000) for value in states)
        animated = bool(self.points or self.ripples)
        self.points = [p for p in self.points if now-p[2] < TRAIL_SECONDS]
        self.ripples = [p for p in self.ripples if now-p[2] < RIPPLE_SECONDS]
        if self.position is not None and position != self.position:
            self.points.append((*self.position, now))
        if any((value & 1) or (down and not was) for value, down, was in zip(states, buttons, self.buttons)):
            self.ripples.append((*position, now))
        if position != self.position or buttons != self.buttons or paused != self.paused or animated or self.ripples or not self.visible:
            frame, origin = compose_frame(position, self.points, self.ripples, now, paused)
            self._paint(frame, *origin)
        self.position, self.buttons, self.paused = position, buttons, paused
        if not self.visible:
            self.user.ShowWindow(self.hwnd, 4)
            self.visible = True
        self._ensure_capture_excluded()
