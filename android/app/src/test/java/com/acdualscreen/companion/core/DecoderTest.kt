package com.acdualscreen.companion.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** Decoder tests against a synthetic memory image laid out per src/test/resources/spec_fixture.json. */
class DecoderTest {

    companion object {
        const val CD = 0x81266400L
        const val PLAYERS = CD + 0x20
        const val STRIDE = 0x2440L
        const val GAME_PT = 0x812F31B8L
        const val PLAY_MAIN = 0x8062B370L
        const val GAME_OBJ = 0x81300000L
        const val TABLE_FTR = 0x80700000L
        const val TABLE_ITEM = 0x80710000L
        const val PTR_CHECK = 0x80720000L
        const val NPC_CACHE = 0x8129A3E8L

        fun fixtureText(): String =
            DecoderTest::class.java.classLoader!!.getResource("spec_fixture.json")!!.readText()

        fun fixture(): MemoryMap = MemoryMap.parse(fixtureText())

        /** A memory image of player 2 (index 1) standing in town. */
        fun townImage(): FakeMemory {
            val m = FakeMemory()
            m.text(0x80000000L, "GAFE01", 6).u8(0x80000007L, 0)
            m.u32(GAME_PT, GAME_OBJ).u32(GAME_OBJ + 4, PLAY_MAIN)
            m.u32(CD + 0x14, 7) // scene_no
            m.u8(CD + 0x26003, 1) // player_no
            m.u32(CD + 0x2613C, PLAYERS + STRIDE) // now_private -> player index 1
            m.text(CD + 0x9120, "Cheevo", 8)
            // rtc: sec, min, hour, day, weekday, month, year
            m.u8(CD + 0x26120, 30).u8(CD + 0x26121, 5).u8(CD + 0x26122, 13).u8(CD + 0x26123, 1)
                .u8(CD + 0x26124, 4).u8(CD + 0x26125, 10).u16(CD + 0x26126, 2026)
            m.u16(CD + 0x2666C, 1).u16(CD + 0x2666E, 2) // Rain, Medium
            listOf(100, 90, 85, 120, 400, 150, 60).forEachIndexed { i, v -> m.u16(CD + 0x20480 + 2 * i, v) }
            m.u16(CD + 0x20688, 0x2003) // town_fruit (item id)
            m.u16(CD + 0x30000, 0xBEEF) // fixture_extra
            m.u8(0x80001000L, 5) // fixture_abs

            val p = PLAYERS + STRIDE
            m.text(p, "Doc", 8).text(p + 8, "Cheevo", 8).u8(p + 0x1086, 1)
            val pockets = intArrayOf(0x1004, 0x0000, 0x2003, 0x3000, 0x1005, 0x2003)
            pockets.forEachIndexed { i, id -> m.u16(p + 0x68 + 2 * i, id) }
            m.u32(p + 0x88, 0x21L) // slot 0 present, slot 2 quest
            m.u32(p + 0x8C, 77122).u32(p + 0x90, 39800).u32(p + 0x122C, 1234567)
            m.u16(p + 0x4A4, 0x1006) // equipment (item id)

            // Item-name tables (synthetic names, not game text).
            m.text(TABLE_FTR + 1 * 16, "Fake Chair", 16) // ids 0x1004..0x1007 (shift 2)
            m.text(TABLE_ITEM + 3 * 16, "Test Fruit", 16) // id 0x2003
            m.u32(PTR_CHECK, TABLE_FTR).u32(PTR_CHECK + 4, TABLE_ITEM) // runtime_check pointers

            val v = CD + 0x17438
            m.u16(v + 0 * 0x988, 0xE001).u16(v + 1 * 0x988, 0x0000).u16(v + 2 * 0x988, 0xE00A)
                .u16(v + 14 * 0x988, 0xE0FF).u16(v + 5 * 0x988, 0x1234)
            return m
        }
    }

    private lateinit var map: MemoryMap

    @Before
    fun setUp() {
        map = fixture()
    }

    // ------------------------------------------------------------ spec parsing

    @Test
    fun parsesFixture() {
        assertEquals(1, map.schema)
        assertEquals("GAFE01", map.game.id)
        assertEquals(0x80000007L, map.game.revisionAddr)
        assertEquals(CD, map.bases["common_data"])
        assertEquals(17, map.globals.size) // the u64 field is skipped
        assertTrue(map.warnings.any { "fixture_badtype" in it })
        assertEquals(256, map.charmap.size)
        assertEquals("A", map.charmap[0x41])
        assertEquals(" ", map.charmap[0x20])
        assertEquals(10, map.players!!.fields.size)
        assertEquals(setOf(15L, 16L), map.notInTownScenes)
        assertEquals(RuntimeCheck(PTR_CHECK, listOf(TABLE_FTR, TABLE_ITEM)), map.itemNames!!.check)
        assertEquals(NPC_CACHE, map.npcNameCache!!.addr)
        assertTrue(map.global("town_fruit")!!.itemId)
        assertEquals(STRIDE, map.players!!.stride)
        assertEquals(15, map.villagers!!.count)
        assertEquals(2, map.itemNames!!.ranges.size)
        assertEquals(setOf(0), map.itemNames!!.emptyIds)
        assertEquals("Rain", map.enumLabel("weather", 1))
        assertEquals(30, map.players!!.field("pockets")!!.byteSize)
        assertEquals(0x80001000L, map.globalAddr(map.global("fixture_abs")!!))
    }

    @Test
    fun parsesNumbers() {
        assertEquals(0x81266400L, MemoryMap.num("0x81266400"))
        assertEquals(255L, MemoryMap.num("0XfF"))
        assertEquals(42L, MemoryMap.num("42"))
        assertEquals(-16L, MemoryMap.num("-0x10"))
        assertEquals(7L, MemoryMap.num(7))
    }

    @Test
    fun rejectsUnusableSpecs() {
        for (bad in listOf(
            "not json",
            fixtureText().replace("\"schema\": 1", "\"schema\": 2"),
            fixtureText().replace("\"gamePT\"", "\"gamePointer\""),
            """{"schema":1,"game":{"id":"GAFE01","id_addr":"0x80000000","revision_addr":"0x80000007"},
               "bases":{"common_data":"0x81266400","gamePT":"0x812F31B8","play_main":"0x8062B370"}}""",
        )) {
            try {
                MemoryMap.parse(bad)
                fail("accepted: ${bad.take(40)}")
            } catch (e: SpecException) {
                // expected
            }
        }
    }

    // ------------------------------------------------------------ decoding

    @Test
    fun decodesTownState() {
        val mem = townImage()
        val d = Decoder(map)
        d.onHello("GAFE01", "hash1")
        val s = d.poll(mem)

        assertEquals(Phase.IN_TOWN, s.phase)
        assertEquals("In town", s.status)
        assertEquals("GAFE01", s.gameId)
        assertEquals("Cheevo", s.town)
        assertEquals("Test scene B", s.scene)
        assertEquals(1, s.playerNo)
        assertEquals(1, s.playerIndex)
        assertFalse(s.visiting)
        assertEquals("Doc", s.playerName)
        assertEquals("Cheevo", s.playerTown)
        assertEquals(Clock(2026, 10, 1, 4, "Thursday", 13, 5, "October"), s.clock)
        assertEquals("Rain (Medium)", s.weather)
        assertEquals(77122L, s.wallet)
        assertEquals(39800L, s.loan)
        assertEquals(1234567L, s.bank)

        assertEquals(15, s.pockets.size)
        val p = s.pockets
        assertEquals(Pocket(0, 0x1004, "Fake Chair", false, Pocket.COND_PRESENT), p[0])
        assertEquals(Pocket(1, 0, null, true, Pocket.COND_NORMAL), p[1])
        assertEquals(Pocket(2, 0x2003, "Test Fruit", false, Pocket.COND_QUEST), p[2])
        assertEquals(Pocket(3, 0x3000, null, false, 0), p[3]) // no range -> shown as hex
        assertEquals("Fake Chair", p[4].name) // 0x1005 >> 2 shares 0x1004's entry
        assertEquals("Test Fruit", p[5].name)
        assertTrue(p.drop(6).all { it.empty })

        assertEquals(listOf(100, 90, 85, 120, 400, 150, 60), s.turnips.map { it.price })
        assertEquals("Sunday", s.turnips[0].label)
        assertEquals(listOf(4), s.turnips.indices.filter { s.turnips[it].today })

        assertEquals(listOf(Villager(0, 0xE001, null), Villager(2, 0xE00A, null), Villager(14, 0xE0FF, null)), s.villagers)
        assertEquals(
            listOf("town fruit" to "Test Fruit", "equipment" to "Fake Chair", "fixture extra" to "48879", "fixture abs" to "5"),
            s.extras,
        )
    }

    @Test
    fun readsInFewBatches() {
        val mem = townImage()
        val d = Decoder(map)
        d.poll(mem)
        // fixed addresses (each read twice, rule 8), GAME re-check, item names (first time only)
        assertEquals(3, mem.batches.size)
        assertEquals(2 * d.planSize, mem.batches[0].size)
        assertEquals(mem.batches[0].take(d.planSize), mem.batches[0].drop(d.planSize))
        assertTrue(mem.batches.all { b -> b.all { it.size in 1..4096 } })
        assertEquals(listOf(ReadReq(GAME_PT, 4), ReadReq(GAME_OBJ + 4, 4)), mem.batches[1])
        mem.batches.clear()
        d.poll(mem)
        assertEquals(2, mem.batches.size)
    }

    @Test
    fun itemNameCacheClearsOnNewGameHash() {
        val mem = townImage()
        val d = Decoder(map)
        d.onHello("GAFE01", "hash1")
        assertEquals("Fake Chair", d.poll(mem).pockets[0].name)

        mem.text(TABLE_FTR + 16, "Other Name", 16)
        assertEquals("Fake Chair", d.poll(mem).pockets[0].name) // cached
        d.onHello("GAFE01", "hash1")
        assertEquals("Fake Chair", d.poll(mem).pockets[0].name) // same image, cache kept
        d.onHello("GAFE01", "hash2")
        assertEquals(0, d.cachedNameCount)
        assertEquals("Other Name", d.poll(mem).pockets[0].name)
    }

    @Test
    fun notInTownWhenGamePointerIsNull() {
        val mem = townImage().u32(GAME_PT, 0)
        val s = Decoder(map).poll(mem)
        assertEquals(Phase.NOT_IN_TOWN, s.phase)
        assertEquals("Test scene B", s.scene)
        assertNull(s.town)
        assertEquals(1, mem.batches.size) // no follow-up reads
    }

    @Test
    fun notInTownWhenExecIsNotPlayMain() {
        val mem = townImage().u32(GAME_OBJ + 4, 0x80012345L)
        val s = Decoder(map).poll(mem)
        assertEquals(Phase.NOT_IN_TOWN, s.phase)
        assertTrue(s.pockets.isEmpty())
    }

    @Test
    fun notInTownWhenPointerChangesBetweenReads() {
        val mem = townImage()
        // Wrap the reader so the GAME pointer moves after the first batch (a scene change).
        val racing = MemoryReader { reqs ->
            val out = mem.read(reqs)
            mem.u32(GAME_PT, GAME_OBJ + 0x100)
            out
        }
        assertEquals(Phase.NOT_IN_TOWN, Decoder(map).poll(racing).phase)
    }

    @Test
    fun wrongGameAndRevision() {
        val other = townImage().text(0x80000000L, "GALE01", 6)
        val s = Decoder(map).poll(other)
        assertEquals(Phase.WRONG_GAME, s.phase)
        assertEquals("GALE01", s.gameId)

        val rev1 = townImage().u8(0x80000007L, 1)
        assertEquals(Phase.WRONG_GAME, Decoder(map).poll(rev1).phase)
    }

    @Test
    fun noGameMemoryWhenRangeInvalid() {
        val nothing = MemoryReader { reqs -> reqs.map { null } }
        assertEquals(Phase.WRONG_GAME, Decoder(map).poll(nothing).phase)
    }

    @Test
    fun visitingPlayerShowsGlobalsOnly() {
        val mem = townImage().u32(CD + 0x2613C, 0x81000000L).u8(CD + 0x26003, 4) // player_no = foreigner
        val s = Decoder(map).poll(mem)
        assertEquals(Phase.IN_TOWN, s.phase)
        assertTrue(s.visiting)
        assertNull(s.playerIndex)
        assertNull(s.wallet)
        assertTrue(s.pockets.isEmpty())
        assertEquals("Cheevo", s.town)
        assertEquals(7, s.turnips.size)
    }

    @Test
    fun pointerOutsidePlayersIsNotInTownUnlessForeigner() {
        // Rule 3(c): player_no is a local slot (1), so a pointer outside the array is not a visitor.
        val s = Decoder(map).poll(townImage().u32(CD + 0x2613C, 0x81000000L))
        assertEquals(Phase.NOT_IN_TOWN, s.phase)
        assertNull(s.town)
        // NULL now_private (common_data being rebuilt) is not a visitor even with player_no 4.
        val rebuilding = townImage().u32(CD + 0x2613C, 0).u8(CD + 0x26003, 4)
        assertEquals(Phase.NOT_IN_TOWN, Decoder(map).poll(rebuilding).phase)
    }

    @Test
    fun tornReadsAreRereadOrDropped() {
        val p = PLAYERS + STRIDE
        // The wallet changes between the two copies once, then settles: the re-read is used.
        var flips = 1
        val mem = townImage()
        val settling = MemoryReader { reqs ->
            val out = mem.read(reqs).toMutableList()
            val i = reqs.indexOfLast { it.addr == p + 0x8C }
            if (flips-- > 0 && i >= 0) out[i] = byteArrayOf(0, 0, 0, 1)
            out
        }
        val d = Decoder(map)
        assertEquals(77122L, d.poll(settling).wallet)
        assertEquals(0, d.unstableReads)
        // A value that never agrees with itself is shown as unknown.
        var n = 0
        val torn = MemoryReader { reqs ->
            val out = mem.read(reqs).toMutableList()
            reqs.forEachIndexed { i, r -> if (r.addr == p + 0x8C) out[i] = byteArrayOf(0, 0, 0, (n++).toByte()) }
            out
        }
        val s = d.poll(torn)
        assertEquals(Phase.IN_TOWN, s.phase)
        assertNull(s.wallet)
        assertEquals(1, d.unstableReads)
    }

    @Test
    fun clearedNameEntryHasNoName() {
        // An all-zero entry would decode to charmap[0] glyphs; it must count as "no name".
        val mem = townImage().raw(TABLE_FTR + 16, ByteArray(16))
        val s = Decoder(map).poll(mem)
        assertNull(s.pockets[0].name)
        assertEquals("Test Fruit", s.pockets[2].name)
    }

    @Test
    fun misalignedNowPrivateIsNotAPlayer() {
        val mem = townImage().u32(CD + 0x2613C, PLAYERS + STRIDE + 0x10)
        assertNull(Decoder(map).poll(mem).playerIndex)
    }

    @Test
    fun playerWithoutExistsFlagIsNotInTown() {
        for (v in listOf(0, 2)) {
            val mem = townImage().u8(PLAYERS + STRIDE + 0x1086, v)
            val s = Decoder(map).poll(mem)
            assertEquals(Phase.NOT_IN_TOWN, s.phase)
            assertNull(s.playerName)
        }
    }

    @Test
    fun titleDemoSceneIsNotInTown() {
        val mem = townImage().u32(CD + 0x14, 15) // runs play_main, but listed in not_in_town_scenes
        val s = Decoder(map).poll(mem)
        assertEquals(Phase.NOT_IN_TOWN, s.phase)
        assertEquals("Test demo scene", s.scene)
    }

    @Test
    fun impossibleValuesAreDropped() {
        val p = PLAYERS + STRIDE
        val mem = townImage().u8(CD + 0x26125, 13).u32(p + 0x8C, 100_000).u16(CD + 0x20480 + 4, 5000)
        val s = Decoder(map).poll(mem)
        assertEquals(Phase.IN_TOWN, s.phase)
        assertNull(s.clock)
        assertNull(s.wallet)
        assertNull(s.turnips[2].price)
        assertEquals(90, s.turnips[1].price)
        assertTrue(s.turnips.none { it.today }) // no clock -> no "today"
    }

    @Test
    fun itemNamesNeedTheRuntimeCheck() {
        val mem = townImage().u32(PTR_CHECK + 4, 0x80123456L) // table pointer does not match
        val d = Decoder(map)
        val s = d.poll(mem)
        assertEquals(Phase.IN_TOWN, s.phase)
        assertNull(s.pockets[0].name)
        assertTrue(s.status.contains("did not verify"))
        assertEquals("0x2003", s.extras.first { it.first == "town fruit" }.second)
        // A failed check is remembered: no more name reads for this game image.
        mem.batches.clear()
        d.poll(mem)
        assertEquals(2, mem.batches.size)
    }

    @Test
    fun villagerNamesAreHarvestedFromTheNameCache() {
        val mem = townImage().u16(NPC_CACHE, 0xE00A).text(NPC_CACHE + 2, "Bob", 8)
        val d = Decoder(map)
        assertNull(d.poll(mem).villagers.first { it.id == 0xE00A }.name) // seen once: not trusted yet
        assertEquals("Bob", d.poll(mem).villagers.first { it.id == 0xE00A }.name)
        // The cache moving on to someone else keeps what was learned.
        mem.u16(NPC_CACHE, 0xE001).text(NPC_CACHE + 2, "Amy", 8)
        val s = d.poll(mem)
        assertEquals("Bob", s.villagers.first { it.id == 0xE00A }.name)
        assertNull(s.villagers.first { it.id == 0xE001 }.name)
        assertEquals("Amy", d.poll(mem).villagers.first { it.id == 0xE001 }.name)
        d.onHello("GAFE01", "other image")
        mem.u16(NPC_CACHE, 0)
        assertNull(d.poll(mem).villagers.first { it.id == 0xE00A }.name)
    }

    @Test
    fun onlyNeededRecordFieldsAreRead() {
        val d = Decoder(map)
        // 3 header reads + 17 globals + 4 players x 9 used fields (gender skipped)
        // + 15 villagers x 1 used field (town_name skipped) + 1 npc-cache read (one span)
        assertEquals(3 + 17 + 4 * 9 + 15 + 1, d.planSize)
    }

    @Test
    fun firstPlayerSlotDecodes() {
        val mem = townImage().u32(CD + 0x2613C, PLAYERS)
        mem.text(PLAYERS, "Ann", 8).u8(PLAYERS + 0x1086, 1).u32(PLAYERS + 0x8C, 5)
        val s = Decoder(map).poll(mem)
        assertEquals(0, s.playerIndex)
        assertEquals("Ann", s.playerName)
        assertEquals(5L, s.wallet)
    }

    // ------------------------------------------------------------ primitives

    @Test
    fun stringsUseCharmapAndKeepLowBytes() {
        val d = Decoder(map)
        // 0x00 is a glyph in this charset, not a terminator; only trailing spaces are trimmed.
        val b = byteArrayOf(0x41, 0x62, 0x00, 0x63, 0x20, 0x20, 0x20, 0x20)
        assertEquals("Ab" + map.charmap[0] + "c", d.decodeStr(b, 0, b.size))
        assertEquals(" Zz9", d.decodeStr(byteArrayOf(0x20, 0x5A, 0x7A, 0x39, 0x20), 0, 5))
    }

    @Test
    fun scalarTypesDecodeBigEndian() {
        val d = Decoder(map)
        fun f(type: FieldType, mask: Long? = null, count: Int = 1) =
            Field("t", null, 0, type, 0, count, null, mask, null, null)
        val b = byteArrayOf(0xFF.toByte(), 0xFE.toByte(), 0x00, 0x10)
        assertEquals(255L, d.value(f(FieldType.U8), b))
        assertEquals(-1L, d.value(f(FieldType.S8), b))
        assertEquals(0xFFFEL, d.value(f(FieldType.U16), b))
        assertEquals(-2L, d.value(f(FieldType.S16), b))
        assertEquals(0xFFFE0010L, d.value(f(FieldType.U32), b))
        assertEquals(0xFFFE0010L.toInt().toLong(), d.value(f(FieldType.S32), b))
        assertEquals(0x10L, d.value(f(FieldType.U32, mask = 0xFF), b))
        assertEquals(1.5f, d.value(f(FieldType.F32), byteArrayOf(0x3F, 0xC0.toByte(), 0, 0)))
        assertArrayEquals(longArrayOf(0xFFFE, 0x0010), d.value(f(FieldType.U16_ARR, count = 2), b) as LongArray)
        assertNull(d.value(f(FieldType.U32), byteArrayOf(1, 2)))
        assertNull(d.value(f(FieldType.U8), null))
    }
}
