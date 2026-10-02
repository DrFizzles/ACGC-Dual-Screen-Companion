"""Spec decoding, validity gating and item-name lookup against synthetic images."""

import socket
import struct
import unittest

import support
import acmap
from emulink import EmuLinkClient, MemoryImageSource
from mock_server import MockEmuLinkServer, build_synthetic_image

SPEC = acmap.load_spec(support.FIXTURE_SPEC)


class SpecTest(unittest.TestCase):
    def test_fixture_is_complete(self):
        self.assertEqual(SPEC.missing_required(), [])
        self.assertEqual(SPEC.warnings, [])
        self.assertEqual(len(SPEC.charmap), 256)
        self.assertEqual(SPEC.bases["common_data"], 0x81266400)

    def test_parse_int(self):
        self.assertEqual(acmap.parse_int("0x2440"), 0x2440)
        self.assertEqual(acmap.parse_int("15"), 15)
        self.assertEqual(acmap.parse_int(7), 7)

    def test_decode_every_type(self):
        def field(typ, **kw):
            return acmap.Field.from_json({"key": "k", "offset": "0x0", "type": typ, **kw})

        self.assertEqual(SPEC.decode(field("u8"), b"\xff"), 255)
        self.assertEqual(SPEC.decode(field("s8"), b"\xff"), -1)
        self.assertEqual(SPEC.decode(field("u16"), b"\x12\x34"), 0x1234)
        self.assertEqual(SPEC.decode(field("s16"), b"\xff\xfe"), -2)
        self.assertEqual(SPEC.decode(field("u32"), b"\x00\x01\x2d\x42"), 77122)
        self.assertEqual(SPEC.decode(field("s32"), b"\xff\xff\xff\xf9"), -7)
        self.assertEqual(SPEC.decode(field("f32"), struct.pack(">f", 1.5)), 1.5)
        self.assertEqual(SPEC.decode(field("ptr"), b"\x81\x26\x64\x20"), 0x81266420)
        self.assertEqual(SPEC.decode(field("u16[]", count=3), b"\x00\x01\x00\x02\xe0\x01"), [1, 2, 0xE001])
        self.assertEqual(SPEC.decode(field("u8[]", count=2), b"\x05\x06"), [5, 6])
        self.assertEqual(SPEC.decode(field("u32[]", count=1), b"\x00\x00\x00\x09"), [9])
        # str: charmap decode, trailing spaces trimmed, 0x00 is a glyph (not a terminator)
        self.assertEqual(SPEC.decode(field("str", len=8), b"Doc     "), "Doc")
        self.assertEqual(SPEC.decode(field("str", len=4), b"A\x00B "), "A" + SPEC.charmap[0] + "B")
        # short/missing data decodes to None
        self.assertIsNone(SPEC.decode(field("u32"), b""))

    def test_mask_and_shift(self):
        f = acmap.Field.from_json({"key": "k", "offset": "0x0", "type": "u8", "mask": "0xF0", "shift": 4})
        self.assertEqual(SPEC.decode(f, b"\xa5"), 0xA)

    def test_bad_field_is_skipped_with_warning(self):
        obj = dict(SPEC.raw)
        obj["globals"] = list(SPEC.raw["globals"]) + [{"key": "bogus", "base": "common_data",
                                                       "offset": "0x0", "type": "u24"}]
        spec = acmap.parse_spec(obj)
        self.assertIsNone(spec.global_field("bogus"))
        self.assertTrue(any("bogus" in w for w in spec.warnings))

    def test_name_entry_address(self):
        r = SPEC.name_ranges[0]   # furniture: shift 2
        self.assertEqual(SPEC.name_entry(r.id_min + 8), (r.table_addr + 2 * 16, 16))
        self.assertIsNone(SPEC.name_entry(0x9ABC))


class DecodeTest(unittest.TestCase):
    """GameState from a synthetic in-town image, via dump source and via UDP."""

    @classmethod
    def setUpClass(cls):
        cls.image, cls.exp = build_synthetic_image(SPEC, "town")

    def check_state(self, state):
        exp = self.exp
        self.assertTrue(state["connected"])
        self.assertTrue(state["game"]["ok"])
        self.assertEqual(state["game"]["id"], "GAFE01")
        self.assertEqual(state["status"], acmap.ST_IN_TOWN, state["message"])
        self.assertTrue(state["in_gameplay"])
        self.assertEqual(state["player_index"], exp["player_index"])

        g = state["globals"]
        for key in acmap.REQUIRED["globals"]:
            self.assertIn(key, g)
            self.assertEqual(g[key], exp["globals"][key], key)
        self.assertEqual(state["labels"]["weather"], "Rain")
        self.assertEqual(state["labels"]["weather_intensity"], "Normal")
        self.assertEqual(state["labels"]["rtc_weekday"], "Thu")
        self.assertEqual(state["labels"]["scene_no"], "Outdoors")

        p = state["player"]
        ep = exp["player"]
        for key in ("name", "town_name", "wallet", "loan", "bank"):
            self.assertEqual(p[key], ep[key], key)
        self.assertEqual(p["exists"], 1)
        self.assertEqual(p["pockets"], exp["pockets"])
        self.assertEqual(p["item_conditions"], exp["conditions"])
        self.assertEqual(p["labels"]["gender"], "Girl")
        self.assertEqual([x["name"] for x in state["players"]], ["Ana", "Bo"])

        self.assertEqual([v["npc_id"] for v in state["villagers"]], exp["villagers"])
        self.assertEqual(state["villagers"][0]["npc_id_hex"], "0xE001")
        self.assertEqual(state["villagers"][0]["labels"]["cloth"], exp["item_names"][exp["villager_cloth"]])
        self.assertEqual(state["villagers"][0]["name"], exp["npc_name"])     # from npc_name_cache
        self.assertIsNone(state["villagers"][1]["name"])                     # not looked up yet
        self.assertEqual(state["labels"]["town_fruit"], exp["item_names"][exp["town_fruit"]])
        self.assertEqual(p["labels"]["equipment"], exp["item_names"][exp["player"]["equipment"]])

        pockets = state["pockets"]
        self.assertEqual(len(pockets), 15)
        names = exp["item_names"]
        for slot in pockets:
            item_id = slot["id"]
            if item_id == exp["empty_id"]:
                self.assertTrue(slot["empty"])
                self.assertIsNone(slot["name"])
            elif item_id == exp["unknown_id"]:
                self.assertFalse(slot["empty"])
                self.assertIsNone(slot["name"])
            else:
                self.assertEqual(slot["name"], names[item_id])
        self.assertEqual(pockets[1]["condition_label"], "Wrapped")
        self.assertEqual(pockets[3]["condition_label"], "Quest")
        self.assertEqual(pockets[0]["condition"], 0)
        self.assertEqual(state["warnings"], [])

    def test_decode_from_image(self):
        reader = acmap.ACReader(MemoryImageSource(self.image), SPEC)
        self.check_state(reader.poll())

    def test_decode_over_udp(self):
        with MockEmuLinkServer(self.image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5) as client:
            reader = acmap.ACReader(client, SPEC)
            first = reader.poll()
            self.check_state(first)
            self.assertLessEqual(first["round_trips"], 3)        # handshake + 2 batches
            second = reader.poll()
            self.check_state(second)
            self.assertEqual(second["round_trips"], 1)          # steady state: one batch
            self.assertEqual(server.stats["single"], 0)         # batch reads only
            self.assertEqual(server.stats["writes"], 0)

    def test_name_cache(self):
        src = MemoryImageSource(self.image, game_hash="hash-A")
        reader = acmap.ACReader(src, SPEC)
        reader.poll()
        cached = dict(reader.name_cache)
        for item_id, name in self.exp["item_names"].items():
            if item_id in self.exp["pockets"]:
                self.assertEqual(cached[item_id], name)
        self.assertNotIn(self.exp["unknown_id"], cached)
        self.assertEqual(reader.poll()["round_trips"], 1)       # names come from the cache

        # A different game hash must clear the cache and re-read the tables.
        src.game_hash = "hash-B"
        reader.handshake_interval = 0
        state = reader.poll()
        self.assertEqual(state["round_trips"], 3)               # handshake + batch + names
        self.check_state(state)

    def test_steady_state_is_one_udp_request(self):
        with MockEmuLinkServer(self.image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5) as client:
            reader = acmap.ACReader(client, SPEC)
            reader.poll()
            before = (client.transactions, server.stats["batch"])
            state = reader.poll()
            self.check_state(state)
            self.assertEqual(client.transactions - before[0], 1)
            self.assertEqual(server.stats["batch"] - before[1], 1)
            self.assertEqual(state["round_trips"], 1)
            self.assertEqual(client.reconnects, 0)

    def test_impossible_values_are_rejected(self):
        image = bytearray(self.image)

        def put(addr, data):
            off = addr & 0x01FFFFFF
            image[off:off + len(data)] = data

        put(SPEC.global_addr(SPEC.global_field("rtc_sec")), bytes([75]))
        put(SPEC.global_addr(SPEC.global_field("rtc_month")), bytes([13]))
        prices = [100, 65535, 87, 140, 2001, 75, 60]
        put(SPEC.global_addr(SPEC.global_field("kabu_prices")), struct.pack(">7H", *prices))
        p0 = SPEC.players.record_addr(SPEC.bases, 0)
        put(p0 + SPEC.players.field("wallet").offset, struct.pack(">I", 0xDEADBEEF))
        state = acmap.ACReader(MemoryImageSource(image), SPEC).poll()
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        g = state["globals"]
        self.assertIsNone(g["rtc_sec"])
        self.assertIsNone(g["rtc_month"])
        self.assertEqual(g["rtc_hour"], 13)
        self.assertEqual(g["kabu_prices"], [100, None, 87, 140, None, 75, 60])
        self.assertIsNone(state["player"]["wallet"])
        self.assertEqual(state["player"]["bank"], 67890)
        text = " ".join(state["warnings"])
        for key in ("rtc_sec", "rtc_month", "kabu_prices", "player.wallet"):
            self.assertIn(key, text)

    def test_values_that_change_between_reads(self):
        wallet_addr = SPEC.players.record_addr(SPEC.bases, 0) + SPEC.players.field("wallet").offset

        class Flaky(MemoryImageSource):
            """The wallet's bytes differ in the second copy of its span, ``flips`` times."""
            flips = 1

            def batch_read(self, requests):
                requests = list(requests)
                out = super().batch_read(requests)
                for k in range(len(requests) // 2, len(requests)):
                    addr, size = requests[k]
                    if self.flips and addr <= wallet_addr < addr + size:
                        off = wallet_addr - addr
                        data = bytearray(out[k])
                        data[off:off + 4] = bytes(b ^ 0xFF for b in data[off:off + 4])
                        out[k] = bytes(data)
                        self.flips -= 1
                return out

        src = Flaky(self.image)
        state = acmap.ACReader(src, SPEC).poll()                 # one flip: the re-read agrees
        self.assertEqual(state["player"]["wallet"], 12345)
        self.assertEqual(state["round_trips"], 4)                # handshake, batch, re-read, names
        self.assertEqual(state["warnings"], [])

        src = Flaky(self.image)
        src.flips = 2                                            # the re-read disagrees too
        state = acmap.ACReader(src, SPEC).poll()
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertIsNone(state["player"]["wallet"])
        self.assertTrue(any("players[0].wallet" in w for w in state["warnings"]))

    def test_name_table_check(self):
        image = bytearray(self.image)
        off = SPEC.names_check[0] & 0x01FFFFFF
        image[off:off + 4] = bytes(4)
        reader = acmap.ACReader(MemoryImageSource(image), SPEC)
        state = reader.poll()
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertEqual(reader.name_cache, {})
        self.assertTrue(all(p["name"] is None for p in state["pockets"]))
        self.assertTrue(any("runtime_check" in w for w in state["warnings"]))
        # Table found later: names are fetched then.
        image[off:off + 4] = struct.pack(">I", SPEC.names_check[1][0])
        self.check_state(reader.poll())

    def test_blank_name_entry_is_not_garbage(self):
        image = bytearray(self.image)
        item_id = self.exp["pockets"][0]
        addr, length = SPEC.name_entry(item_id)
        off = addr & 0x01FFFFFF
        image[off:off + length] = bytes(length)                  # 0x00 is a glyph, not a terminator
        state = acmap.ACReader(MemoryImageSource(image), SPEC).poll()
        self.assertIsNone(state["pockets"][0]["name"])

    def test_npc_name_cache_ignores_non_residents(self):
        image = bytearray(self.image)
        off = SPEC.npc_cache.addr & 0x01FFFFFF
        image[off:off + 2] = struct.pack(">H", 0xE0FF)            # a villager id, but not a resident
        reader = acmap.ACReader(MemoryImageSource(image), SPEC)
        state = reader.poll()
        self.assertEqual(reader.npc_names, {})
        self.assertIsNone(state["villagers"][0]["name"])


class ValidityTest(unittest.TestCase):
    def poll(self, scenario):
        image, exp = build_synthetic_image(SPEC, scenario)
        reader = acmap.ACReader(MemoryImageSource(image), SPEC)
        return reader, reader.poll(), exp

    def assert_hidden(self, state):
        self.assertFalse(state["in_gameplay"])
        self.assertEqual(state["globals"], {})
        self.assertIsNone(state["player"])
        self.assertEqual(state["pockets"], [])
        self.assertEqual(state["villagers"], [])

    def test_title_demo_scene_is_not_in_town(self):
        # Rule 5: the title demo runs play_main and fills now_private with a demo player.
        reader, state, exp = self.poll("title_demo")
        self.assertIn(exp["globals"]["scene_no"], SPEC.not_in_town_scenes)
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assertIn(SPEC.enum_label("scene_no", exp["globals"]["scene_no"]), state["message"])
        self.assert_hidden(state)
        self.assertEqual(reader.name_cache, {})
        self.assertEqual(reader.npc_names, {})

    def test_title_screen_is_not_in_town(self):
        reader, state, _ = self.poll("title")
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assert_hidden(state)
        self.assertEqual(reader.name_cache, {})                 # nothing cached outside gameplay
        self.assertEqual(reader.poll()["round_trips"], 1)       # and no name reads while waiting

    def test_null_game_pointer_is_not_in_town(self):
        _, state, _ = self.poll("null_game")
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assert_hidden(state)

    def test_wrong_game(self):
        _, state, _ = self.poll("wrong_game")
        self.assertEqual(state["status"], acmap.ST_WRONG_GAME)
        self.assertFalse(state["game"]["ok"])
        self.assertEqual(state["game"]["id"], "GAFP01")
        self.assert_hidden(state)

    def test_visiting_shows_globals_only(self):
        _, state, exp = self.poll("visiting")
        self.assertEqual(state["status"], acmap.ST_VISITING)
        self.assertTrue(state["in_gameplay"])
        self.assertIsNone(state["player_index"])
        self.assertIsNone(state["player"])
        self.assertEqual(state["pockets"], [])
        self.assertEqual(state["globals"]["town_name"], "Maple")
        self.assertEqual(state["globals"]["now_private"], exp["now_private"])

    def test_missing_player_record(self):
        _, state, _ = self.poll("no_player")
        self.assertEqual(state["status"], acmap.ST_NO_PLAYER)
        self.assertIn("Not in town", state["message"])
        self.assert_hidden(state)

    def test_exists_must_be_exactly_one(self):
        image, _ = build_synthetic_image(SPEC, "town")
        off = (SPEC.players.record_addr(SPEC.bases, 0) + SPEC.players.field("exists").offset) & 0x01FFFFFF
        image[off] = 0xFF
        state = acmap.ACReader(MemoryImageSource(image), SPEC).poll()
        self.assertEqual(state["status"], acmap.ST_NO_PLAYER)
        self.assert_hidden(state)

    def test_game_change_resets_speculation(self):
        # Steady state relies on the last GAME pointer; a new pointer costs one extra batch.
        image, _ = build_synthetic_image(SPEC, "town")
        src = MemoryImageSource(image)
        reader = acmap.ACReader(src, SPEC)
        reader.poll()
        self.assertEqual(reader.poll()["round_trips"], 1)
        addr = SPEC.bases["gamePT"] & 0x01FFFFFF
        image[addr:addr + 4] = struct.pack(">I", 0x81700200)      # new GAME, exec not set
        state = reader.poll()
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assertEqual(state["round_trips"], 2)               # batch + follow the new pointer

    def test_disconnected(self):
        tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tmp.bind(("127.0.0.1", 0))
        port = tmp.getsockname()[1]
        tmp.close()
        with EmuLinkClient("127.0.0.1", port, timeout=0.1, retries=0) as client:
            state = acmap.ACReader(client, SPEC).poll()
        self.assertFalse(state["connected"])
        self.assertEqual(state["status"], acmap.ST_DISCONNECTED)
        self.assert_hidden(state)


if __name__ == "__main__":
    unittest.main()
