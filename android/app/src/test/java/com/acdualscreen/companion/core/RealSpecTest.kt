package com.acdualscreen.companion.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Cross-checks the shared spec (../spec/ac_memory_map.json, written separately) against this
 * client: it must parse cleanly, define every key the panel relies on, and decode the same
 * synthetic town image the fixture tests use. Skipped when the spec file is absent.
 */
class RealSpecTest {

    private fun specFile(): File? =
        listOf("../../spec/ac_memory_map.json", "../spec/ac_memory_map.json", "spec/ac_memory_map.json")
            .map(::File).firstOrNull { it.isFile }

    private fun load(): MemoryMap {
        val f = specFile()
        assumeTrue("shared spec not found", f != null)
        return MemoryMap.parse(f!!.readText())
    }

    @Test
    fun parsesWithoutWarnings() {
        val map = load()
        assertEquals(emptyList<String>(), map.warnings)
        assertEquals("GAFE01", map.game.id)
        assertEquals(256, map.charmap.size)
    }

    @Test
    fun definesRequiredKeys() {
        val map = load()
        val globals = listOf(
            "scene_no", "player_no", "now_private", "town_name", "rtc_sec", "rtc_min", "rtc_hour",
            "rtc_day", "rtc_weekday", "rtc_month", "rtc_year", "weather", "weather_intensity", "kabu_prices",
        )
        for (k in globals) assertNotNull("global $k", map.global(k))
        for (k in listOf("name", "town_name", "exists", "pockets", "item_conditions", "wallet", "loan", "bank")) {
            assertNotNull("player field $k", map.players!!.field(k))
        }
        assertNotNull(map.villagers!!.field("npc_id"))
        for (e in listOf("weather", "weekday", "scene_no")) assertTrue("enum $e", e in map.enums)
        assertTrue(map.itemNames!!.ranges.isNotEmpty())
    }

    @Test
    fun readPlanFitsOneBatch() {
        val d = Decoder(load())
        // Each entry is read twice (rule 8); both copies should still fit one 256-entry batch.
        assertTrue("plan has ${d.planSize} entries", 2 * d.planSize <= 256)
    }

    @Test
    fun decodesSyntheticTown() {
        val map = load()
        val s = Decoder(map).poll(DecoderTest.townImage())
        assertEquals(s.status, Phase.IN_TOWN, s.phase)
        assertEquals("Cheevo", s.town)
        assertEquals(1, s.playerIndex)
        assertEquals("Doc", s.playerName)
        assertEquals(77122L, s.wallet)
        assertEquals(1234567L, s.bank)
        assertEquals(39800L, s.loan)
        assertEquals(2026, s.clock?.year)
        assertEquals(13, s.clock?.hour)
        assertEquals(15, s.pockets.size)
        assertEquals(7, s.turnips.size)
        assertEquals(listOf(0xE001, 0xE00A, 0xE0FF), s.villagers.map { it.id })
        // The synthetic image has no real item tables, so the runtime check must refuse names.
        assertTrue(s.pockets.all { it.name == null })
    }
}
