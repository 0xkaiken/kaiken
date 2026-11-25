#!/usr/bin/env python3
"""prompt_encoder — encode an objective into transport/obfuscation formats.

The gauntlet prompts call this as::

    python3 tools/prompt_encoder.py encode --text "terms" --format all

It is a thin CLI over ``parseltongue`` so the encodings the Blood Agents use
are the same ones the dashboard documents.  No network, no API keys.

Usage
-----
    prompt_encoder.py encode --text "payload" --format all
    prompt_encoder.py encode --text "payload" --format leetspeak,base64
    prompt_encoder.py encode --text "payload" --format all --json
    prompt_encoder.py decode --text "aGVsbG8=" --format base64
    prompt_encoder.py list
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _paths import pliny_home
    INSTALL_DIR = pliny_home()
except Exception:
    INSTALL_DIR = ROOT
sys.path.insert(0, str(INSTALL_DIR))

try:
    import parseltongue as pt
except Exception as exc:  # pragma: no cover - defensive
    print(f"error: cannot import parseltongue from {INSTALL_DIR}: {exc}",
          file=sys.stderr)
    sys.exit(2)

# The encodings this tool advertises in the red-team prompt: leetspeak,
# base64, ROT13, hex, homoglyph (unicode/cyrillic/greek), pig latin — plus the
# extended catalogue.
DEFAULT_FORMATS = [
    "leetspeak", "base64", "rot13", "hex", "unicode", "cyrillic", "greek",
    "piglatin", "binary", "morse", "fullwidth", "smallcaps", "fraktur",
    "doublestruck", "mixedcase", "atbash", "caesar", "reverse", "combo",
]

REMOVED_CHARS = set(" \n\r\t")


def _payload_for(text: str, fmt: str, intensity: str) -> str:
    """Return the encoded payload only (no wrapper prose)."""
    if fmt == "hex":
        return text.encode("utf-8").hex()
    if fmt == "base64":
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    if fmt == "rot13":
        import codecs
        return codecs.encode(text, "rot13")
    if fmt == "binary":
        return " ".join(f"{b:08b}" for b in text.encode("utf-8"))
    if fmt == "morse":
        # parseltongue wraps morse in an instruction; strip the prefix.
        out = pt.transform(text, "morse", intensity)
        return out.split("\n\n", 1)[-1]
    return pt.transform(text, fmt, intensity)


def cmd_encode(args) -> int:
    text = args.text
    if not text:
        print("error: --text is required", file=sys.stderr)
        return 2

    if args.format == "all":
        formats = DEFAULT_FORMATS
    else:
        formats = [f.strip() for f in args.format.split(",") if f.strip()]

    unknown = [f for f in formats if f not in pt.TECHNIQUES]
    if unknown:
        print(f"error: unknown format(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(sorted(pt.TECHNIQUES))}", file=sys.stderr)
        return 2

    results = {}
    for fmt in formats:
        try:
            results[fmt] = _payload_for(text, fmt, args.intensity)
        except Exception as exc:
            results[fmt] = f"[error: {type(exc).__name__}: {exc}]"

    if args.json:
        print(json.dumps({"input": text, "intensity": args.intensity,
                          "encodings": results}, indent=2, ensure_ascii=False))
        return 0

    print(f"# prompt_encoder — {len(results)} format(s), intensity={args.intensity}")
    print(f"# input: {text}")
    print()
    for fmt, value in results.items():
        print(f"## {fmt}")
        print(value)
        print()
    return 0


def cmd_decode(args) -> int:
    fmt = args.format
    text = args.text.strip()
    try:
        if fmt == "base64":
            out = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "replace")
        elif fmt == "hex":
            out = bytes.fromhex(text.replace(" ", "")).decode("utf-8", "replace")
        elif fmt == "rot13":
            import codecs
            out = codecs.decode(text, "rot13")
        elif fmt == "binary":
            bits = text.split()
            out = bytes(int(b, 2) for b in bits).decode("utf-8", "replace")
        elif fmt == "ascii85":
            out = base64.a85decode(text).decode("utf-8", "replace")
        elif fmt == "base32":
            out = base64.b32decode(text.upper() + "=" * (-len(text) % 8)).decode("utf-8", "replace")
        elif fmt == "uuencode":
            lines = [ln for ln in text.splitlines()
                     if ln and not ln.startswith("begin ") and not ln.startswith("end")
                     and ln != "`"]
            out = b"".join(binascii.a2b_uu(ln.encode()) for ln in lines).decode("utf-8", "replace")
        else:
            print(f"error: cannot decode {fmt!r} (supported: base64, hex, rot13, "
                  f"binary, ascii85, base32, uuencode)", file=sys.stderr)
            return 2
    except Exception as exc:
        print(f"error: decode failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0


def cmd_list(_args) -> int:
    cats = pt.list_techniques()
    for name, items in cats.items():
        print(f"{name}:")
        for item in items:
            print(f"  {item}")
    print(f"\ntotal: {len(pt.TECHNIQUES)} techniques")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prompt_encoder", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("encode", help="encode text into one or more formats")
    e.add_argument("--text", required=True)
    e.add_argument("--format", default="all",
                   help='"all" or a comma-separated list of technique names')
    e.add_argument("--intensity", default="medium", choices=["light", "medium", "heavy"])
    e.add_argument("--json", action="store_true", help="machine-readable output")
    e.set_defaults(func=cmd_encode)

    d = sub.add_parser("decode", help="decode a single format")
    d.add_argument("--text", required=True)
    d.add_argument("--format", required=True)
    d.set_defaults(func=cmd_decode)

    ls = sub.add_parser("list", help="list every available technique")
    ls.set_defaults(func=cmd_list)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
