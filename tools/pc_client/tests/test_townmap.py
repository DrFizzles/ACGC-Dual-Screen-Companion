"""Town map: placement rules, reader, rendering and dashboard output.

Everything runs against the synthetic map from mock_server (made-up acre
images, palettes and house icon written at the real spec's addresses).  No
game data is used.
"""

import contextlib
import copy
import io
import json
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
import acmap
import dashboard
import gctex
import townmap
from emulink import EmuLinkClient, MemoryImageSource
from mock_server import (GAME_STRUCT_ADDR, MockEmuLinkServer, build_synthetic_image,
                         SYN_IX_MARK, SYN_IX_OUTSIDE, SYN_IX_SEPARATOR)

REAL = acmap.DEFAULT_SPEC_PATH
SPEC = acmap.load_spec(REAL) if REAL.exists() else None
SKIP = unittest.skipUnless(SPEC is not None and SPEC.raw.get("map"), f"real spec with a map section not found at {REAL}")
P = acmap.parse_int

MARKER = (0xFF, 0x2D, 0x2D, 255)
HIGHLIGHT = (0xFF, 0x00, 0xE6, 255)


def put(image, addr, data):
    off = addr & 0x01FFFFFF
    image[off:off + len(data)] = data


class RecordingSource(MemoryImageSource):
    """MemoryImageSource that counts transactions and remembers every batch."""

    def __init__(self, image):
        super().__init__(image)
        self.transactions = 0
        self.batches = []
        self.flaky_addr = None    # flip a byte in the 2nd copy of spans holding this address

    def handshake(self):
        self.transactions += 1
        return super().handshake()

    def batch_read(self, requests):
        reqs = list(requests)
        self.transactions += 1
        self.batches.append(reqs)
        res = super().batch_read(reqs)
        n = len(reqs) // 2
        if self.flaky_addr is not None and n and reqs[:n] == reqs[n:]:
            for j in range(n, 2 * n):
                a, s = reqs[j]
                if a <= self.flaky_addr < a + s and res[j]:
                    b = bytearray(res[j])
                    b[self.flaky_addr - a] ^= 0x40
                    res[j] = bytes(b)
        return res


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #

class RulesTest(unittest.TestCase):
    def test_house_tier(self):
        # 3-step town: <100 -> 2, <220 -> 1, else 0
        self.assertEqual([townmap.house_tier(y, True) for y in (40, 99.9, 100, 160, 219.9, 220, 280)],
                         [2, 2, 1, 1, 1, 0, 0])
        # 2-step town: layer - 1, never below 0
        self.assertEqual([townmap.house_tier(y, False) for y in (40, 160, 280)], [1, 0, 0])

    def test_facing(self):
        for angle, (dx, dy), label in ((0, (0, 1), "S"), (0x4000, (1, 0), "E"), (0x8000, (0, -1), "N"),
                                       (0xC000, (-1, 0), "W"), (0x2000, (math.sqrt(.5), math.sqrt(.5)), "SE"),
                                       (0xE000, (-math.sqrt(.5), math.sqrt(.5)), "SW")):
            vx, vy = townmap.facing_vector(angle)
            self.assertAlmostEqual(vx, dx, places=6)
            self.assertAlmostEqual(vy, dy, places=6)
            self.assertEqual(townmap.facing_label(angle), label)
        self.assertEqual(townmap.facing_label(0x4000 + 0x0FFF), "E")    # rounds to the nearest 1/8 turn
        self.assertEqual(townmap.facing_label(0xFFFF), "S")

    @SKIP
    def test_player_place(self):
        m = townmap.load_map_spec(SPEC)
        p = townmap.player_place(m, 640.0, 640.0)        # map origin = block (1, 1) top-left
        self.assertEqual((p["block"], p["tile"], p["map"]), ((1, 1), (0, 0), (0.0, 0.0)))
        p = townmap.player_place(m, 2520.4, 1540.3)      # the spec's live example: B-3, east edge
        self.assertEqual(p["block"], (3, 2))
        self.assertEqual(m.acre_label(*p["block"]), "B-3")
        self.assertEqual(p["tile"], (15, 6))
        self.assertAlmostEqual(p["map"][0], (2520.4 / 640 - 1) * 22)
        self.assertAlmostEqual(p["map"][1], (1540.3 / 640 - 1) * 22)
        p = townmap.player_place(m, 3839.9, 4479.9)      # last tile of F-5
        self.assertEqual((p["block"], p["tile"]), ((5, 6), (15, 15)))
        self.assertEqual(townmap.player_place(m, 100.0, 5300.0)["block"], (0, 8))   # border / island

    @SKIP
    def test_house_slot(self):
        rule = townmap.load_map_spec(SPEC).slot_rule
        n = rule.terminator_index + 1

        def entry(name, slots):
            raw = bytearray(rule.entry_len)
            struct.pack_into(">H", raw, rule.fg_name_offset, name)
            for j, (ux, uz, idx) in enumerate(slots):
                o = rule.slots_offset + j * rule.slot_len
                raw[o + rule.f_ut_x], raw[o + rule.f_ut_z], raw[o + rule.f_idx] = ux, uz, idx
            return bytes(raw)

        filler = entry(0x0700, [(15, 15, 8)] * 3)
        hpl = bytearray(filler * n)
        hpl[0:rule.entry_len] = entry(0x0100, [(0, 0, 4), (1, 1, 5), (2, 2, 6)])
        hpl[3 * rule.entry_len:4 * rule.entry_len] = entry(0x0222, [(1, 1, 0), (5, 8, 7), (3, 3, 2)])
        hpl[4 * rule.entry_len:5 * rule.entry_len] = entry(0x0222, [(5, 8, 1)] * 3)       # later duplicate
        hpl[(n - 1) * rule.entry_len:] = entry(rule.terminator, [(0, 0, 0)] * 3)
        self.assertEqual(townmap.house_slot(bytes(hpl), 0x0222, 5, 9, rule), (7, True))  # ut_z - 1 == 8
        self.assertEqual(townmap.house_slot(bytes(hpl), 0x0222, 5, 8, rule), (0, False))  # no match: slot 0
        self.assertEqual(townmap.house_slot(bytes(hpl), 0x0999, 5, 9, rule), (4, False))  # no entry: entry 0
        # entries after the terminator are never used
        late = bytearray(hpl)
        late[(n - 1) * rule.entry_len:] = entry(0x0333, [(1, 1, 3)] * 3)
        late[(n - 2) * rule.entry_len:(n - 1) * rule.entry_len] = entry(rule.terminator, [(0, 0, 0)] * 3)
        self.assertEqual(townmap.house_slot(bytes(late), 0x0333, 1, 2, rule), (4, False))
        self.assertEqual(townmap.house_slot(b"", 0x0222, 5, 9, rule), (None, False))

    @SKIP
    def test_select_acres(self):
        m = townmap.load_map_spec(SPEC)
        types = [0] * m.blocks
        cells = []
        for bz in range(1, 7):
            for bx in range(1, 6):
                t = 20 + len(cells)
                types[m.block_index(bx, bz)] = t
                cells.append((t, bx, bz))
        self.assertEqual(townmap.select_acres(m, types), cells)
        # a border type is skipped: later acres move up one cell and the list is padded
        skipped = list(types)
        skipped[m.block_index(2, 1)] = min(m.skip_types)
        out = townmap.select_acres(m, skipped)
        self.assertEqual(out, cells[:1] + cells[2:] + [(m.pad_type, None, None)])
        # an unreadable type keeps its cell
        bad = list(types)
        bad[m.block_index(2, 1)] = m.type_count
        self.assertEqual(townmap.select_acres(m, bad)[1], (None, 2, 1))
        # the extra bridge replaces the type of its acre only
        pluss = bytearray([m.pluss["none"]] * m.pluss["count"])
        pluss[21] = 77
        out = townmap.select_acres(m, types, (2, 1, True), bytes(pluss))
        self.assertEqual(out[1], (77, 2, 1))
        self.assertEqual(townmap.select_acres(m, types, (2, 1, False), bytes(pluss))[1], (21, 2, 1))
        self.assertEqual(townmap.select_acres(m, types, (3, 1, True), bytes(pluss))[1], (21, 2, 1))


class MapSpecTest(unittest.TestCase):
    @SKIP
    def test_real_spec(self):
        m = townmap.load_map_spec(SPEC)
        self.assertEqual((m.cols, m.rows, m.units, m.block_cols), (5, 6, 22, 7))
        self.assertEqual((m.tex_format, m.tex_w, m.tex_h, m.tex_size), ("C4", 32, 32, 512))
        self.assertEqual(m.vis, (0, 0, 22, 22))
        self.assertEqual(m.icon_format, "IA4")
        self.assertEqual(m.acre_label(3, 2), "B-3")
        self.assertIsNone(m.acre_label(0, 2))
        self.assertEqual(len(m.indicators), 10)
        self.assertEqual([t.tier for t in m.tiers], [0, 1, 2])
        self.assertEqual(m.chain[-1].type, "ptr")
        self.assertTrue(m.tex_ptr_valid(m.tex_start + 3 * m.tex_size))
        self.assertFalse(m.tex_ptr_valid(m.tex_start + 3 * m.tex_size + 2))
        self.assertFalse(m.tex_ptr_valid(m.tex_end))

    def test_fixture_spec_has_no_map(self):
        spec = acmap.load_spec(support.FIXTURE_SPEC)
        self.assertIsNone(townmap.load_map_spec(spec))
        tm = townmap.TownMapReader(MemoryImageSource(bytes(64)), spec)
        st = tm.update({"connected": True})
        self.assertEqual(st["status"], townmap.MS_UNAVAILABLE)
        self.assertIn("no map section", st["message"])

    @SKIP
    def test_malformed_sections(self):
        for path, value in ((("grid",), None), (("grid", "cols"), "x"), (("acre_texture", "format"), "CMPR"),
                            (("acre_texture", "size"), 100),
                            (("buildings", "indicators", 0, "fallback_marker", "color"), "red")):
            raw = copy.deepcopy(SPEC.raw)
            obj = raw["map"]
            for k in path[:-1]:
                obj = obj[k]
            if value is None:
                del obj[path[-1]]
            else:
                obj[path[-1]] = value
            with self.assertRaises(townmap.MapSpecError, msg=str(path)):
                townmap.load_map_spec(acmap.parse_spec(raw))


# --------------------------------------------------------------------------- #
# Reader against the synthetic town
# --------------------------------------------------------------------------- #

@SKIP
class ReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pristine, exp = build_synthetic_image(SPEC, "town", with_map=True)
        cls.exp = exp
        cls.mexp = exp["map"]

    def setUp(self):
        self.image = bytearray(self.pristine)
        self.src = RecordingSource(self.image)
        self.reader = acmap.ACReader(self.src, SPEC)
        self.tm = townmap.TownMapReader(self.src, SPEC, scale=4)
        self.m = self.tm.map

    def poll(self, tm=None):
        st = self.reader.poll()
        return st, (tm or self.tm).update(st)

    # -- helpers ----------------------------------------------------------------

    def cell_pixel(self, canvas, n, tx, ty):
        """Pixel at the centre of texel (tx, ty) of map cell n."""
        x, y = self.tm.map_to_pixel((n % 5) * 22 + tx + 0.5, (n // 5) * 22 + ty + 0.5)
        return canvas.pixel(int(x), int(y))

    def colour(self, acre_type, index):
        return gctex.rgb5a3(self.mexp["palettes"][acre_type % 2][index])

    def cell_of(self, label):
        row, col = label.split("-")
        return "ABCDEF".index(row) * 5 + int(col) - 1

    # -- tests ------------------------------------------------------------------

    def test_full_map(self):
        st, ms = self.poll()
        self.assertEqual(st["status"], acmap.ST_IN_TOWN)
        self.assertEqual(ms["status"], townmap.MS_OK, ms)
        self.assertEqual(ms["warnings"], [])
        self.assertEqual(ms["layout"]["source"], "table")
        self.assertEqual(ms["layout"]["acre_types"], [t for t, _bx, _bz in self.mexp["cells"]])
        self.assertEqual(ms["layout"]["invalid_acres"], [])
        self.assertEqual({b["key"]: b["acre"] for b in ms["buildings"]}, self.mexp["buildings"])
        self.assertEqual([(h["slot"], h["acre"], h["spot"], h["tier"]) for h in ms["villager_houses"]],
                         [(h["slot"], h["acre"], h["spot"], h["tier"]) for h in self.mexp["houses"]])
        self.assertEqual([h["npc_id"] for h in ms["villager_houses"]],
                         [f"0x{v:04X}" for v in self.exp["villagers"]])
        exact = {h["slot"]: h["exact"] for h in self.tm._layout.houses}
        self.assertEqual(exact, {h["slot"]: h["exact"] for h in self.mexp["houses"]})
        p, ep = ms["player"], self.mexp["player"]
        self.assertTrue(p["visible"])
        self.assertEqual((p["acre"], tuple(p["block"]), tuple(p["tile"]), p["facing"], p["facing_dir"]),
                         (ep["acre"], ep["block"], ep["tile"], ep["facing"], "N"))
        self.assertAlmostEqual(p["map"][0], ep["map"][0], places=2)
        self.assertAlmostEqual(p["map"][1], ep["map"][1], places=2)
        self.assertEqual(ms["highlight"], {"acre": ep["acre"], "block": ep["block"], "source": "player"})
        json.dumps(ms)    # JSON-serialisable

    def test_render_pixels(self):
        self.poll()
        canvas = self.tm.render()
        self.assertEqual((canvas.width, canvas.height),
                         (self.tm.status["image"]["width"], self.tm.status["image"]["height"]))
        player_cell = self.cell_of(self.mexp["player"]["acre"])
        for n, (t, _bx, _bz) in enumerate(self.mexp["cells"]):
            self.assertEqual(self.cell_pixel(canvas, n, 6, 8), self.colour(t, 2 + t % 12), f"cell {n} body")
            self.assertEqual(self.cell_pixel(canvas, n, 3, 3), self.colour(t, SYN_IX_MARK), f"cell {n} mark")
            # the acre box covers the separators of the player's acre and of its right/lower neighbours
            if n not in (player_cell, player_cell + 5):
                self.assertEqual(self.cell_pixel(canvas, n, 10, 0), self.colour(t, SYN_IX_SEPARATOR))
            if n not in (player_cell, player_cell + 1):
                self.assertEqual(self.cell_pixel(canvas, n, 0, 10), self.colour(t, SYN_IX_SEPARATOR))
            self.assertEqual(self.cell_pixel(canvas, n, 21, 21), self.colour(t, 2 + t % 12), f"cell {n} edge")
        # the texels outside the 22x22 crop never show up
        outside = bytes(self.colour(0, SYN_IX_OUTSIDE))
        self.assertTrue(all(canvas.rgba[i:i + 4] != outside for i in range(0, len(canvas.rgba), 4)))
        # closing separator right of column 5
        x, y = self.tm.map_to_pixel(110 + 0.5, 10)
        self.assertEqual(canvas.pixel(int(x), int(y)), self.colour(0, SYN_IX_SEPARATOR))
        # villager houses: icon centre is ENV (I = 0), upper body PRIM of the house's tier
        for h in self.mexp["houses"]:
            prim, env = self.mexp["tiers"][h["tier"]]
            cx, cy = h["map"]
            x, y = self.tm.map_to_pixel(cx, cy)
            self.assertEqual(canvas.pixel(int(x), int(y)), (*env, 255), h)
            x, y = self.tm.map_to_pixel(cx - 5 + 7.5 * 10 / 16, cy - 5 + 2.5 * 10 / 16)
            self.assertEqual(canvas.pixel(int(x), int(y)), (*prim, 255), h)
            x, y = self.tm.map_to_pixel(cx - 4.9, cy - 4.9)            # transparent icon corner
            self.assertNotIn(canvas.pixel(int(x), int(y)), ((*prim, 255), (*env, 255)))
        # player: dot at the exact position, arrow towards north (up), box around the acre
        px, py = self.tm.map_to_pixel(*self.mexp["player"]["map"])
        self.assertEqual(canvas.pixel(int(px), int(py)), MARKER)
        self.assertEqual(canvas.pixel(int(px), int(py - 15)), MARKER)      # arrowhead
        self.assertNotEqual(canvas.pixel(int(px), int(py + 15)), MARKER)
        x, y = self.tm.map_to_pixel((player_cell % 5) * 22, (player_cell // 5) * 22)
        self.assertEqual(canvas.pixel(int(x) + 1, int(y) + 1), HIGHLIGHT)
        self.assertEqual(canvas.pixel(int(x) + 1, int(y) + 40), HIGHLIGHT)

    def test_steady_state_reads_only_the_player(self):
        self.poll()
        n0 = len(self.src.batches)
        st, ms = self.poll()
        tm_batches = self.src.batches[n0 + 1:]       # the first one is ACReader's
        self.assertEqual(ms["round_trips"], 1)
        self.assertEqual(len(tm_batches), 1)
        m = self.m
        total = sum(size for _a, size in tm_batches[0])
        self.assertLess(total, 300)
        for addr, size in tm_batches[0]:
            self.assertFalse(addr < m.tex_end and addr + size > m.tex_start, "acre images re-read")
            self.assertFalse(m.tex_ptr_addr <= addr < m.tex_ptr_addr + 4 * m.tex_ptr_count)
        self.assertTrue(ms["player"]["visible"])

    def test_reads_stay_in_spec_regions(self):
        """Only spec'd map tables, the player chain and the villager house heights are read
        (never the item/fg grid or anything else)."""
        m, cd = self.m, SPEC.bases["common_data"]
        allowed = [(m.tex_ptr_addr, 4 * m.tex_ptr_count), (m.pal_sel_addr, m.pal_sel_count),
                   (m.pal_ptr_addr, 4 * m.pal_ptr_count), (m.dct_addr, m.dct_entry_len * m.dct_count),
                   (m.pluss["addr"], m.pluss["count"]), (m.slot_rule.addr, m.slot_rule.entry_len * m.slot_rule.count),
                   (m.icon_addr, m.icon_size), (m.tex_start, m.tex_end - m.tex_start),
                   (m.type_ptr_addr, 4), (m.type_ptr_expected, m.type_table_count),
                   (m.combi_addr, 2 * m.combi_count), (m.bridge["addr"], m.bridge["size"]),
                   (m.kinds["ptr"], 4), (m.kinds["expected"], 4 * m.kinds["count"]),
                   (m.house_y["addr"], SPEC.villagers.count * m.house_y["stride"]),
                   (SPEC.bases["gamePT"], 4), (GAME_STRUCT_ADDR, 0x2000),
                   (self.mexp["actor_addr"], 0x100), (m.field_type["addr"], 1),
                   (m.indoor["next_scene"], 4), (m.indoor["exit"], 6)]
        allowed += [(t.dl_addr, 16) for t in m.tiers] + [(p, m.pal_size) for p in m.pal_expected]
        seen = []
        real = acmap.read_stable

        def spy(batch, reads):
            seen.extend(reads)
            return real(batch, reads)

        first, second = self.reader.poll(), self.reader.poll()     # ACReader's own reads are not spied on
        with mock.patch.object(acmap, "read_stable", spy):
            self.tm.update(first)
            self.tm.layout_interval = 0
            self.tm.update(second)
        self.assertTrue(seen)
        for tag, addr, size in seen:
            self.assertTrue(any(a <= addr and addr + size <= a + n for a, n in allowed),
                            f"{tag} reads 0x{addr:08X}+{size} outside the spec'd map data")
        # around common_data: only the combi table, bridge, house heights, field data and the
        # block type/kind tables; nothing else of the save (e.g. its item grid) is read
        cd_reads = sorted({a - cd for _t, a, _s in seen if cd <= a < cd + 0x30000})
        self.assertTrue(all(off in (m.combi_addr - cd, m.bridge["addr"] - cd, m.field_type["addr"] - cd,
                                    m.indoor["next_scene"] - cd, m.indoor["exit"] - cd,
                                    m.type_ptr_expected - cd, m.kinds["expected"] - cd)
                            or 0 <= off - (m.house_y["addr"] - cd) < SPEC.villagers.count * m.house_y["stride"]
                            for off in cd_reads), [hex(o) for o in cd_reads])

    def test_layout_refresh_reuses_textures(self):
        self.poll()
        base = self.tm._render_base()
        self.tm.layout_interval = 0
        n0 = len(self.src.batches)
        st, ms = self.poll()
        reads = [r for b in self.src.batches[n0 + 1:] for r in b]
        self.assertTrue(any(a == self.m.combi_addr for a, _s in reads))           # layout re-read
        self.assertFalse(any(a < self.m.tex_end and a + s > self.m.tex_start for a, s in reads))
        self.assertIs(self.tm._render_base(), base)                                # same content: cached image
        self.assertEqual(ms["status"], townmap.MS_OK)

    def test_type_pointer_mismatch_uses_the_save(self):
        put(self.image, self.m.type_ptr_addr, b"\0\0\0\0")
        st, ms = self.poll()
        self.assertEqual(ms["layout"]["source"], "save")
        self.assertEqual(ms["layout"]["acre_types"], [t for t, _bx, _bz in self.mexp["cells"]])
        self.assertTrue(any("taken from the save" in w for w in ms["warnings"]))

    def test_table_save_disagreement_keeps_previous_layout(self):
        _st, first = self.poll()
        put(self.image, self.m.type_ptr_expected + self.m.block_index(1, 1), bytes([self.mexp["cells"][1][0]]))
        self.tm.layout_interval = 0
        _st, ms = self.poll()
        self.assertEqual(ms["status"], townmap.MS_OK)
        self.assertTrue(any("disagrees with the save" in w for w in ms["warnings"]))
        self.assertEqual(ms["layout"]["acre_types"], first["layout"]["acre_types"])

    def test_invalid_texture_pointer_draws_placeholder(self):
        shop = next(i for i in self.m.indicators if i.key == "shop")
        acre = self.mexp["buildings"]["shop"]
        t_shop = self.mexp["cells"][self.cell_of(acre)][0]
        t_a1 = self.mexp["cells"][0][0]
        put(self.image, self.m.tex_ptr_addr + 4 * t_shop, struct.pack(">I", self.m.tex_start + 2))
        put(self.image, self.m.tex_ptr_addr + 4 * t_a1, struct.pack(">I", 0x80001000))
        st, ms = self.poll()
        self.assertEqual(sorted(ms["layout"]["invalid_acres"]), sorted([acre, "A-1"]))
        self.assertTrue(any("placeholders" in w for w in ms["warnings"]))
        canvas = self.tm.render()
        n = self.cell_of(acre)
        x, y = self.tm.map_to_pixel((n % 5) * 22 + 11, (n // 5) * 22 + 8)     # inside the 8-unit square
        self.assertEqual(canvas.pixel(int(x), int(y)), shop.fallback_color)
        self.assertEqual(self.cell_pixel(canvas, n, 2, 20), townmap.COL_FALLBACK_ACRE)
        self.assertEqual(self.cell_pixel(canvas, 0, 11, 11), townmap.COL_FALLBACK_ACRE)   # no building: no square
        # the label under the square is drawn in white
        x0, y0 = self.tm.map_to_pixel((n % 5) * 22, (n // 5) * 22 + 13)
        x1, y1 = self.tm.map_to_pixel((n % 5) * 22 + 22, (n // 5) * 22 + 17)
        white = sum(1 for yy in range(int(y0), int(y1)) for xx in range(int(x0), int(x1))
                    if canvas.pixel(xx, yy) == townmap.COL_TEXT)
        self.assertGreater(white, 10)

    def test_palette_pointer_mismatch(self):
        put(self.image, self.m.pal_ptr_addr + 4, struct.pack(">I", 0x80001234))
        st, ms = self.poll()
        odd = sorted(f"{'ABCDEF'[bz - 1]}-{bx}" for t, bx, bz in self.mexp["cells"] if t % 2)
        self.assertEqual(sorted(ms["layout"]["invalid_acres"]), odd)
        self.assertTrue(any("palette pointers" in w for w in ms["warnings"]))

    def test_skip_type_shifts_cells(self):
        i = self.m.block_index(2, 1)
        skip = min(self.m.skip_types)
        put(self.image, self.m.type_ptr_expected + i, bytes([skip]))
        put(self.image, self.m.combi_addr + 2 * i, struct.pack(">H", skip << 2))
        st, ms = self.poll()
        types = [t for t, _bx, _bz in self.mexp["cells"]]
        self.assertEqual(ms["layout"]["acre_types"], types[:1] + types[2:] + [self.m.pad_type])
        self.assertEqual(ms["layout"]["invalid_acres"], [])    # the pad acre has an image

    def test_bridge_rule(self):
        bx, bz = 2, 2
        n = self.cell_of("B-2")
        t = self.mexp["cells"][n][0]
        new = self.mexp["cells"][0][0]
        br = self.m.bridge
        put(self.image, br["addr"] + br["bx"], bytes([bx]))
        put(self.image, br["addr"] + br["bz"], bytes([bz]))
        put(self.image, br["addr"] + br["flags"], bytes([br["mask"]]))
        put(self.image, self.m.pluss["addr"] + t, bytes([new]))
        st, ms = self.poll()
        self.assertEqual(ms["layout"]["bridge"], [bx, bz, True])
        self.assertEqual(ms["layout"]["acre_types"][n], new)
        canvas = self.tm.render()
        self.assertEqual(self.cell_pixel(canvas, n, 6, 4), self.colour(new, 2 + new % 12))

    def test_not_in_gameplay(self):
        exec_addr = GAME_STRUCT_ADDR + 4
        play_main = SPEC.bases["play_main"]
        put(self.image, exec_addr, struct.pack(">I", 0x80600000))
        st, ms = self.poll()
        self.assertEqual(ms["status"], townmap.MS_NO_LAYOUT)
        self.assertIsNone(ms["player"])
        canvas = self.tm.render()                       # empty grid, still an image
        self.assertEqual(canvas.width, ms["image"]["width"])
        put(self.image, exec_addr, struct.pack(">I", play_main))
        st, ms = self.poll()
        self.assertEqual(ms["status"], townmap.MS_OK)
        put(self.image, exec_addr, struct.pack(">I", 0x80600000))
        st, ms = self.poll()
        self.assertEqual(ms["status"], townmap.MS_STALE)
        self.assertIsNone(ms["player"])
        self.assertIsNone(ms["highlight"])
        self.assertTrue(ms["buildings"])

    def test_indoors(self):
        scene = SPEC.global_field("scene_no")
        put(self.image, SPEC.global_addr(scene), struct.pack(">i", 20))
        st, ms = self.poll()
        self.assertEqual(st["status"], acmap.ST_IN_TOWN)
        self.assertFalse(ms["player"]["visible"])
        self.assertIn("not outdoors (scene 20", ms["player"]["reason"])
        self.assertIsNone(ms["highlight"])
        canvas = self.tm.render()
        px, py = self.tm.map_to_pixel(*self.mexp["player"]["map"])
        self.assertNotEqual(canvas.pixel(int(px), int(py)), MARKER)
        # the game's indoor rule: field_type != 0 and next_scene != 0 -> acre of the exit door
        m = self.m
        put(self.image, m.field_type["addr"], b"\x01")
        put(self.image, m.indoor["next_scene"], struct.pack(">i", 20))
        put(self.image, m.indoor["exit"], struct.pack(">3h", 2 * 640 + 300, 0, 4 * 640 + 100))
        st, ms = self.poll()
        self.assertFalse(ms["player"]["visible"])
        self.assertEqual(ms["highlight"], {"acre": "D-2", "block": (2, 4), "source": "exit_door"})
        x, y = self.tm.map_to_pixel(22, 66)
        self.assertEqual(self.tm.render().pixel(int(x) + 1, int(y) + 1), HIGHLIGHT)
        put(self.image, m.indoor["next_scene"], b"\0\0\0\0")
        st, ms = self.poll()
        self.assertIsNone(ms["highlight"])
        # field_type alone (scene 7) also hides the dot
        put(self.image, SPEC.global_addr(scene), struct.pack(">i", 7))
        st, ms = self.poll()
        self.assertIn("field_type", ms["player"]["reason"])

    def test_outside_the_grid(self):
        actor = self.mexp["actor_addr"]
        put(self.image, actor + P(SPEC.raw["map"]["player"]["position"]["z_offset"]), struct.pack(">f", 8 * 640 + 100))
        st, ms = self.poll()
        self.assertFalse(ms["player"]["visible"])
        self.assertIn("outside the town map", ms["player"]["reason"])
        self.assertIsNone(ms["highlight"])

    def test_actor_checks(self):
        actor = self.mexp["actor_addr"]
        image = bytearray(self.image)
        put(self.image, actor, struct.pack(">h", 5))                 # not the player profile
        st, ms = self.poll()
        self.assertFalse(ms["player"]["visible"])
        self.assertIn("actor check failed", ms["player"]["reason"])
        self.image[:] = image
        count = next(s for s in self.m.chain if s.min is not None)
        put(self.image, GAME_STRUCT_ADDR + count.offset, struct.pack(">i", 0))
        st, ms = self.poll()
        self.assertEqual(ms["player"]["reason"], "no player actor")

    def test_unstable_position_keeps_last_marker(self):
        st, first = self.poll()
        x_addr = self.mexp["actor_addr"] + P(SPEC.raw["map"]["player"]["position"]["x_offset"])
        self.src.flaky_addr = x_addr
        st, ms = self.poll()
        self.assertTrue(ms["player"]["visible"])
        self.assertTrue(ms["player"]["stale"])
        self.assertEqual(ms["player"]["map"], first["player"]["map"])
        fresh = townmap.TownMapReader(self.src, SPEC)
        st, ms = self.poll(fresh)
        self.assertFalse(ms["player"]["visible"])
        self.assertEqual(ms["player"]["reason"], "unstable read")

    def test_tier_colours_read_from_ram(self):
        t0 = self.m.tiers[0]
        put(self.image, t0.dl_addr + t0.prim_offset, bytes([10, 20, 30]))
        st, ms = self.poll()
        self.assertTrue(any("tier 0 colours" in w for w in ms["warnings"]))
        h = next(h for h in self.mexp["houses"] if h["tier"] == 0)
        cx, cy = h["map"]
        x, y = self.tm.map_to_pixel(cx - 5 + 7.5 * 10 / 16, cy - 5 + 2.5 * 10 / 16)
        self.assertEqual(self.tm.render().pixel(int(x), int(y)), (10, 20, 30, 255))

    def test_missing_icon_uses_squares(self):
        put(self.image, self.m.icon_addr, bytes(self.m.icon_size))
        st, ms = self.poll()
        self.assertTrue(any("house icon" in w for w in ms["warnings"]))
        canvas = self.tm.render()
        for h in self.mexp["houses"]:
            prim, _env = self.mexp["tiers"][h["tier"]]
            x, y = self.tm.map_to_pixel(*h["map"])
            self.assertEqual(canvas.pixel(int(x), int(y)), (*prim, 255))

    def test_write_png(self):
        self.poll()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.png")
            self.assertTrue(self.tm.write_png(path))
            self.assertFalse(self.tm.write_png(path))               # unchanged: not rewritten
            canvas = self.tm.render()
            w, h, rgba = gctex.read_png(Path(path).read_bytes())
            self.assertEqual((w, h, bytes(rgba)), (canvas.width, canvas.height, bytes(canvas.rgba)))
            actor = self.mexp["actor_addr"]
            put(self.image, actor + P(SPEC.raw["map"]["player"]["position"]["x_offset"]), struct.pack(">f", 1700.0))
            self.poll()
            self.assertTrue(self.tm.write_png(path))                # the player moved
            self.assertEqual(os.listdir(tmp), ["map.png"])          # no temp files left behind

    def test_scale(self):
        tm = townmap.TownMapReader(self.src, SPEC, scale=1)
        st, ms = self.poll(tm)
        canvas = tm.render()
        self.assertEqual((canvas.width, canvas.height), (126, 150))
        self.assertEqual((ms["image"]["width"], ms["image"]["height"], ms["image"]["scale"]), (126, 150, 1))
        t = self.mexp["cells"][7][0]
        x, y = tm.map_to_pixel(2 * 22 + 6, 22 + 8)
        self.assertEqual(canvas.pixel(int(x), int(y)), self.colour(t, 2 + t % 12))

    def test_udp_end_to_end(self):
        with MockEmuLinkServer(self.image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5) as client:
            reader = acmap.ACReader(client, SPEC)
            tm = townmap.TownMapReader(client, SPEC)
            ms = tm.update(reader.poll())
            self.assertEqual(ms["status"], townmap.MS_OK, ms)
            self.assertTrue(ms["player"]["visible"])
            before = client.transactions
            ms = tm.update(reader.poll())
            self.assertEqual(ms["round_trips"], 1)
            self.assertEqual(client.transactions - before, 2)       # ACReader 1 + map 1
            self.assertEqual(server.stats["writes"], 0)

    def test_spec_driven_addresses(self):
        """Move every relocatable table in a copy of the spec: the reader follows the spec."""
        raw = copy.deepcopy(SPEC.raw)
        mp = raw["map"]
        mp["acre_texture"]["pointer_table"]["addr"] = "0x81730000"
        start, size = 0x81740000, mp["acre_texture"]["size"]
        count = mp["acre_texture"]["valid_range"]["count"]
        mp["acre_texture"]["valid_range"].update(start=hex(start), end=hex(start + count * size))
        mp["acre_texture"]["palette"]["expected_pointers"] = ["0x81750000", "0x81750020"]
        mp["villager_houses"]["marker"]["addr"] = "0x81731000"
        mp["acre_types"]["from_save"]["data_combi_table"]["addr"] = "0x81760000"
        spec = acmap.parse_spec(raw)
        image, exp = build_synthetic_image(spec, "town", with_map=True)
        src = MemoryImageSource(image)
        tm = townmap.TownMapReader(src, spec)
        ms = tm.update(acmap.ACReader(src, spec).poll())
        self.assertEqual(ms["status"], townmap.MS_OK)
        self.assertEqual(ms["warnings"], [])
        self.assertEqual(ms["layout"]["acre_types"], [t for t, _bx, _bz in exp["map"]["cells"]])
        self.assertEqual(ms["layout"]["invalid_acres"], [])


@SKIP
class DashboardMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image, cls.exp = build_synthetic_image(SPEC, "town", with_map=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dump = os.path.join(cls.tmp.name, "mem1.raw")
        with open(cls.dump, "wb") as fh:
            fh.write(image)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dashboard.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_json_and_png(self):
        png = os.path.join(self.tmp.name, "town.png")
        code, out, err = self.run_main("--once", "--json", "--from-dump", self.dump, "--map", png)
        self.assertEqual(code, 0, err)
        state = json.loads(out)
        m = state["map"]
        self.assertEqual(m["status"], townmap.MS_OK)
        self.assertEqual(m["player"]["acre"], self.exp["map"]["player"]["acre"])
        self.assertEqual(m["player"]["tile"], list(self.exp["map"]["player"]["tile"]))
        self.assertTrue(m["player"]["visible"])
        self.assertEqual(Path(m["image"]["path"]), Path(png).resolve())
        w, h, _rgba = gctex.read_png(Path(png).read_bytes())
        self.assertEqual((w, h), (m["image"]["width"], m["image"]["height"]))

    def test_text_line(self):
        code, out, _err = self.run_main("--once", "--from-dump", self.dump)
        self.assertEqual(code, 0)
        self.assertIn("Map     you are in C-2 (tile 7,2) facing N", out)
        self.assertIn("[10 buildings, 3 houses]", out)

    def test_no_map(self):
        code, out, _err = self.run_main("--once", "--json", "--from-dump", self.dump, "--no-map")
        self.assertEqual(code, 0)
        self.assertNotIn("map", json.loads(out))

    def test_map_path_in_project_refused(self):
        """--map must not put decoded game art into the project: exit code 2, nothing written."""
        root = townmap.PROJECT_ROOT
        here = Path(__file__).resolve().parent
        name = "refused-map-test.png"
        candidates = [
            str(root),                                                     # the root itself
            str(root / name),
            str(here / name),
            str(root / "tools" / ".." / "android" / name),                 # ".." stays inside
            str(root / "tools" / "pc_client" / "new_dir" / name),          # folder does not exist yet
        ]
        try:
            candidates.append(os.path.relpath(here / name))                # relative to the cwd
        except ValueError:
            pass                                                           # cwd on another drive
        if os.name == "nt":
            candidates.append(str(root / name).swapcase())                 # case-insensitive file system
        for path in candidates:
            with self.subTest(path=path):
                code, out, err = self.run_main("--once", "--json", "--from-dump", self.dump, "--map", path)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("refusing to write a map decoded from the game into the project", err)
        for leftover in (root / name, here / name, root / "android" / name,
                         root / "tools" / "pc_client" / "new_dir"):
            self.assertFalse(leftover.exists(), leftover)

    def test_map_path_symlink_into_project_refused(self):
        link = Path(self.tmp.name) / "into-project"
        try:
            os.symlink(townmap.PROJECT_ROOT, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            try:
                import _winapi                       # Windows without symlink privilege: a junction
                _winapi.CreateJunction(str(townmap.PROJECT_ROOT), str(link))
            except (ImportError, AttributeError, OSError):
                self.skipTest(f"cannot create a directory link here: {exc}")
        try:
            code, _out, err = self.run_main("--once", "--from-dump", self.dump, "--map", str(link / "x.png"))
            self.assertEqual(code, 2)
            self.assertIn("refusing", err)
            self.assertFalse((townmap.PROJECT_ROOT / "x.png").exists())
        finally:
            try:
                os.unlink(link)
            except OSError:
                os.rmdir(link)                                             # Windows directory link

    def test_check_output_path(self):
        root = Path(self.tmp.name) / "proj"
        outside = Path(self.tmp.name) / "proj-sibling" / "m.png"     # shares a name prefix, not a parent
        self.assertEqual(townmap.check_output_path(outside, root=root), outside.resolve())
        for bad in (root, root / "m.png", root / "a" / "b" / ".." / "m.png"):
            with self.subTest(path=bad), self.assertRaises(townmap.ProjectPathError):
                townmap.check_output_path(bad, root=root)
        with self.assertRaises(townmap.ProjectPathError):
            townmap.check_output_path(Path(self.tmp.name) / "proj-sibling" / ".." / "proj" / "m.png", root=root)
        self.assertTrue(issubclass(townmap.ProjectPathError, ValueError))

    def test_reader_write_png_refuses_project_path(self):
        src = MemoryImageSource(self.dump)
        tm = townmap.TownMapReader(src, SPEC)
        tm.update(acmap.ACReader(src, SPEC).poll())
        target = townmap.PROJECT_ROOT / "tools" / "pc_client" / "refused-reader-test.png"
        with self.assertRaises(townmap.ProjectPathError):
            tm.write_png(target)
        self.assertFalse(target.exists())
        ok = Path(self.tmp.name) / "reader-ok.png"
        self.assertTrue(tm.write_png(ok))                    # outside the project: written
        self.assertTrue(ok.exists())

    def test_map_needs_a_map_section(self):
        code, _out, err = self.run_main("--once", "--from-dump", self.dump, "--spec", str(support.FIXTURE_SPEC),
                                        "--map", os.path.join(self.tmp.name, "x.png"))
        self.assertEqual(code, 2)
        self.assertIn("no map section", err)


if __name__ == "__main__":
    unittest.main()
