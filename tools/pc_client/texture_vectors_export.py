"""Export GX texture decode vectors for the Android decoder (GcTextureTest.kt).

Every case is synthetic (hand-made tiles plus deterministic pseudo-random
bytes); gctex.py decodes it and the expected ARGB pixels are written next to
the input, so the Kotlin decoder (GcTexture.kt) is checked against the Python
one on identical data:

    <out>/texture_vectors.json

    python texture_vectors_export.py [--out DIR]

No game data is involved. tests/test_texture_vectors.py fails when the exported
file no longer matches what gctex.py produces (rerun this script).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gctex  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "test" / "resources" / "crosscheck"
FILE_NAME = "texture_vectors.json"
SIZES = [(8, 8), (12, 10), (16, 4), (5, 3)]


def lcg_bytes(seed: int, n: int) -> bytes:
    """Deterministic pseudo-random bytes (the same on every platform)."""
    out = bytearray()
    s = seed & 0xFFFFFFFF
    for _ in range(n):
        s = (s * 1103515245 + 12345) & 0xFFFFFFFF
        out.append((s >> 16) & 0xFF)
    return bytes(out)


def argb(c) -> str:
    r, g, b, a = c
    return f"{(a << 24) | (r << 16) | (g << 8) | b:08X}"


def make_case(name, fmt, w, h, data, offset=0, tlut_format=None, tlut=None, tlut_count=None) -> dict:
    """Decodes data[offset:] with gctex and records input + expected pixels."""
    case = {"name": name, "format": fmt, "width": w, "height": h, "offset": offset, "data": data.hex()}
    palette = None
    if tlut is not None:
        palette = gctex.decode_palette(tlut, tlut_format, tlut_count)
        case.update(tlut_format=tlut_format, tlut_count=tlut_count, tlut=tlut.hex(),
                    palette=[argb(c) for c in palette])
    pixels = gctex.decode_colors(data[offset:], fmt, w, h, palette)
    case["expected"] = [argb(c) for c in pixels]
    return case


def hand_cases() -> list[dict]:
    """Small tiles with known meaning (block order, nibble order, byte order)."""
    cases = []
    d = bytearray(64)                        # I4 16x8: two 8x8 blocks
    d[0], d[4], d[32] = 0x1F, 0x20, 0xF0
    cases.append(make_case("hand_I4_blocks", "I4", 16, 8, bytes(d)))
    d = bytearray(32)                        # IA4 8x4: high nibble alpha
    d[0], d[1], d[8] = 0xF0, 0x0F, 0x5A
    cases.append(make_case("hand_IA4", "IA4", 8, 4, bytes(d)))
    d = bytearray(128)                       # C4 16x16: block (1,0) at 32, (0,1) at 64
    d[0], d[32], d[64] = 0x12, 0x21, 0x20
    pal = bytes([0x00, 0x00, 0x88, 0x86, 0x91, 0x0C] + [0xFF, 0xFF] * 13)
    cases.append(make_case("hand_C4_blocks", "C4", 16, 16, bytes(d), tlut_format="RGB5A3", tlut=pal, tlut_count=16))
    d = bytes([0, 1, 200] + [0] * 29)        # C8 index past a short palette -> transparent
    cases.append(make_case("hand_C8_short_palette", "C8", 8, 4, d, tlut_format="IA8",
                           tlut=bytes([0xFF, 0x11, 0x80, 0x22]), tlut_count=2))
    d = bytearray(64)                        # RGBA8: AR pairs then GB pairs
    d[0], d[1], d[32], d[33] = 0x80, 0x11, 0x22, 0x33
    d[30], d[31], d[62], d[63] = 1, 2, 3, 4
    cases.append(make_case("hand_RGBA8", "RGBA8", 4, 4, bytes(d)))
    d = bytes([9]) + bytes([0x80, 0x1F] * 32)  # RGB5A3 5x3 padded to 2 blocks, 1-byte offset
    cases.append(make_case("hand_RGB5A3_padding", "RGB5A3", 5, 3, d, offset=1))
    d = bytes([0x40, 0x01, 0x00, 0x02] + [0] * 28)  # C14X2: index = BE16 & 0x3FFF
    cases.append(make_case("hand_C14X2", "C14X2", 4, 4, d, tlut_format="RGB565",
                           tlut=bytes([0xF8, 0x00, 0x07, 0xE0, 0x00, 0x1F]), tlut_count=3))
    return cases


def texture_vectors() -> dict:
    cases = hand_cases()
    seed = 1
    for fmt in gctex.FORMATS:
        paletted = fmt in gctex.PALETTE_FORMATS
        for w, h in SIZES:
            for tlut_format in (gctex.TLUT_FORMATS if paletted else (None,)):
                seed += 1
                offset = seed % 7
                size = gctex.texture_size(fmt, w, h)
                data = lcg_bytes(seed, offset + size + 3)
                if fmt == "C14X2":
                    # Keep most indices inside the palette; the top two bits test the 14-bit mask.
                    data = bytes(b & 0xC1 if i >= offset and (i - offset) % 2 == 0 else b
                                 for i, b in enumerate(data))
                name = f"{fmt}_{w}x{h}" + (f"_{tlut_format}" if tlut_format else "")
                if not paletted:
                    cases.append(make_case(name, fmt, w, h, data, offset))
                    continue
                # Short palettes check that missing entries decode as transparent.
                n = {"C4": 16, "C8": 256, "C14X2": 512}[fmt]
                if w == 5:
                    n = 9 if fmt == "C4" else 40
                tlut = lcg_bytes(seed + 1000, 2 * n)
                cases.append(make_case(name, fmt, w, h, data, offset, tlut_format, tlut, n))
    return {"schema": 1, "generator": "tools/pc_client/texture_vectors_export.py (gctex.py)",
            "note": "synthetic data only; pixels are ARGB hex (0xAARRGGBB)", "cases": cases}


def write(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILE_NAME
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(texture_vectors(), fh, ensure_ascii=True, indent=1)
        fh.write("\n")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    args = ap.parse_args(argv)
    path = write(Path(args.out))
    print(f"wrote {path} ({len(texture_vectors()['cases'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
