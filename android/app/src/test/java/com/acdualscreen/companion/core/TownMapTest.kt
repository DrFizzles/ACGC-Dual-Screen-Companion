package com.acdualscreen.companion.core

import com.acdualscreen.companion.core.MapImage.f32
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** TownMapReader / TownMapRenderer on the synthetic map image of [MapImage]. */
class TownMapTest {

    private val map = MapImage.map
    private val spec = MapImage.spec
    private val grid = spec.grid

    private fun reader() = TownMapReader(map, spec).also { it.onHello("GAFE01", "hash") }

    private fun poll(mem: FakeMemory = MapImage.build(), r: TownMapReader = reader(), now: Long = 0L) = r.poll(mem, now)

    // ------------------------------------------------------------ spec

    @Test
    fun specParses() {
        assertEquals(5, grid.cols)
        assertEquals(6, grid.rows)
        assertEquals("C-3", grid.acreLabel(3, 3))
        assertEquals(null, grid.acreLabel(6, 3))
        assertEquals(GcFormat.C4, spec.texture.format)
        assertEquals(TlutFormat.RGB5A3, spec.texture.paletteFormat)
        assertEquals(GcFormat.IA4, spec.houses.iconFormat)
        assertEquals(3, spec.houses.tiers.size)
        assertEquals(0xFF5A5AE1.toInt(), spec.houses.tiers[0].expectedPrim)
        assertEquals("actor", spec.player.actorStep)
        assertEquals(setOf(7L), spec.player.showScenes)
        assertEquals(10, spec.buildings.indicators.size)
        assertEquals("player_houses", spec.buildings.indicators[0].key)
        assertTrue(spec.texture.validPointer(spec.texture.rangeStart))
        assertFalse(spec.texture.validPointer(spec.texture.rangeStart + 2))
        assertFalse(spec.texture.validPointer(spec.texture.rangeEnd))
    }

    @Test
    fun mapSectionIsOptional() {
        val m = DecoderTest.fixture()
        assertNull(m.townMap)
        assertFalse(m.warnings.any { it.startsWith("map") })
    }

    @Test
    fun brokenMapSectionIsAWarningNotAFailure() {
        val text = MapImage::class.java.classLoader!!.getResource("crosscheck/ac_memory_map.json")!!.readText()
            .replace("\"acre_map_units\"", "\"acre_units_renamed\"")
        val m = MemoryMap.parse(text)
        assertNull(m.townMap)
        assertTrue(m.warnings.any { it.startsWith("map:") })
    }

    // ------------------------------------------------------------ layout

    @Test
    fun acresInGameOrderWithCroppedTextures() {
        val s = poll()
        val lay = s.layout!!
        assertEquals("", s.note)
        assertEquals(30, lay.acres.size)
        assertEquals(110, lay.widthUnits)
        assertEquals(132, lay.heightUnits)
        for (cell in lay.acres) {
            val bx = cell.col + 1
            val bz = cell.row + 1
            assertEquals(bx, cell.blockX)
            assertEquals(bz, cell.blockZ)
            assertEquals(MapImage.typeOf(bx, bz), cell.type)
            val px = cell.pixels!!
            assertEquals(22 * 22, px.size)
            assertEquals(MapImage.colour(cell.type, 14), px[0]) // top-left texel kept (no flip)
            assertEquals(MapImage.colour(cell.type, MapImage.indexOf(cell.type)), px[21 * 22 + 21])
            assertFalse(px.any { it == MapImage.colour(cell.type, 15) }) // slack column/row cropped
        }
    }

    @Test
    fun skippedBorderTypesShiftTheListAndPad() {
        val mem = MapImage.build()
        mem.u8(0x81295A3CL + grid.blockIndex(1, 1), 5) // a border type: skipped
        val lay = poll(mem).layout!!
        assertEquals(MapImage.typeOf(2, 1), lay.acres[0].type)
        assertEquals(2, lay.acres[0].blockX)
        assertEquals(spec.acreTypes.padType, lay.acres[29].type)
        assertEquals(-1, lay.acres[29].blockX)
    }

    @Test
    fun typesFromTheSaveWhenTheTablePointerIsWrong() {
        val mem = MapImage.build()
        mem.u32(spec.acreTypes.tablePointerAddr, 0x81000000L)
        mem.u8(0x81000000L + grid.blockIndex(3, 3), 99) // must not be used
        val s = poll(mem)
        assertEquals((1..30).map { MapImage.typeOf((it - 1) % 5 + 1, (it - 1) / 5 + 1) }, s.layout!!.acres.map { it.type })
        assertTrue(s.note, s.note.contains("from the save"))
    }

    @Test
    fun extraBridgeReplacesTheType() {
        val mem = MapImage.build()
        val b = spec.acreTypes.bridge!!
        mem.u8(b.addr + b.blockXOffset, 2).u8(b.addr + b.blockZOffset, 2).u8(b.addr + b.flagsOffset, 0x80)
        val lay = poll(mem).layout!!
        val cell = lay.acres.first { it.blockX == 2 && it.blockZ == 2 }
        assertEquals(50, cell.type)
        assertEquals(MapImage.colour(50, MapImage.indexOf(50)), cell.pixels!![100])
        // Flag clear: no bridge even though the block matches.
        mem.u8(b.addr + b.flagsOffset, 0x7F)
        assertEquals(MapImage.typeOf(2, 2), poll(mem).layout!!.acres.first { it.blockX == 2 && it.blockZ == 2 }.type)
    }

    @Test
    fun invalidTexturesFallBack() {
        val mem = MapImage.build()
        val t = MapImage.typeOf(3, 2)
        mem.u32(spec.texture.pointerTableAddr + 4 * t, spec.texture.rangeStart + 3) // misaligned
        val s = poll(mem)
        val lay = s.layout!!
        assertNull(lay.acres.first { it.blockX == 3 && it.blockZ == 2 }.pixels)
        assertTrue(s.note, s.note.contains("1 acre image"))
        // That acre holds the house cluster: its fallback marker must now be drawn.
        val houses = lay.buildings.first { it.key == "player_houses" }
        assertFalse(houses.inTexture)
        assertEquals(MapSpec.parseColor("#3D7BE0"), houses.color)
        assertEquals("Houses", houses.label)
    }

    @Test
    fun wrongPalettePointersDisableAllTextures() {
        val mem = MapImage.build()
        mem.u32(spec.texture.palettePointerAddr, 0x806CDC00L)
        val s = poll(mem)
        assertTrue(s.layout!!.acres.all { it.pixels == null })
        assertTrue(s.note, s.note.contains("palettes did not verify"))
        assertTrue(s.layout.buildings.none { it.inTexture })
    }

    @Test
    fun buildingAcres() {
        val lay = poll().layout!!
        assertEquals(
            listOf("dump" to (1 to 1), "post_office" to (2 to 1), "player_houses" to (3 to 2), "dock" to (5 to 6)),
            lay.buildings.map { it.key to (it.blockX to it.blockZ) },
        )
        assertTrue(lay.buildings.all { it.inTexture })
    }

    // ------------------------------------------------------------ villager houses

    @Test
    fun villagerHousesSlotsAndTiers() {
        val s = poll()
        assertEquals(
            listOf(
                HouseIcon(0, 2, 3, 7, 0, 35f, 62f, true),
                HouseIcon(2, 4, 1, 1, 1, 79f, 4f, false),
                HouseIcon(14, 5, 6, 4, 2, 101f, 121f, false),
            ),
            s.houses,
        )
        assertEquals(3, s.houseArt.size)
        val tier1 = s.houseArt[1]
        assertEquals(16, tier1.w)
        assertEquals(10, tier1.units)
        val px = tier1.pixels!!
        assertEquals(0, px[0] ushr 24) // transparent corner
        assertEquals(0xFF9146CD.toInt(), px[2 * 16 + 3]) // I = 255 -> PRIM (145, 70, 205)
        assertEquals(0xFFE1E1E1.toInt(), px[13 * 16 + 3]) // I = 0 -> ENV
    }

    @Test
    fun tierRule() {
        assertEquals(0, TownMapReader.tierFor(280f, true))
        assertEquals(1, TownMapReader.tierFor(160f, true))
        assertEquals(2, TownMapReader.tierFor(40f, true))
        assertEquals(0, TownMapReader.tierFor(280f, false))
        assertEquals(0, TownMapReader.tierFor(160f, false))
        assertEquals(1, TownMapReader.tierFor(40f, false))
    }

    @Test
    fun tint() {
        val icon = intArrayOf(0xFFFFFFFF.toInt(), 0xFF000000.toInt(), 0x80808080.toInt(), 0)
        val prim = GcTexture.argb(255, 90, 90, 225)
        val env = GcTexture.argb(255, 225, 225, 225)
        val t = TownMapReader.tint(icon, prim, env)
        assertEquals(prim, t[0])
        assertEquals(env, t[1])
        // I = 128: 225 + (90 - 225) * 128 / 255 = 157.2 -> 157; alpha 0x80 kept
        assertEquals(GcTexture.argb(0x80, 157, 157, 225), t[2])
        assertEquals(0, t[3] ushr 24)
        // Rounded, not truncated (townmap.py): I = 0x55 with prim 145 -> 225 - 80 / 3 = 198.33 -> 198,
        // prim 70 -> 225 - 155 / 3 = 173.33 -> 173, prim 205 -> 225 - 20 / 3 = 218.33 -> 218;
        // I = 0xAA -> 171.67 -> 172, 121.67 -> 122, 211.67 -> 212.
        val t2 = TownMapReader.tint(intArrayOf(0xFF555555.toInt(), 0xFFAAAAAA.toInt()), GcTexture.argb(255, 145, 70, 205), env)
        assertEquals(GcTexture.argb(255, 198, 173, 218), t2[0])
        assertEquals(GcTexture.argb(255, 172, 122, 212), t2[1])
    }

    @Test
    fun wrongHouseColoursUseTheSpecValues() {
        val mem = MapImage.build()
        mem.u8(spec.houses.tiers[2].displayListAddr + spec.houses.tiers[2].primOffset, 1)
        val s = poll(mem)
        assertEquals(spec.houses.tiers[2].expectedPrim, s.houseArt[2].fallbackColor)
        assertTrue(s.note, s.note.contains("house colours"))
    }

    // ------------------------------------------------------------ player

    @Test
    fun playerMarker() {
        val p = poll().player!!
        assertEquals(3, p.blockX)
        assertEquals(2, p.blockZ)
        assertEquals((MapImage.PLAYER_X / 640f - 1) * 22, p.mapX, 1e-4f)
        assertEquals((MapImage.PLAYER_Z / 640f - 1) * 22, p.mapY, 1e-4f)
        assertEquals(0x4000, p.facing)
        assertEquals(1f, p.dirX, 1e-6f)
        assertEquals(0f, p.dirY, 1e-6f)
    }

    @Test
    fun facingVectors() {
        fun dir(f: Int) = PlayerMarker(0f, 0f, 1, 1, f).let { it.dirX to it.dirY }
        fun near(a: Pair<Float, Float>, x: Float, y: Float) = abs(a.first - x) < 1e-6 && abs(a.second - y) < 1e-6
        assertTrue(near(dir(0), 0f, 1f))          // south = down
        assertTrue(near(dir(0x4000), 1f, 0f))     // east = right
        assertTrue(near(dir(0x8000), 0f, -1f))    // north = up
        assertTrue(near(dir(0xC000), -1f, 0f))    // west = left
        val a = TownMapGeometry.arrow(10f, 10f, 0f, 1f, 1f)
        assertTrue(a[1] > 10f + 2f) // tip below the centre when facing south
        assertEquals(10f, a[0], 1e-6f)
    }

    @Test
    fun playerHiddenWhenNotOutdoorsInTown() {
        val mem = MapImage.build()
        val r = reader()
        assertNotNull(r.poll(mem, 0))
        mem.u32(DecoderTest.CD + 0x14, 8) // another scene
        assertNull(poll(mem).player)
        mem.u32(DecoderTest.CD + 0x14, 7)
        mem.u8(spec.player.fieldTypeAddr!!, 1) // indoors
        assertNull(poll(mem).player)
        mem.u8(spec.player.fieldTypeAddr, 0)
        mem.f32(MapImage.ACTOR + spec.player.posZ, 8 * 640f + 100f) // the island (block z 8)
        assertNull(poll(mem).player)
        mem.f32(MapImage.ACTOR + spec.player.posZ, MapImage.PLAYER_Z)
        mem.u8(MapImage.ACTOR + 2, 4) // not the player actor
        assertNull(poll(mem).player)
        mem.u8(MapImage.ACTOR + 2, 3)
        mem.u32(DecoderTest.GAME_OBJ + 0x1DC4, 0) // no player actor
        assertNull(poll(mem).player)
        mem.u32(DecoderTest.GAME_OBJ + 0x1DC4, 1)
        mem.f32(MapImage.ACTOR + spec.player.posX, Float.NaN)
        assertNull(poll(mem).player)
        mem.f32(MapImage.ACTOR + spec.player.posX, MapImage.PLAYER_X)
        assertNotNull(poll(mem).player)
    }

    @Test
    fun aTornReadKeepsTheLastMarkerBriefly() {
        val mem = MapImage.build()
        val r = reader()
        val first = r.poll(mem, 0).player!!
        mem.u16(MapImage.ACTOR, 1) // actor check fails (as a torn read would)
        repeat(TownMapReader.PLAYER_GRACE) { assertEquals(first, r.poll(mem, 0).player) }
        assertNull(r.poll(mem, 0).player)
    }

    @Test
    fun indoorAcreOnlyWithExitData() {
        val mem = MapImage.build()
        mem.u8(spec.player.fieldTypeAddr!!, 1)
        assertNull(poll(mem).indoorAcre) // next_scene 0
        mem.u32(spec.player.nextSceneAddr!!, 7)
        mem.u16(spec.player.exitPositionAddr!!, 4 * 640 + 300).u16(spec.player.exitPositionAddr + 4, 5 * 640 + 20)
        val s = poll(mem)
        assertNull(s.player)
        assertEquals(4 to 5, s.indoorAcre)
    }

    // ------------------------------------------------------------ caching and read discipline

    @Test
    fun staticTablesAndLayoutAreCached() {
        val mem = MapImage.build()
        val r = reader()
        val a = r.poll(mem, 0)
        val b = r.poll(mem, 1_000_000_000L)
        assertEquals(1, r.staticReads)
        assertEquals(1, r.layoutBuilds)
        assertTrue(a.layout === b.layout)
        assertEquals(a, b)
        // A different town (combi table changes): new layout, statics kept.
        mem.u16(spec.acreTypes.combiAddr + 2 * grid.blockIndex(1, 1), MapImage.combiValue(grid.blockIndex(1, 1)) + 4)
        val c = r.poll(mem, 2_000_000_000L)
        assertEquals(2, r.layoutBuilds)
        assertEquals(1, r.staticReads)
        assertFalse(a.layout === c.layout)
        // A new game image drops everything.
        r.onHello("GAFE01", "other")
        r.poll(mem, 3_000_000_000L)
        assertEquals(2, r.staticReads)
    }

    @Test
    fun aTornSignatureReadKeepsTheLayout() {
        val mem = MapImage.build()
        val r = reader()
        val a = r.poll(mem, 0)
        // Every read of the block-type pointer comes back different (as if the CPU kept writing).
        var flip = 0
        val tearing = MemoryReader { reqs ->
            mem.read(reqs).mapIndexed { k, b ->
                if (reqs[k].addr == spec.acreTypes.tablePointerAddr && b != null) b.copyOf().also { it[3] = (flip++).toByte() } else b
            }
        }
        val b = r.poll(tearing, 1_000_000L)
        assertEquals(1, r.layoutBuilds)
        assertTrue(a.layout === b.layout)
    }

    @Test
    fun housesRefreshOnlyEveryFewSeconds() {
        val mem = MapImage.build()
        val r = reader()
        assertEquals(3, r.poll(mem, 0).houses.size)
        mem.u16(map.recordAddr(map.villagers!!, 0)!!, 0) // villager 0 moves out
        assertEquals(3, r.poll(mem, 1_000_000_000L).houses.size)
        assertEquals(2, r.poll(mem, TownMapReader.HOUSE_REFRESH_NS + 1).houses.size)
    }

    /** [mem], except that every read covering [addr] comes back different (a value being written). */
    private fun tearingAt(mem: FakeMemory, addr: Long): MemoryReader {
        var flip = 0
        return MemoryReader { reqs ->
            mem.read(reqs).mapIndexed { k, b ->
                val q = reqs[k]
                if (b != null && addr >= q.addr && addr < q.addr + q.size) {
                    b.copyOf().also { it[(addr - q.addr).toInt()] = (flip++).toByte() }
                } else b
            }
        }
    }

    @Test
    fun aTornHouseReadKeepsTheIconAndRetriesSoon() {
        val mem = MapImage.build()
        val r = reader()
        val before = r.poll(mem, 0).houses
        assertEquals(3, before.size)
        assertEquals(1, r.houseReads)
        val vil = map.villagers!!
        // Villager 0's home block tears, villager 14's house height tears.
        val homeX = map.recordAddr(vil, 0)!! + vil.field("home_block_x")!!.offset
        val y14 = spec.houses.houseYAddr + 14 * spec.houses.houseYStride + spec.houses.houseYFieldOffset
        val t1 = TownMapReader.HOUSE_REFRESH_NS
        val torn = r.poll(tearingAt(mem, homeX), t1).houses
        assertEquals(2, r.houseReads)
        assertEquals(before, torn) // slot 0 kept its icon
        val torn2 = r.poll(tearingAt(mem, y14), t1 + TownMapReader.HOUSE_RETRY_NS).houses
        assertEquals(3, r.houseReads) // retried after HOUSE_RETRY_NS, not HOUSE_REFRESH_NS
        assertEquals(before, torn2) // slot 14 kept its tier (2), not the no-height default (0)
        assertEquals(2, torn2.first { it.slot == 14 }.tier)
        // Reads agree again: back to the normal refresh period.
        r.poll(mem, t1 + 2 * TownMapReader.HOUSE_RETRY_NS)
        assertEquals(4, r.houseReads)
        r.poll(mem, t1 + 3 * TownMapReader.HOUSE_RETRY_NS)
        assertEquals(4, r.houseReads)
    }

    @Test
    fun aTornReadOfANewSlotShowsNothingForIt() {
        val mem = MapImage.build()
        val vil = map.villagers!!
        val r = reader()
        // The first read has no previous icon to keep: the torn slot is left out until the retry.
        val first = r.poll(tearingAt(mem, map.recordAddr(vil, 2)!!), 0).houses
        assertEquals(listOf(0, 14), first.map { it.slot })
        assertEquals(listOf(0, 2, 14), r.poll(mem, TownMapReader.HOUSE_RETRY_NS).houses.map { it.slot })
    }

    @Test
    fun incompleteStaticsAreReReadWithoutRebuildingTheLayout() {
        val mem = MapImage.build()
        mem.raw(spec.houses.iconAddr, ByteArray(256)) // fully transparent icon: statics incomplete
        val r = reader()
        val a = r.poll(mem, 0)
        val step = TownMapReader.STATIC_RETRY_NS + 1
        val b = r.poll(mem, step)
        val c = r.poll(mem, 2 * step)
        assertEquals(3, r.staticReads)
        assertEquals(1, r.layoutBuilds)
        assertTrue(a.layout === c.layout)
        assertTrue(a.houseArt === c.houseArt) // same content keeps the same list
        assertEquals(a, b)
        assertTrue(a.houseArt.all { it.pixels == null })
        // The icon becomes readable: new house art, but the acres did not change.
        mem.raw(spec.houses.iconAddr, MapImage.ia4Icon())
        val d = r.poll(mem, 3 * step)
        assertEquals(4, r.staticReads)
        assertEquals(1, r.layoutBuilds)
        assertTrue(a.layout === d.layout)
        assertTrue(d.houseArt.all { it.pixels != null })
        // Complete now: no more re-reads.
        r.poll(mem, 5 * step)
        assertEquals(4, r.staticReads)
    }

    @Test
    fun staticsThatChangeTheAcreArtRebuildTheLayout() {
        val mem = MapImage.build()
        mem.u32(spec.texture.palettePointerAddr, 0x806CDC00L) // palettes fail: no acre images
        val r = reader()
        val a = r.poll(mem, 0)
        assertTrue(a.layout!!.acres.all { it.pixels == null })
        mem.u32(spec.texture.palettePointerAddr, 0x806CDC60L)
        val b = r.poll(mem, TownMapReader.STATIC_RETRY_NS + 1)
        assertEquals(2, r.layoutBuilds)
        assertFalse(a.layout === b.layout)
        assertTrue(b.layout!!.acres.all { it.pixels != null })
        assertEquals("", b.note)
    }

    @Test
    fun steadyPollsAreSmallAndNeverTouchTheItemGrid() {
        val mem = MapImage.build()
        val r = reader()
        r.poll(mem, 0)
        mem.batches.clear()
        r.poll(mem, 1_000_000L)
        // D1 (doubled), the GAME-relative chain step (doubled), the actor (doubled).
        assertEquals(3, mem.batches.size)
        assertTrue(mem.batches.sumOf { b -> b.sumOf { it.size } } < 1024)

        // Over every poll, common_data is only read where the map spec points.
        val cd = DecoderTest.CD
        val vil = map.villagers!!
        val allowed = ArrayList<LongRange>()
        fun allow(a: Long, n: Int) { allowed += a until a + n }
        allow(spec.acreTypes.combiAddr, 2 * spec.acreTypes.combiCount)
        allow(spec.acreTypes.tableExpected, spec.acreTypes.tableCount) // l_block_type (.bss, just past common_data)
        allow(spec.buildings.kindsExpected, 4 * spec.buildings.kindsCount)
        spec.acreTypes.bridge?.let { allow(it.addr, it.readSize) }
        allow(cd + 0x14, 4)
        allow(spec.player.fieldTypeAddr!!, 1)
        allow(spec.player.nextSceneAddr!!, 4)
        allow(spec.player.exitPositionAddr!!, 6)
        for (i in 0 until vil.count) {
            val rec = map.recordAddr(vil, i)!!
            allow(rec, 2)
            allow(rec + vil.field("home_block_x")!!.offset, 4)
            allow(spec.houses.houseYAddr + i * spec.houses.houseYStride + spec.houses.houseYFieldOffset, 4)
        }
        val mem2 = MapImage.build()
        val r2 = reader()
        for (k in 0 until 3) r2.poll(mem2, k * TownMapReader.HOUSE_REFRESH_NS)
        for (req in mem2.batches.flatten()) {
            if (req.addr !in cd until cd + 0x30000) continue
            val inside = allowed.any { req.addr in it && req.addr + req.size - 1 in it }
            assertTrue("unexpected common_data read 0x%X+%d".format(req.addr, req.size), inside)
        }
    }

    // ------------------------------------------------------------ rendering

    @Test
    fun composeDrawsAcresAndIcons() {
        val s = poll()
        val scale = 8
        val ras = TownMapRenderer.compose(s.layout!!, s.houseArt, s.houses, scale)
        assertEquals(110 * scale, ras.width)
        assertEquals(132 * scale, ras.height)
        // Acre C-4 (col 3, row 2): a texel in its middle.
        val t = MapImage.typeOf(4, 3)
        assertEquals(MapImage.colour(t, MapImage.indexOf(t)), ras[(3 * 22 + 11) * scale + 3, (2 * 22 + 11) * scale + 3])
        // Top-left texel of acre B-2 fills an 8x8 pixel block.
        val t2 = MapImage.typeOf(2, 2)
        assertEquals(MapImage.colour(t2, 14), ras[22 * scale + 7, 22 * scale + 7])
        assertEquals(MapImage.colour(t2, MapImage.indexOf(t2)), ras[22 * scale + 8, 22 * scale + 8])
        // House icon of villager 0, centred at (35, 62), tier 0 (prim blue): icon texel (3, 2) is
        // drawn at map unit (35 - 5 + 3 * 10/16, 62 - 5 + 2 * 10/16) = 5 px per texel at scale 8.
        val ix = ((35 - 5) * scale) + 3 * 5 + 2
        val iy = ((62 - 5) * scale) + 2 * 5 + 2
        assertEquals(GcTexture.argb(255, 90, 90, 225), ras[ix, iy])
        // Its transparent corner shows the acre underneath.
        val ta = MapImage.typeOf(2, 3)
        assertEquals(MapImage.colour(ta, MapImage.indexOf(ta)), ras[(35 - 5) * scale + 1, (62 - 5) * scale + 1])
    }

    @Test
    fun acresAtNativeScaleMatchTheFullComposition() {
        // TownMapView draws composeAcres at scale 1 plus the icons; it must show the same acres.
        val s = poll()
        val lay = s.layout!!
        val one = TownMapRenderer.composeAcres(lay, 1)
        assertEquals(110, one.width)
        assertEquals(132, one.height)
        val eight = TownMapRenderer.compose(lay, s.houseArt, emptyList(), 8)
        for (y in 0 until one.height) for (x in 0 until one.width) {
            assertEquals("texel ($x, $y)", eight[x * 8 + 4, y * 8 + 4], one[x, y])
        }
        // An icon's rectangle: centred on the house, the art's size.
        val h = s.houses.first { it.slot == 0 }
        val a = TownMapGeometry.artFor(h, s.houseArt)!!
        assertTrue(a === s.houseArt[0])
        assertArrayEquals(floatArrayOf(30f, 57f, 40f, 67f), TownMapGeometry.houseRect(h, a), 0f)
        assertTrue(TownMapGeometry.artFor(h.copy(tier = 9), s.houseArt) === s.houseArt[0])
        assertNull(TownMapGeometry.artFor(h, emptyList()))
    }

    @Test
    fun composeFallbacks() {
        val mem = MapImage.build()
        mem.u32(spec.texture.palettePointerAddr, 0x806CDC00L) // no textures at all
        mem.raw(spec.houses.iconAddr, ByteArray(256)) // fully transparent icon: unusable
        val s = poll(mem)
        val ras = TownMapRenderer.compose(s.layout!!, s.houseArt, s.houses, 4)
        // Plain acre with its grid line.
        assertEquals(TownMapRenderer.FALLBACK_LINE, ras[(22 + 5) * 4, 22 * 4])
        assertEquals(TownMapRenderer.FALLBACK_ACRE, ras[(22 + 2) * 4, (22 + 2) * 4])
        // Building fallback square in the dump acre (A-1) centre.
        assertEquals(MapSpec.parseColor("#8A6A3D"), ras[11 * 4, 11 * 4])
        // House fallback: a square in the tier's prim colour.
        assertEquals(GcTexture.argb(255, 90, 90, 225), ras[35 * 4, 62 * 4])
        assertTrue(s.note, s.note.contains("house icon"))
    }

    @Test
    fun overlayMarksThePlayerAndItsAcre() {
        val s = poll()
        val scale = 8
        val ras = TownMapRenderer.compose(s.layout!!, s.houseArt, s.houses, scale)
        TownMapRenderer.drawOverlay(ras, s, scale)
        val p = s.player!!
        val cx = (p.mapX * scale).toInt()
        val cy = (p.mapY * scale).toInt()
        assertEquals(s.markerColor, ras[cx, cy])
        // Facing east: the arrow is to the right of the dot, not to the left.
        val r = TownMapGeometry.DOT_RADIUS * scale
        assertEquals(s.markerColor, ras[(cx + r * 1.8f).toInt(), cy])
        assertFalse(ras[(cx - r * 1.8f).toInt(), cy] == s.markerColor)
        // Highlight box on acre B-3's border, not inside it.
        val rect = s.layout.acreRect(3, 2)
        assertEquals(s.highlightColor, ras[(rect[0] * scale).toInt() + 30, (rect[1] * scale).toInt()])
        assertFalse(ras[(rect[0] * scale).toInt() + 30, (rect[1] * scale).toInt() + 30] == s.highlightColor)
        assertEquals(3 to 2, TownMapRenderer.blockAt(s.layout, p.mapX, p.mapY))
    }

    @Test
    fun rasterBlend() {
        val ras = Raster(2, 1, intArrayOf(0xFF0000FF.toInt(), 0))
        ras.blend(0, 0, 0x80FF0000.toInt())
        val c = ras[0, 0]
        assertEquals(255, c ushr 24)
        assertTrue(((c shr 16) and 0xFF) in 127..129)
        assertTrue((c and 0xFF) in 126..128)
        ras.blend(1, 0, 0x80FF0000.toInt()) // over transparent: keeps the source colour
        assertEquals(0x80FF0000.toInt(), ras[1, 0])
        assertArrayEquals(intArrayOf(0, 0), Raster(2, 1).pixels)
    }
}
