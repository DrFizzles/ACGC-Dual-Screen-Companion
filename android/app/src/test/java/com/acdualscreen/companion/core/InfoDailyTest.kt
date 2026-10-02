package com.acdualscreen.companion.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The info-page fields (loan state, fortune, birthday, town fruit, held item) and the daily
 * tracker ([DailyReader]) against the shared spec, on the synthetic town image of [DecoderTest]
 * (player index 1, Thursday 1 October 2026). Skipped when the spec file is absent.
 */
class InfoDailyTest {

    private companion object {
        const val CD = 0x81266400L
        const val P = CD + 0x20 + 0x2440 // player index 1
        const val VILLAGERS = CD + 0x17438
        const val V_STRIDE = 0x988L
        const val MEM_OFF = 0x10L
        const val MEM_STRIDE = 0x138L
        const val HOMES = CD + 0x9CE8
        const val HOME_STRIDE = 0x26B0L
    }

    private fun load(): MemoryMap {
        val f = listOf("../../spec/ac_memory_map.json", "../spec/ac_memory_map.json", "spec/ac_memory_map.json")
            .map(::File).firstOrNull { it.isFile }
        assumeTrue("shared spec not found", f != null)
        return MemoryMap.parse(f!!.readText())
    }

    private fun poll(map: MemoryMap, m: FakeMemory): GameState = Decoder(map).poll(m)

    @Test
    fun loanStates() {
        val map = load()
        val m = DecoderTest.townImage()
        assertEquals(LoanState.PAYING, poll(map, m).loanState)

        // Loan 0, player 1's house is homes[2] (arrangement bits 2-3), untouched: no loan yet.
        m.u32(P + 0x90, 0).u8(CD + 0x2068A, 2 shl 2)
        assertEquals(LoanState.NONE, poll(map, m).loanState)
        // Grown house (size 1 in bits 7-5 of +0x2A): paid off.
        m.u8(HOMES + 2 * HOME_STRIDE + 0x2A, 1 shl 5)
        assertEquals(LoanState.PAID_OFF, poll(map, m).loanState)
        // An upgrade ordered (next_size, bits 4-2) also counts.
        m.u8(HOMES + 2 * HOME_STRIDE + 0x2A, 1 shl 2)
        assertEquals(LoanState.PAID_OFF, poll(map, m).loanState)
        // So does a basement (flags bit 4).
        m.u8(HOMES + 2 * HOME_STRIDE + 0x2A, 0).u8(HOMES + 2 * HOME_STRIDE + 0x24, 0x10)
        assertEquals(LoanState.PAID_OFF, poll(map, m).loanState)
        // Another home's growth does not count.
        m.u8(HOMES + 2 * HOME_STRIDE + 0x24, 0).u8(HOMES + 0 * HOME_STRIDE + 0x2A, 3 shl 5)
        assertEquals(LoanState.NONE, poll(map, m).loanState)
    }

    @Test
    fun fortuneBirthdayFruitHeldItem() {
        val map = load()
        val m = DecoderTest.townImage()
        var s = poll(map, m)
        assertNull(s.fortune)
        assertNull(s.birthday) // zeroed memory: month 0
        assertNull(s.fruit) // 0x2003 is not a fruit
        assertNotNull(s.heldItem)

        m.u8(P + 0x109D, 1).u8(P + 0x109F, 10).u16(P + 0x10A0, 2026).u8(P + 0x10A2, 4)
        m.u8(P + 0x10A6, 6).u8(P + 0x10A7, 14)
        m.u16(CD + 0x20688, 0x2803)
        s = poll(map, m)
        assertEquals("Money luck", s.fortune)
        assertEquals(6 to 14, s.birthday)
        assertEquals(Fruit.PEACH, s.fruit)

        m.u8(P + 0x109D, 30).u8(P + 0x109F, 9) // yesterday's fortune
        m.u8(P + 0x10A6, 0xFF).u8(P + 0x10A7, 0xFF)
        m.u16(P + 0x4A4, 0xFFF1) // an empty id? no: unknown id shows as hex
        s = poll(map, m)
        assertNull(s.fortune)
        assertNull(s.birthday)
    }

    @Test
    fun talkedToday() {
        val map = load()
        val m = DecoderTest.townImage()
        m.u16(P + 0x10, 0x1234).u16(P + 0x12, 0x3001)
        val me = m.slice(P, 0x14)!!
        // Slot 0: memory 3 is this player, last spoke today.
        val v0 = VILLAGERS + MEM_OFF + 3 * MEM_STRIDE
        m.raw(v0, me).u8(v0 + 0x17, 1).u8(v0 + 0x19, 10).u16(v0 + 0x1A, 2026)
        // Slot 2: this player, last spoke yesterday.
        val v2 = VILLAGERS + 2 * V_STRIDE + MEM_OFF
        m.raw(v2, me).u8(v2 + 0x17, 30).u8(v2 + 0x19, 9).u16(v2 + 0x1A, 2026)
        // Slot 14: today, but a different player's memory.
        val v14 = VILLAGERS + 14 * V_STRIDE + MEM_OFF
        m.text(v14, "Someone", 8).u8(v14 + 0x17, 1).u8(v14 + 0x19, 10).u16(v14 + 0x1A, 2026)

        val s = poll(map, m)
        val d = DailyReader(map).poll(m, s)
        assertEquals(mapOf(0 to true, 2 to false, 14 to false), d.talkedToday)
    }

    @Test
    fun fossilsAndGlowingSpot() {
        val map = load()
        val daily = map.daily!!
        val m = DecoderTest.townImage()
        fun unit(bx: Int, bz: Int, ux: Int, uz: Int) =
            daily.fgAddr + ((bz - 1) * daily.blocksX + (bx - 1)).toLong() * daily.blockBytes + (uz * 16 + ux) * 2L
        fun bury(bx: Int, bz: Int, ux: Int, uz: Int) {
            val b = (bz - 1) * daily.blocksX + (bx - 1)
            val a = daily.depositAddr + (b * 16 + uz) * 2L
            val row = Decoder.be16(m.slice(a, 2)!!)
            m.u16(a, row or (1 shl ux))
        }
        m.u16(unit(1, 1, 3, 4), daily.fossilItem); bury(1, 1, 3, 4)
        m.u16(unit(5, 6, 15, 15), daily.fossilItem); bury(5, 6, 15, 15)
        m.u16(unit(2, 3, 0, 0), daily.fossilItem) // lying on the ground, not buried
        m.u16(unit(2, 3, 1, 0), 0x1004); bury(2, 3, 1, 0) // something else buried

        // Player 1's glowing spot at block (3, 2), unit (5, 6), not dug yet.
        val pos = daily.shinePosAddr + 1 * daily.shinePosStride
        m.u8(pos, 3).u8(pos + 1, 2).u8(pos + 2, 5).u8(pos + 3, 6)
        m.u16(unit(3, 2, 5, 6), daily.shineSpotItem)

        val s = poll(map, m)
        val reader = DailyReader(map)
        var d = reader.poll(m, s, now = 0L)
        assertEquals(2, d.fossilsBuried)
        assertEquals(3, d.fossilsDug)
        assertEquals(ShineSpot.NOT_DUG, d.shineSpot)

        m.u16(unit(3, 2, 5, 6), daily.shineSpotItem + 1) // dug: a glowing hole
        d = reader.poll(m, s, now = 1L)
        assertEquals(ShineSpot.DUG, d.shineSpot)

        m.u32(pos, 0)
        assertEquals(ShineSpot.NONE_TODAY, reader.poll(m, s, now = 2L).shineSpot)
    }

    @Test
    fun villagerNamesComeFromACheckedLookup() {
        val map = load()
        val npc = map.npcNameCache!!
        val dma = npc.dmaAddr!!
        val m = DecoderTest.townImage() // town villagers: 0xE001, 0xE00A, 0xE0FF
        // Lookup of 0xE00A (name id 10): the buffer holds name ids 8..15, slot 2 = "Bob".
        val buffer = listOf("Hal", "Ivy", "Bob", "Kip", "Lu", "Mo", "Ned", "Oz")
        buffer.forEachIndexed { j, n -> m.text(dma + 8L * j, n, 8) }
        m.u16(npc.addr, 0xE00A).text(npc.addr + 2, "Bob", 8).u8(npc.addr + 0xA, 1)
        val d = Decoder(map)
        d.poll(m)
        var s = d.poll(m) // learned after two agreeing polls
        assertEquals("Bob", s.villagers.first { it.id == 0xE00A }.name)

        // Neighbours in the buffer are learned too (0xE001 is name id 1: buffer 0..7).
        val low = listOf("Amy", "Ann", "Cy", "Di", "Ed", "Flo", "Gus", "Hy")
        low.forEachIndexed { j, n -> m.text(dma + 8L * j, n, 8) }
        m.u16(npc.addr, 0xE003).text(npc.addr + 2, "Di", 8)
        d.poll(m)
        s = d.poll(m)
        assertEquals("Ann", s.villagers.first { it.id == 0xE001 }.name)

        // A cache that disagrees with the buffer (stale or torn) teaches nothing.
        val d2 = Decoder(map)
        m.u16(npc.addr, 0xE00A).text(npc.addr + 2, "Zed", 8)
        d2.poll(m)
        assertNull(d2.poll(m).villagers.first { it.id == 0xE00A }.name)

        // Nor does a special NPC's entry (npc_type 0) or a name with odd glyphs.
        val d3 = Decoder(map)
        buffer.forEachIndexed { j, n -> m.text(dma + 8L * (j), n, 8) }
        m.text(npc.addr + 2, "Bob", 8).u8(npc.addr + 0xA, 0)
        d3.poll(m)
        assertNull(d3.poll(m).villagers.first { it.id == 0xE00A }.name)
        m.u8(npc.addr + 0xA, 1).u8(npc.addr + 2, 0x01).u8(dma + 16, 0x01)
        d3.poll(m)
        assertNull(d3.poll(m).villagers.first { it.id == 0xE00A }.name)
    }

    @Test
    fun bundledNamesNameEveryVillagerWithoutALookup() {
        val f = listOf("src/main/assets/villager_names.json", "app/src/main/assets/villager_names.json")
            .map(::File).firstOrNull { it.isFile }
        assumeTrue("bundled names not found", f != null)
        val bundled = com.acdualscreen.companion.VillagerNameStore.parse(f!!.readText())
        assertEquals(236, bundled.size)
        assertTrue(bundled.keys.all { Decoder.isVillagerId(it) })
        assertTrue(bundled.values.all { it.isNotBlank() && it.length <= 8 })

        val map = load()
        val s = Decoder(map, bundled).poll(DecoderTest.townImage()) // name cache empty
        for (v in s.villagers.filter { (it.id and 0xFF) < bundled.size }) {
            assertEquals(bundled[v.id], v.name)
        }
    }
}
