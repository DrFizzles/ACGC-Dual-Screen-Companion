"""The client against the real shared spec (spec/ac_memory_map.json), when present.

The fixture spec only mirrors the schema; these tests catch differences that
only the real map has (rule 5 scenes, slot_enum, runtime_check, packet size).
"""

import struct
import unittest

import support
import acmap
import dashboard
from emulink import MAX_BATCH_ENTRIES, EmuLinkClient, MemoryImageSource
from mock_server import MockEmuLinkServer, build_synthetic_image

REAL = acmap.DEFAULT_SPEC_PATH


@unittest.skipUnless(REAL.exists(), f"real spec not found at {REAL}")
class RealSpecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = acmap.load_spec(REAL)
        cls.image, cls.exp = build_synthetic_image(cls.spec, "town")

    def put(self, image, addr, data):
        off = addr & 0x01FFFFFF
        image[off:off + len(data)] = data

    def poll_image(self, image):
        return acmap.ACReader(MemoryImageSource(image), self.spec).poll()

    def test_spec_parses_cleanly(self):
        spec = self.spec
        self.assertEqual(spec.missing_required(), [])
        self.assertEqual(spec.warnings, [])
        self.assertTrue(spec.not_in_town_scenes)
        self.assertIsNotNone(spec.names_check)
        self.assertIsNotNone(spec.npc_cache)
        self.assertEqual(spec.players.field("item_conditions").slot_enum, "item_condition")

    def test_in_town_over_udp_is_one_request(self):
        spec, exp = self.spec, self.exp
        with MockEmuLinkServer(self.image, port=0) as server, \
                EmuLinkClient("127.0.0.1", server.port, timeout=0.5) as client:
            reader = acmap.ACReader(client, spec)
            state = reader.poll()
            self.assertEqual(state["status"], acmap.ST_IN_TOWN, state["message"])
            before = (client.transactions, server.stats["batch"])
            state = reader.poll()
            self.assertEqual(client.transactions - before[0], 1)     # steady state: one UDP request
            self.assertEqual(server.stats["batch"] - before[1], 1)
            self.assertEqual(state["round_trips"], 1)
            self.assertEqual(server.stats["writes"], 0)

        self.assertEqual(state["status"], acmap.ST_IN_TOWN, state["message"])
        self.assertEqual(state["warnings"], [])
        g = state["globals"]
        self.assertEqual(g["town_name"], "Maple")
        self.assertEqual(g["kabu_prices"], exp["globals"]["kabu_prices"])
        self.assertEqual(state["player"]["name"], "Ana")
        self.assertEqual(state["player"]["wallet"], 12345)
        names = exp["item_names"]
        for slot in state["pockets"]:
            if slot["id"] in names:
                self.assertEqual(slot["name"], names[slot["id"]])
        conds = {p["slot"]: p["condition_label"] for p in state["pockets"]}
        self.assertEqual(conds[2], spec.enums["item_condition"][1])       # via slot_enum
        self.assertEqual(state["villagers"][0]["name"], exp["npc_name"])
        self.assertEqual(state["labels"]["town_fruit"], names[exp["town_fruit"]])

    def test_plan_fits_one_packet(self):
        spec = self.spec
        reader = acmap.ACReader(MemoryImageSource(self.image), spec)
        calls = []
        orig = reader.source.batch_read
        reader.source.batch_read = lambda reqs: calls.append(list(reqs)) or orig(calls[-1])
        reader.poll()
        reader.poll()
        steady = calls[-1]
        self.assertLessEqual(len(steady), MAX_BATCH_ENTRIES)
        self.assertLess(sum(2 + n for _, n in steady) + 4, 65000)

    def test_every_not_in_town_scene(self):
        f = self.spec.global_field("scene_no")
        for scene in sorted(self.spec.not_in_town_scenes):
            image = bytearray(self.image)
            self.put(image, self.spec.global_addr(f), struct.pack(">i", scene))
            state = self.poll_image(image)
            self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN, scene)
            self.assertEqual(state["globals"], {})

    def test_title_demo_scenario(self):
        image, _ = build_synthetic_image(self.spec, "title_demo")
        reader = acmap.ACReader(MemoryImageSource(image), self.spec)
        state = reader.poll()
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assertEqual(reader.name_cache, {})
        text = dashboard.render_text(state, self.spec)
        self.assertIn("Not in town", text)
        self.assertNotIn("Pockets", text)

    def test_exists_garbage_is_not_in_town(self):
        image = bytearray(self.image)
        p0 = self.spec.players.record_addr(self.spec.bases, 0)
        self.put(image, p0 + self.spec.players.field("exists").offset, b"\xff")
        state = self.poll_image(image)
        self.assertEqual(state["status"], acmap.ST_NO_PLAYER)
        self.assertEqual(state["globals"], {})

    def test_rule3_visiting_needs_foreigner_and_town_id(self):
        spec = self.spec
        image, exp = build_synthetic_image(spec, "visiting")
        state = self.poll_image(image)
        self.assertEqual(state["status"], acmap.ST_VISITING, state["message"])
        self.assertEqual(state["globals"]["town_id"], exp["globals"]["town_id"])
        # (c) town_id not a land id (common_data being rebuilt) -> not in town
        bad_town = bytearray(image)
        self.put(bad_town, spec.global_addr(spec.global_field("town_id")), b"\0\0")
        self.assertEqual(self.poll_image(bad_town)["status"], acmap.ST_NOT_IN_TOWN)
        # (c) pointer outside the players array but player_no is not 4 -> not in town
        not_foreign = bytearray(image)
        self.put(not_foreign, spec.global_addr(spec.global_field("player_no")), b"\x01")
        self.assertEqual(self.poll_image(not_foreign)["status"], acmap.ST_NOT_IN_TOWN)

    def test_rule3_rebuilding_is_not_in_town(self):
        image, _ = build_synthetic_image(self.spec, "rebuilding")
        state = self.poll_image(image)
        self.assertEqual(state["status"], acmap.ST_NOT_IN_TOWN)
        self.assertEqual(state["globals"], {})
        self.assertIn("not ready", state["message"])

    def test_rule10_residents_include_travelling(self):
        spec = self.spec
        image = bytearray(self.image)
        p1 = spec.players.record_addr(spec.bases, 1)
        self.put(image, p1 + spec.players.field("exists").offset, b"\0")   # Bo is away
        state = self.poll_image(image)
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertEqual([(p["index"], p["name"], p["travelling"]) for p in state["players"]],
                         [(0, "Ana", False), (1, "Bo", True)])

    def test_impossible_values(self):
        spec = self.spec
        image = bytearray(self.image)
        p0 = spec.players.record_addr(spec.bases, 0)
        self.put(image, p0 + spec.players.field("wallet").offset, struct.pack(">I", 0xDEADBEEF))
        self.put(image, spec.global_addr(spec.global_field("kabu_prices")), struct.pack(">7H", *[65535] * 7))
        state = self.poll_image(image)
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertIsNone(state["player"]["wallet"])
        self.assertEqual(state["globals"]["kabu_prices"], [None] * 7)
        self.assertTrue(state["warnings"])
        text = dashboard.render_text(state, spec)
        self.assertIn("wallet ?", text)
        turnips = next(line for line in text.splitlines() if line.startswith("Turnips"))
        self.assertNotIn("65535", turnips)
        self.assertIn("Sun ?", turnips)

    def test_runtime_check_failure_disables_names(self):
        image = bytearray(self.image)
        self.put(image, self.spec.names_check[0], bytes(64))
        reader = acmap.ACReader(MemoryImageSource(image), self.spec)
        state = reader.poll()
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertEqual(reader.name_cache, {})
        self.assertTrue(any("runtime_check" in w for w in state["warnings"]))

    def test_dashboard_text(self):
        state = self.poll_image(self.image)
        text = dashboard.render_text(state, self.spec)
        for needle in ("Maple", "Ana (player 1)", "Thursday", "wallet 12,345", "Mocky", "[Wrapped present]"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
