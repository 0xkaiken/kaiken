#!/usr/bin/env python3
"""v1s10n — visual inspection and visual diff for SHIP MODE.

Called by the Ship Mode preset as::

    python3 tools/v1s10n.py --test-url http://localhost:PORT --focus "layout, polish"
    python3 tools/v1s10n.py --shot http://localhost:PORT out.png
    python3 tools/v1s10n.py --diff before.png after.png

What it can and cannot do, stated plainly so the agent does not over-trust it:

* it CAN capture a deterministic screenshot and report measurable properties
  (dimensions, palette size, blank-space ratio, edge density, contrast);
* it CAN tell you whether two screenshots differ, by how much, and where;
* it CANNOT judge taste.  Read the PNG yourself; the metrics are there to stop
  you from claiming a change you did not make.

Exit codes: 0 ok, 1 diff below threshold (no visible change), 2 usage error,
3 dependency/browser unavailable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_VIEWPORT = (1440, 900)


def _require(name: str):
    """Import a possibly-dotted module path, returning None when unavailable."""
    try:
        import importlib
        return importlib.import_module(name)
    except Exception:
        return None


def _shot(url: str, out: Path, width: int, height: int, full: bool,
          wait_ms: int, browser: str) -> int:
    sync_api = _require("playwright.sync_api")
    if sync_api is None:
        print("error: playwright is not installed. Run:\n"
              "  python -m pip install playwright\n"
              "  python -m playwright install firefox", file=sys.stderr)
        return 3
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_api.sync_playwright() as pw:
            launcher = getattr(pw, browser, None)
            if launcher is None:
                print(f"error: unknown browser {browser!r} "
                      f"(chromium, firefox, webkit)", file=sys.stderr)
                return 2
            b = launcher.launch()
            page = b.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="networkidle", timeout=45000)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(out), full_page=full)
            b.close()
    except Exception as exc:
        print(f"error: capture failed: {type(exc).__name__}: {exc}\n"
              f"hint: python -m playwright install {browser}", file=sys.stderr)
        return 3
    print(f"captured {url} -> {out} ({out.stat().st_size} bytes)")
    return 0


def _metrics(path: Path) -> dict:
    image = _require("PIL.Image")
    if image is None:
        return {}
    im = image.open(path).convert("RGB")
    w, h = im.size
    small = im.resize((max(1, w // 4), max(1, h // 4)))
    colors = small.getcolors(maxcolors=1 << 24) or []
    palette = len(colors)
    pixels = list(small.getdata())
    total = len(pixels) or 1
    near_white = sum(1 for r, g, b in pixels if r > 244 and g > 244 and b > 244)
    luma = [(0.2126 * r + 0.7152 * g + 0.0722 * b) for r, g, b in pixels]
    mean = sum(luma) / total
    var = sum((x - mean) ** 2 for x in luma) / total

    edges = _require("PIL.ImageFilter")
    edge_density = None
    if edges is not None:
        gray = image.open(path).convert("L").resize((max(1, w // 2), max(1, h // 2)))
        filt = gray.filter(edges.FIND_EDGES)
        hist = filt.histogram()
        strong = sum(hist[40:])
        edge_density = round(strong / max(1, sum(hist)), 4)

    band = 0
    best = 0
    for row in range(small.height):
        y = row
        row_pixels = [small.getpixel((x, y)) for x in range(small.width)]
        if all(r > 244 and g > 244 and b > 244 for r, g, b in row_pixels):
            band += 1
            best = max(best, band)
        else:
            band = 0

    return {
        "size": [w, h],
        "palette": palette,
        "blank_ratio": round(near_white / total, 4),
        "contrast": round(var ** 0.5, 2),
        "mean_luma": round(mean, 2),
        "edge_density": edge_density,
        "largest_blank_band_px": best * 4,
    }


def _diff(a: Path, b: Path, out: Path | None, threshold: float) -> int:
    image = _require("PIL.Image")
    chop = _require("PIL.ImageChops")
    if image is None or chop is None:
        print("error: Pillow is not installed. Run: python -m pip install Pillow",
              file=sys.stderr)
        return 3
    ia = image.open(a).convert("RGB")
    ib = image.open(b).convert("RGB")
    if ia.size != ib.size:
        print(f"note: size differs {ia.size} vs {ib.size}; "
              f"cropping to the overlap", file=sys.stderr)
        w = min(ia.width, ib.width)
        h = min(ia.height, ib.height)
        ia = ia.crop((0, 0, w, h))
        ib = ib.crop((0, 0, w, h))

    delta = chop.difference(ia, ib)
    gray = delta.convert("L")
    hist = gray.histogram()
    total = sum(hist) or 1
    changed = sum(hist[16:])
    pct = 100.0 * changed / total

    bbox = gray.point(lambda v: 255 if v > 16 else 0).getbbox()

    result = {
        "a": str(a), "b": str(b),
        "size": list(ia.size),
        "changed_pixel_pct": round(pct, 3),
        "changed_bbox": list(bbox) if bbox else None,
        "threshold_pct": threshold,
        "verdict": "changed" if pct >= threshold else "no-visible-change",
    }

    if out:
        boosted = gray.point(lambda v: min(255, v * 4))
        boosted.save(out)
        result["diff_image"] = str(out)

    print(json.dumps(result, indent=2))
    return 0 if pct >= threshold else 1


def _test_url(url: str, focus: list[str], out_dir: Path, width: int,
              height: int, full: bool, wait_ms: int, browser: str) -> int:
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"shot-{stamp}.png"
    rc = _shot(url, out, width, height, full, wait_ms, browser)
    if rc != 0:
        return rc

    metrics = _metrics(out)
    report = {
        "url": url,
        "screenshot": str(out),
        "focus": focus,
        "metrics": metrics,
        "checklist": [
            "Hierarchy: is there exactly one obvious focal point above the fold?",
            "Spacing: does padding follow a single scale, or does it drift?",
            "Type: are there more than three type sizes doing real work?",
            "Color: is the accent used for one job only?",
            "States: do buttons/inputs show hover, focus and disabled?",
            "Overflow: does anything clip at this viewport width?",
            "Empty space: is the largest blank band a deliberate rhythm "
            "or a hole? (see largest_blank_band_px)",
            "Copy: does every label survive being read out of context?",
        ],
    }
    print(json.dumps(report, indent=2))
    print(f"\nread the screenshot: {out}", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="v1s10n", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--test-url", metavar="URL", help="capture + metrics + checklist")
    g.add_argument("--shot", nargs=2, metavar=("URL", "OUT"), help="capture only")
    g.add_argument("--diff", nargs=2, metavar=("A", "B"), help="visual diff")

    p.add_argument("--focus", default="layout, polish, brand fidelity")
    p.add_argument("--out-dir", default="ships/_v1s10n")
    p.add_argument("--diff-out", help="write the amplified diff image here")
    p.add_argument("--threshold", type=float, default=0.35,
                   help="percent of pixels that must change to count (default 0.35)")
    p.add_argument("--width", type=int, default=DEFAULT_VIEWPORT[0])
    p.add_argument("--height", type=int, default=DEFAULT_VIEWPORT[1])
    p.add_argument("--full", action="store_true", help="full-page capture")
    p.add_argument("--wait-ms", type=int, default=600)
    p.add_argument("--browser", default="firefox",
                   choices=["firefox", "chromium", "webkit"])
    args = p.parse_args()

    if args.test_url:
        return _test_url(args.test_url, [f.strip() for f in args.focus.split(",")],
                         Path(args.out_dir), args.width, args.height, args.full,
                         args.wait_ms, args.browser)
    if args.shot:
        url, out = args.shot
        return _shot(url, Path(out), args.width, args.height, args.full,
                     args.wait_ms, args.browser)
    a, b = args.diff
    diff_out = Path(args.diff_out) if args.diff_out else None
    return _diff(Path(a), Path(b), diff_out, args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
