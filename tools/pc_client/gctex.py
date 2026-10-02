"""GameCube (GX) texture decoding to RGBA8, and a minimal PNG writer/reader.

Standard library only.  Texture data is big-endian and stored in GX tiles:
the image is cut into fixed-size blocks, blocks are stored row-major across the
(padded) image, and pixels inside a block are stored row-major.

=========  ===========  ====  ==================================================
format     tile (w x h)  bpp  pixel
=========  ===========  ====  ==================================================
I4         8 x 8          4   intensity nibble (high nibble = left pixel)
I8         8 x 4          8   intensity byte
IA4        8 x 4          8   high nibble alpha, low nibble intensity
IA8        4 x 4         16   BE16: high byte alpha, low byte intensity
RGB565     4 x 4         16   BE16 r5 g6 b5, opaque
RGB5A3     4 x 4         16   BE16, see :func:`rgb5a3`
RGBA8      4 x 4         32   64-byte tile: 16 x (A, R) then 16 x (G, B)
C4 (CI4)   8 x 8          4   palette index nibble (high nibble = left pixel)
C8 (CI8)   8 x 4          8   palette index byte
C14X2      4 x 4         16   BE16 palette index (low 14 bits)
=========  ===========  ====  ==================================================

Intensity formats replicate I into R, G and B; I4/I8 also use I as alpha (what
the GX texture unit delivers).  Palettes (TLUTs) are arrays of BE16 entries in
IA8, RGB565 or RGB5A3.  CMPR is not supported.

Decoded images are plain ``bytearray`` RGBA rows (``width * height * 4``).
Nothing here knows about any game; callers pass bytes read from emulated RAM.
"""

from __future__ import annotations

import os
import struct
import tempfile
import zlib
from typing import Sequence

Color = tuple[int, int, int, int]

# format -> (tile width, tile height, bits per pixel)
FORMATS: dict[str, tuple[int, int, int]] = {
    "I4": (8, 8, 4),
    "I8": (8, 4, 8),
    "IA4": (8, 4, 8),
    "IA8": (4, 4, 16),
    "RGB565": (4, 4, 16),
    "RGB5A3": (4, 4, 16),
    "RGBA8": (4, 4, 32),
    "C4": (8, 8, 4),
    "C8": (8, 4, 8),
    "C14X2": (4, 4, 16),
}
ALIASES = {"CI4": "C4", "CI8": "C8", "CI14X2": "C14X2", "RGBA32": "RGBA8", "RGBA8888": "RGBA8"}
PALETTE_FORMATS = ("C4", "C8", "C14X2")
TLUT_FORMATS = ("IA8", "RGB565", "RGB5A3")
TRANSPARENT: Color = (0, 0, 0, 0)


class TextureError(ValueError):
    pass


def canonical_format(fmt: str) -> str:
    f = str(fmt).strip().upper()
    f = ALIASES.get(f, f)
    if f not in FORMATS:
        raise TextureError(f"unsupported texture format {fmt!r}")
    return f


def texture_size(fmt: str, width: int, height: int) -> int:
    """Bytes a ``width`` x ``height`` texture occupies (padded to whole tiles)."""
    bw, bh, bpp = FORMATS[canonical_format(fmt)]
    if width <= 0 or height <= 0:
        raise TextureError("texture size must be positive")
    tiles = -(-width // bw) * -(-height // bh)
    return tiles * bw * bh * bpp // 8


# --------------------------------------------------------------------------- #
# Colour conversions (all take the big-endian value as an int)
# --------------------------------------------------------------------------- #

def rgb5a3(v: int) -> Color:
    """Bit 15 set: opaque RGB555.  Clear: A3 RGB444."""
    if v & 0x8000:
        r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
        return (r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), 255
    a, r, g, b = (v >> 12) & 7, (v >> 8) & 15, (v >> 4) & 15, v & 15
    return r * 17, g * 17, b * 17, (a << 5) | (a << 2) | (a >> 1)


def rgb565(v: int) -> Color:
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), 255


def ia8(v: int) -> Color:
    a, i = (v >> 8) & 0xFF, v & 0xFF
    return i, i, i, a


def ia4(v: int) -> Color:
    a, i = ((v >> 4) & 15) * 17, (v & 15) * 17
    return i, i, i, a


_TLUT_DECODERS = {"IA8": ia8, "RGB565": rgb565, "RGB5A3": rgb5a3}


def decode_palette(data: bytes, fmt: str = "RGB5A3", entries: int | None = None) -> list[Color]:
    """Decode a TLUT of BE16 entries."""
    f = str(fmt).strip().upper()
    if f not in _TLUT_DECODERS:
        raise TextureError(f"unsupported palette format {fmt!r}")
    n = len(data) // 2 if entries is None else entries
    if n < 0 or len(data) < 2 * n:
        raise TextureError(f"palette needs {2 * n} bytes, got {len(data)}")
    conv = _TLUT_DECODERS[f]
    return [conv(v) for v in struct.unpack(f">{n}H", bytes(data[:2 * n]))]


# --------------------------------------------------------------------------- #
# Texture decoding
# --------------------------------------------------------------------------- #

def _tile_order(width: int, height: int, bw: int, bh: int):
    """Yield the destination (x, y) of every stored pixel, in storage order.
    Padding pixels outside the image yield None."""
    for ty in range(0, height, bh):
        for tx in range(0, width, bw):
            for y in range(ty, ty + bh):
                for x in range(tx, tx + bw):
                    yield (x, y) if x < width and y < height else None


def raw_values(data: bytes, fmt: str, width: int, height: int) -> list[int]:
    """Undo the tiling: one raw value per pixel, row-major (not for RGBA8)."""
    f = canonical_format(fmt)
    if f == "RGBA8":
        raise TextureError("RGBA8 has no single raw value per pixel")
    bw, bh, bpp = FORMATS[f]
    need = texture_size(f, width, height)
    if len(data) < need:
        raise TextureError(f"{f} {width}x{height} needs {need} bytes, got {len(data)}")
    data = bytes(data[:need])
    if bpp == 4:
        stream = []
        for b in data:
            stream.append(b >> 4)
            stream.append(b & 15)
    elif bpp == 8:
        stream = list(data)
    else:
        stream = list(struct.unpack(f">{need // 2}H", data))
    out = [0] * (width * height)
    for value, where in zip(stream, _tile_order(width, height, bw, bh)):
        if where is not None:
            out[where[1] * width + where[0]] = value
    return out


def _rgba8(data: bytes, width: int, height: int) -> list[Color]:
    need = texture_size("RGBA8", width, height)
    if len(data) < need:
        raise TextureError(f"RGBA8 {width}x{height} needs {need} bytes, got {len(data)}")
    out: list[Color] = [TRANSPARENT] * (width * height)
    pos = 0
    for ty in range(0, height, 4):
        for tx in range(0, width, 4):
            ar, gb = data[pos:pos + 32], data[pos + 32:pos + 64]
            pos += 64
            for k in range(16):
                x, y = tx + (k & 3), ty + (k >> 2)
                if x < width and y < height:
                    out[y * width + x] = (ar[2 * k + 1], gb[2 * k], gb[2 * k + 1], ar[2 * k])
    return out


def decode_colors(data: bytes, fmt: str, width: int, height: int,
                  palette: Sequence[Color] | None = None) -> list[Color]:
    """Decode to one (r, g, b, a) tuple per pixel, row-major.

    Palette formats need ``palette`` (a list from :func:`decode_palette`);
    an index past its end decodes as transparent black.
    """
    f = canonical_format(fmt)
    if f == "RGBA8":
        return _rgba8(data, width, height)
    values = raw_values(data, f, width, height)
    if f in PALETTE_FORMATS:
        if palette is None:
            raise TextureError(f"{f} needs a palette")
        pal = list(palette)
        mask = 0x3FFF if f == "C14X2" else 0xFFFF
        return [pal[v & mask] if (v & mask) < len(pal) else TRANSPARENT for v in values]
    if f == "I4":
        return [(v * 17,) * 4 for v in values]
    if f == "I8":
        return [(v,) * 4 for v in values]
    if f == "IA4":
        return [ia4(v) for v in values]
    conv = {"IA8": ia8, "RGB565": rgb565, "RGB5A3": rgb5a3}[f]
    return [conv(v) for v in values]


def decode_texture(data: bytes, fmt: str, width: int, height: int,
                   palette: Sequence[Color] | None = None,
                   tlut: bytes | None = None, tlut_format: str = "RGB5A3") -> bytearray:
    """Decode a GX texture to RGBA8 bytes (``width * height * 4``).

    For palette formats pass either ``palette`` (decoded colours) or ``tlut``
    (raw BE16 entries, decoded with ``tlut_format``).
    """
    if palette is None and tlut is not None:
        palette = decode_palette(tlut, tlut_format)
    out = bytearray()
    for c in decode_colors(data, fmt, width, height, palette):
        out += bytes(c)
    return out


def crop(rgba: bytes, width: int, x: int, y: int, w: int, h: int) -> bytearray:
    """Cut a ``w`` x ``h`` window out of an RGBA image ``width`` pixels wide."""
    out = bytearray()
    for row in range(y, y + h):
        start = (row * width + x) * 4
        out += rgba[start:start + w * 4]
    if len(out) != w * h * 4:
        raise TextureError("crop window outside the image")
    return out


# --------------------------------------------------------------------------- #
# PNG
# --------------------------------------------------------------------------- #

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rgba: bytes, level: int = 6) -> bytes:
    """8-bit RGBA PNG (colour type 6), filter 0 on every row."""
    stride = width * 4
    if len(rgba) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGBA, got {len(rgba)}")
    raw = bytearray()
    for y in range(height):
        raw += b"\x00"
        raw += rgba[y * stride:(y + 1) * stride]
    return (PNG_MAGIC
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _chunk(b"IEND", b""))


def write_png(path, width: int, height: int, rgba: bytes) -> None:
    """Write a PNG atomically (temp file + rename), so a viewer never sees half a file."""
    data = encode_png(width, height, rgba)
    path = os.fspath(path)
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".map-", suffix=".png", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_png(data: bytes) -> tuple[int, int, bytearray]:
    """Decode an 8-bit, non-interlaced RGB/RGBA PNG to ``(width, height, rgba)``.
    Enough for reading back what :func:`encode_png` writes (used by tests)."""
    if data[:8] != PNG_MAGIC:
        raise ValueError("not a PNG")
    pos, idat, header = 8, bytearray(), None
    while pos < len(data):
        n = struct.unpack_from(">I", data, pos)[0]
        kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG without IHDR")
    width, height, depth, ctype, _c, _f, interlace = header
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise ValueError("only 8-bit non-interlaced RGB/RGBA PNGs are supported")
    bpp = 4 if ctype == 6 else 3
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    out = bytearray()
    for y in range(height):
        ftype = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        prev = line
        if bpp == 4:
            out += line
        else:
            for x in range(width):
                out += line[3 * x:3 * x + 3] + b"\xff"
    return width, height, out
