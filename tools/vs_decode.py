"""Decode invisible stego channels: variation selectors and zero-width bits.

Built after capturing a live payload in the wild: the Spiritual-Spell
red-teaming repo (github.com/Goochbeater/Spiritual-Spell-Red-Teaming) hides an
agent-targeted prompt injection in its README behind Unicode Variation
Selectors (U+E0100–U+E01EF) planted after a visible emoji. This is the
GLOSSOPETRAE tag/selector channel class from the JAILBREAK_LAB.md build order.

Supported schemes:

  ascii    VS17–VS256 encodes ASCII 0x20–0xEF as ord(vs) - 0xE0100 + 0x10.
           The scheme the captured payload uses (VS17 == ' ').
  utf8     VS17–VS256 encodes raw bytes 0x00–0xEF as ord(vs) - 0xE0100;
           the byte stream is then decoded as UTF-8 when it parses.
  zw       Zero-width binary channel: U+200B = 0, U+200C = 1, 8 bits per
           byte, MSB first, UTF-8 bytes (the parseltongue scheme).

The detector tries every scheme on every invisible run and reports the
candidates whose decoded text is mostly printable ASCII. The hidden text never
appears in a byte scan of the visible content, so grep and content filters
miss it — which is the point of the technique and the reason a decoder like
this belongs next to the refusal lexicon.

Usage:

  python tools/vs_decode.py <file|url|->            # decode
  python tools/vs_decode.py --encode "text" --scheme ascii   # round-trip aid
  python tools/vs_decode.py <url> --out report.md   # write a UTF-8 report
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

VS_START = 0xE0100          # VS17
VS_END = 0xE01EF            # VS256
VS_OFFSET = 0x10            # captured scheme: VS17 -> ' '
ZW_ZERO = "\u200b"
ZW_ONE = "\u200c"
ASCII_PRINTABLE = set(range(0x20, 0x7F)) | {0x0A, 0x0D, 0x09}


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    hits = sum(1 for ch in text if ord(ch) in ASCII_PRINTABLE)
    return hits / len(text)


def _selectors(text: str) -> list[tuple[int, int]]:
    """[(index, value 0..239)] for every VS-supplement char in text."""
    out = []
    for i, ch in enumerate(text):
        code = ord(ch)
        if VS_START <= code <= VS_END:
            out.append((i, code - VS_START))
    return out


def _zero_width_bits(text: str) -> list[tuple[int, str]]:
    return [(i, ch) for i, ch in enumerate(text) if ch in (ZW_ZERO, ZW_ONE)]


def decode_ascii(text: str) -> str:
    return "".join(chr(sel + VS_OFFSET) for _, sel in _selectors(text))


def decode_utf8_bytes(text: str) -> str:
    raw = bytes(sel for _, sel in _selectors(text))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Common single-byte encodings read fine; anything else stays bytes.
        try:
            return raw.decode("latin-1")
        except Exception:
            return raw.hex()


def decode_zero_width(text: str) -> str:
    bits = "".join("0" if ch == ZW_ZERO else "1" for _, ch in _zero_width_bits(text))
    raw = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", "replace")


SCHEMES = {
    "ascii": ("VS selectors, ASCII +0x10 (VS17 == ' ')", decode_ascii),
    "utf8":  ("VS selectors, raw bytes 0x00–0xEF", decode_utf8_bytes),
    "zw":    ("zero-width U+200B/200C bits", decode_zero_width),
}


def _group_runs(indices: list[int], gap: int = 2) -> list[tuple[int, int]]:
    """Group indices into runs, tolerating one visible char between marks
    (the emoji carrier the captured payload hides its text after)."""
    runs: list[tuple[int, int]] = []
    for idx in indices:
        if runs and idx - runs[-1][1] <= gap:
            runs[-1] = (runs[-1][0], idx)
        else:
            runs.append((idx, idx))
    return runs


def find_channels(text: str, min_ratio: float = 0.7) -> list[dict]:
    """Decode every invisible run under every scheme, keep readable hits."""
    channels: list[dict] = []

    for scheme, (_label, fn) in SCHEMES.items():
        if scheme == "zw":
            indices = [i for i, ch in enumerate(text) if ch in (ZW_ZERO, ZW_ONE)]
        else:
            indices = [i for i, ch in enumerate(text) if VS_START <= ord(ch) <= VS_END]
        for start, end in _group_runs(indices):
            if end - start < 3:
                continue
            decoded = fn(text[start:end + 1])
            ratio = _printable_ratio(decoded)
            if ratio >= min_ratio and decoded.strip():
                channels.append({
                    "start": start,
                    "end": end,
                    "length": end - start + 1,
                    "scheme": scheme,
                    "ratio": round(ratio, 3),
                    "text": decoded.strip(),
                })
    # One span can decode as printable under several schemes (both VS schemes
    # are byte-shifts of each other). Keep the most readable reading per span.
    best_by_span: dict[tuple[int, int], dict] = {}
    for channel in sorted(channels, key=lambda c: (-c["ratio"], c["start"])):
        key = (channel["start"], channel["end"])
        best_by_span.setdefault(key, channel)
    channels = sorted(best_by_span.values(), key=lambda c: c["start"])
    return channels


def encode(text: str, scheme: str = "ascii", carrier: str = "\U0001f389") -> str:
    """Encode hidden text for round-trip tests. Carrier emoji is decorative."""
    if scheme == "zw":
        bits = "".join(f"{b:08b}" for b in text.encode("utf-8"))
        return carrier + "".join(ZW_ZERO if b == "0" else ZW_ONE for b in bits)
    if scheme == "utf8":
        marks = "".join(chr(VS_START + b) for b in text.encode("utf-8")
                        if b <= (VS_END - VS_START))
        return carrier + marks
    offset = VS_OFFSET
    marks = "".join(chr(VS_START + (ord(ch) - offset)) for ch in text
                    if 0x00 <= (ord(ch) - offset) <= (VS_END - VS_START))
    return carrier + marks


def fetch_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    if source.lower().startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as resp:
            data = resp.read()
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "replace")
    return Path(source).read_text(encoding="utf-8-sig")


def format_report(source: str, text: str, channels: list[dict]) -> str:
    lines = [
        "# VS-stego decode report",
        "",
        f"- **Source:** {source}",
        f"- **Visible length:** {len(text)} chars",
        f"- **Hidden channels found:** {len(channels)}",
        "",
    ]
    for n, ch in enumerate(channels, 1):
        lines += [
            f"## Channel {n}",
            f"- scheme: {SCHEMES[ch['scheme']][0]}",
            f"- span: chars {ch['start']}–{ch['end']} ({ch['length']} marks)",
            f"- printable ratio: {ch['ratio']}",
            "",
            "```text",
            ch["text"][:20000],
            "```",
            "",
        ]
    if not channels:
        lines += ["No hidden channels above threshold.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", help="file path, http(s) URL, or - for stdin")
    parser.add_argument("--encode", help="encode TEXT into a channel and print it")
    parser.add_argument("--scheme", default="ascii", choices=sorted(SCHEMES))
    parser.add_argument("--min-ratio", type=float, default=0.7)
    parser.add_argument("--out", help="write the markdown report here (UTF-8)")
    args = parser.parse_args(argv)

    if args.encode:
        print(encode(args.encode, args.scheme))
        return 0
    if not args.source:
        parser.error("source is required unless --encode is given")

    text = fetch_source(args.source)
    channels = find_channels(text, min_ratio=args.min_ratio)
    report = format_report(args.source, text, channels)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"[vs_decode] report written: {args.out}")
    else:
        sys.stdout.buffer.write(report.encode("utf-8", "replace"))
        sys.stdout.buffer.write(b"\n")
    return 0 if channels or args.out else 1


if __name__ == "__main__":
    raise SystemExit(main())
