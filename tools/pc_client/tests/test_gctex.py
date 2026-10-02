"""GX texture decoder and PNG writer, against hand-built synthetic tiles.

Every expected pixel here is computed by hand from the GX layout rules, not by
re-encoding with code under test.  No game data is used.
"""

import struct
import unittest
import zlib

import support  # noqa: F401  (puts tools/pc_client on sys.path)
import gctex


def px(rgba: bytes, width: int, x: int, y: int) -> tuple:
    o = (y * width + x) * 4
    return tuple(rgba[o:o + 4])


class SizeTest(unittest.TestCase):
    def test_texture_size_pads_to_tiles(self):
        self.assertEqual(gctex.texture_size("C4", 32, 32), 512)
        self.assertEqual(gctex.texture_size("CI4", 32, 32), 512)       # alias
        self.assertEqual(gctex.texture_size("IA4", 16, 16), 256)
        self.assertEqual(gctex.texture_size("I4", 4, 4), 32)           # one 8x8 tile
        self.assertEqual(gctex.texture_size("I8", 9, 4), 64)           # two 8x4 tiles
        self.assertEqual(gctex.texture_size("RGBA8", 4, 4), 64)
        self.assertEqual(gctex.texture_size("RGB5A3", 5, 5), 4 * 32)   # 2x2 tiles of 4x4

    def test_bad_inputs(self):
        with self.assertRaises(gctex.TextureError):
            gctex.decode_texture(b"\0" * 32, "CMPR", 8, 8)
        with self.assertRaises(gctex.TextureError):
            gctex.decode_texture(b"\0" * 31, "I4", 8, 8)               # one byte short
        with self.assertRaises(gctex.TextureError):
            gctex.decode_texture(b"\0" * 32, "C4", 8, 8)               # palette missing
        with self.assertRaises(gctex.TextureError):
            gctex.decode_palette(b"\0" * 4, "RGBA8")


class ColourTest(unittest.TestCase):
    def test_rgb5a3(self):
        self.assertEqual(gctex.rgb5a3(0xFC00), (255, 0, 0, 255))         # opaque red
        self.assertEqual(gctex.rgb5a3(0x83E0), (0, 255, 0, 255))         # opaque green
        self.assertEqual(gctex.rgb5a3(0x801F), (0, 0, 255, 255))
        self.assertEqual(gctex.rgb5a3(0x8000 | (16 << 10) | (1 << 5) | 2), (132, 8, 16, 255))
        self.assertEqual(gctex.rgb5a3(0x0000), (0, 0, 0, 0))             # transparent
        self.assertEqual(gctex.rgb5a3(0x3F00), (255, 0, 0, 109))         # a=3 -> 96+12+1
        self.assertEqual(gctex.rgb5a3(0x7FFF), (255, 255, 255, 255))     # a=7 -> 255
        self.assertEqual(gctex.rgb5a3(0x1234), (34, 51, 68, 36))         # a=1 -> 32+4+0

    def test_rgb565(self):
        self.assertEqual(gctex.rgb565(0xF800), (255, 0, 0, 255))
        self.assertEqual(gctex.rgb565(0x07E0), (0, 255, 0, 255))
        self.assertEqual(gctex.rgb565(0x001F), (0, 0, 255, 255))
        self.assertEqual(gctex.rgb565(0x8410), (132, 130, 132, 255))     # r16 g32 b16

    def test_ia(self):
        self.assertEqual(gctex.ia8(0x80C0), (0xC0, 0xC0, 0xC0, 0x80))    # high byte = alpha
        self.assertEqual(gctex.ia4(0xA5), (85, 85, 85, 170))             # high nibble = alpha


class FormatTest(unittest.TestCase):
    def test_i4_single_tile(self):
        # 8x8, one tile: 8 rows of 4 bytes, high nibble = left pixel. Pixel value (x + y) % 16.
        data = bytes(((((2 * b) + y) % 16) << 4) | (((2 * b + 1) + y) % 16) for y in range(8) for b in range(4))
        out = gctex.decode_texture(data, "I4", 8, 8)
        for y in range(8):
            for x in range(8):
                v = ((x + y) % 16) * 17
                self.assertEqual(px(out, 8, x, y), (v, v, v, v), (x, y))

    def test_i4_tile_order(self):
        # 16x16 = 2x2 tiles, stored row-major: tile k is filled with value k + 1.
        data = b"".join(bytes([((k + 1) << 4) | (k + 1)]) * 32 for k in range(4))
        out = gctex.decode_texture(data, "I4", 16, 16)
        self.assertEqual(px(out, 16, 0, 0)[0], 1 * 17)      # tile 0: top-left
        self.assertEqual(px(out, 16, 15, 0)[0], 2 * 17)     # tile 1: top-right
        self.assertEqual(px(out, 16, 0, 15)[0], 3 * 17)     # tile 2: bottom-left
        self.assertEqual(px(out, 16, 15, 15)[0], 4 * 17)    # tile 3: bottom-right

    def test_i4_padding_is_skipped(self):
        # 4x4 inside one 8x8 tile: only the left 4 pixels of the first 4 rows are used.
        data = bytes([0x12, 0x34, 0xFF, 0xFF] * 4 + [0xEE] * 16)
        out = gctex.decode_texture(data, "I4", 4, 4)
        self.assertEqual(len(out), 4 * 4 * 4)
        self.assertEqual([px(out, 4, x, 2)[0] // 17 for x in range(4)], [1, 2, 3, 4])

    def test_i8_two_tiles_vertical(self):
        # 8x8 = two 8x4 tiles; tile 0 holds 0..31, tile 1 holds 100..131 (row-major inside).
        data = bytes(range(32)) + bytes(range(100, 132))
        out = gctex.decode_texture(data, "I8", 8, 8)
        self.assertEqual(px(out, 8, 3, 2), (19, 19, 19, 19))
        self.assertEqual(px(out, 8, 3, 6), (119, 119, 119, 119))

    def test_i8_two_tiles_horizontal(self):
        data = bytes(range(32)) + bytes(range(100, 132))
        out = gctex.decode_texture(data, "I8", 16, 4)
        self.assertEqual(px(out, 16, 7, 0)[0], 7)
        self.assertEqual(px(out, 16, 8, 0)[0], 100)
        self.assertEqual(px(out, 16, 9, 3)[0], 100 + 3 * 8 + 1)

    def test_ia4(self):
        # 8x4 tile, 1 byte per pixel: high nibble alpha, low nibble intensity.
        data = bytes([0xF0, 0x0F, 0xA5] + [0x00] * 29)
        out = gctex.decode_texture(data, "IA4", 8, 4)
        self.assertEqual(px(out, 8, 0, 0), (0, 0, 0, 255))
        self.assertEqual(px(out, 8, 1, 0), (255, 255, 255, 0))
        self.assertEqual(px(out, 8, 2, 0), (85, 85, 85, 170))

    def test_ia8_tiles(self):
        # 8x4 = two 4x4 tiles of (alpha, intensity) pairs.
        t0 = b"".join(bytes([0xFF, k * 10]) for k in range(16))
        t1 = b"".join(bytes([0x80, 200 + k]) for k in range(16))
        out = gctex.decode_texture(t0 + t1, "IA8", 8, 4)
        self.assertEqual(px(out, 8, 1, 2), (90, 90, 90, 255))         # k = 2*4 + 1
        self.assertEqual(px(out, 8, 5, 1), (205, 205, 205, 0x80))     # tile 1, k = 1*4 + 1

    def test_rgb565_tile(self):
        vals = [0xF800, 0x07E0, 0x001F, 0xFFFF] * 4
        out = gctex.decode_texture(struct.pack(">16H", *vals), "RGB565", 4, 4)
        self.assertEqual(px(out, 4, 0, 0), (255, 0, 0, 255))
        self.assertEqual(px(out, 4, 1, 3), (0, 255, 0, 255))
        self.assertEqual(px(out, 4, 3, 1), (255, 255, 255, 255))

    def test_rgb5a3_tile(self):
        vals = [0x0000, 0x3F00, 0xFC00, 0x801F] + [0x8000] * 12
        out = gctex.decode_texture(struct.pack(">16H", *vals), "RGB5A3", 4, 4)
        self.assertEqual(px(out, 4, 0, 0), (0, 0, 0, 0))
        self.assertEqual(px(out, 4, 1, 0), (255, 0, 0, 109))
        self.assertEqual(px(out, 4, 3, 0), (0, 0, 255, 255))
        self.assertEqual(px(out, 4, 2, 3), (0, 0, 0, 255))

    def test_rgba8_tile(self):
        # 64-byte tile: 16 (A, R) pairs, then 16 (G, B) pairs.
        ar = b"".join(bytes([k, 16 + k]) for k in range(16))
        gb = b"".join(bytes([32 + k, 48 + k]) for k in range(16))
        out = gctex.decode_texture(ar + gb, "RGBA8", 4, 4)
        self.assertEqual(px(out, 4, 0, 0), (16, 32, 48, 0))
        self.assertEqual(px(out, 4, 3, 2), (16 + 11, 32 + 11, 48 + 11, 11))

    def test_c4_with_each_tlut_format(self):
        # 8x8 tile of 4-bit indices: index = (x + 2*y) % 16.
        idx = [[(x + 2 * y) % 16 for x in range(8)] for y in range(8)]
        data = bytes((idx[y][2 * b] << 4) | idx[y][2 * b + 1] for y in range(8) for b in range(4))
        rgb5a3 = struct.pack(">16H", *[0x8000 | (k << 10) for k in range(16)])     # red ramp
        out = gctex.decode_texture(data, "C4", 8, 8, tlut=rgb5a3, tlut_format="RGB5A3")
        self.assertEqual(px(out, 8, 3, 1), ((5 << 3) | (5 >> 2), 0, 0, 255))     # index 5
        ia8 = struct.pack(">16H", *[(0xFF << 8) | (k * 16) for k in range(16)])
        out = gctex.decode_texture(data, "C4", 8, 8, tlut=ia8, tlut_format="IA8")
        self.assertEqual(px(out, 8, 7, 7), (5 * 16, 5 * 16, 5 * 16, 255))         # (7+14)%16 = 5
        rgb565 = struct.pack(">16H", *[k for k in range(16)])                     # blue ramp
        out = gctex.decode_texture(data, "C4", 8, 8, tlut=rgb565, tlut_format="RGB565")
        self.assertEqual(px(out, 8, 0, 1), (0, 0, (2 << 3) | (2 >> 2), 255))     # index 2

    def test_c4_tile_order_and_palette_list(self):
        # 16x8 = two 8x8 tiles: left tile index 1, right tile index 2.
        data = bytes([0x11]) * 32 + bytes([0x22]) * 32
        pal = [(0, 0, 0, 0), (10, 20, 30, 255), (40, 50, 60, 255)]
        out = gctex.decode_texture(data, "C4", 16, 8, palette=pal)
        self.assertEqual(px(out, 16, 7, 4), (10, 20, 30, 255))
        self.assertEqual(px(out, 16, 8, 4), (40, 50, 60, 255))

    def test_c8(self):
        # 8x4 tile of byte indices 0..31; IA8 palette entry k = (A 255, I 8k).
        pal = struct.pack(">32H", *[(0xFF << 8) | (8 * k) for k in range(32)])
        out = gctex.decode_texture(bytes(range(32)), "C8", 8, 4, tlut=pal, tlut_format="IA8")
        self.assertEqual(px(out, 8, 2, 3), (8 * 26, 8 * 26, 8 * 26, 255))

    def test_c8_index_past_palette_is_transparent(self):
        out = gctex.decode_texture(bytes([200] * 32), "C8", 8, 4, palette=[(1, 2, 3, 255)])
        self.assertEqual(px(out, 8, 0, 0), (0, 0, 0, 0))

    def test_c14x2(self):
        # 4x4 tile of BE16 indices; the top two bits are ignored.
        vals = [0xC000 | (k % 3) for k in range(16)]
        pal = [(1, 1, 1, 255), (2, 2, 2, 255), (3, 3, 3, 255)]
        out = gctex.decode_texture(struct.pack(">16H", *vals), "C14X2", 4, 4, palette=pal)
        self.assertEqual(px(out, 4, 1, 1), (3, 3, 3, 255))          # k = 5 -> index 2

    def test_crop(self):
        img = bytes(range(4 * 4 * 4))
        out = gctex.crop(img, 4, 1, 2, 2, 1)
        self.assertEqual(out, bytes(img[(2 * 4 + 1) * 4:(2 * 4 + 3) * 4]))
        with self.assertRaises(gctex.TextureError):
            gctex.crop(img, 4, 3, 3, 2, 2)


class PngTest(unittest.TestCase):
    def test_round_trip_and_chunks(self):
        w, h = 5, 3
        rgba = bytes(v for y in range(h) for x in range(w) for v in (x * 40, y * 80, 7, 255 - x))
        data = gctex.encode_png(w, h, rgba)
        self.assertEqual(data[:8], gctex.PNG_MAGIC)
        # every chunk's CRC is correct
        pos = 8
        kinds = []
        while pos < len(data):
            n = struct.unpack_from(">I", data, pos)[0]
            kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
            crc = struct.unpack_from(">I", data, pos + 8 + n)[0]
            self.assertEqual(crc, zlib.crc32(kind + payload) & 0xFFFFFFFF)
            kinds.append(kind)
            pos += 12 + n
        self.assertEqual(kinds, [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(gctex.read_png(data), (w, h, bytearray(rgba)))

    def test_size_mismatch(self):
        with self.assertRaises(ValueError):
            gctex.encode_png(2, 2, b"\0" * 15)


if __name__ == "__main__":
    unittest.main()
