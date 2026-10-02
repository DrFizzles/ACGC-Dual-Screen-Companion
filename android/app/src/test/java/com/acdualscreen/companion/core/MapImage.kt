package com.acdualscreen.companion.core

/**
 * Adds a synthetic town map to a [FakeMemory] (normally [DecoderTest.townImage]) at the addresses
 * the spec's "map" section names. Every texture, palette, icon and colour here is made up: solid
 * patterns that only let the tests tell texels and tiers apart. Nothing comes from the game.
 */
object MapImage {
    /** The shared spec copy (kept equal to ../spec by CrossCheckTest). */
    val map: MemoryMap by lazy {
        MemoryMap.parse(MapImage::class.java.classLoader!!.getResource("crosscheck/ac_memory_map.json")!!.readText())
    }
    val spec: MapSpec get() = map.townMap!!

    const val ACTOR = 0x81400000L
    const val PLAYER_X = 2520.4f
    const val PLAYER_Z = 1540.3f
    const val PLAYER_FACING = 0x4000

    /** Synthetic acre type of block (bx, bz): 11..40, none of them a skipped border type. */
    fun typeOf(bx: Int, bz: Int) = 11 + (bz - 1) * 5 + (bx - 1)

    /** Visible texel colour index of a type's synthetic texture (1..13); 14 = top-left, 15 = slack. */
    fun indexOf(type: Int) = 1 + type % 13

    /** Synthetic palette entry k of palette [sel] (RGB5A3, opaque; entry 0 transparent). */
    fun paletteEntry(sel: Int, k: Int): Int =
        if (k == 0) 0 else if (sel == 0) 0x8000 or (k shl 10) or ((31 - k) shl 5) or (k * 2 and 31) else 0x8000 or (k shl 5) or 7

    fun colour(type: Int, k: Int): Int = GcTexture.rgb5a3(paletteEntry(type % 2, k))

    val PRIM = listOf(intArrayOf(90, 90, 225), intArrayOf(145, 70, 205), intArrayOf(170, 115, 20))
    val ENV = intArrayOf(225, 225, 225)

    fun FakeMemory.f32(addr: Long, v: Float) = u32(addr, v.toRawBits().toLong() and 0xFFFFFFFFL)

    fun combiValue(i: Int) = ((100 + i) shl 2) or (if (i == 0) 2 else 0)

    /** C4 32x32: texel (x, y) -> index; tiles of 8x8, high nibble = left pixel. */
    fun c4Texture(index: (Int, Int) -> Int): ByteArray {
        val out = ByteArray(512)
        for (y in 0 until 32) for (x in 0 until 32) {
            val block = (y / 8) * 4 + x / 8
            val o = block * 32 + (y % 8) * 4 + (x % 8) / 2
            val v = index(x, y) and 15
            out[o] = if (x % 2 == 0) ((out[o].toInt() and 0x0F) or (v shl 4)).toByte() else ((out[o].toInt() and 0xF0) or v).toByte()
        }
        return out
    }

    /** IA4 16x16: alpha 15 where the icon is drawn (a 10x12 block), intensity by row. */
    fun ia4Icon(): ByteArray {
        val out = ByteArray(256)
        for (y in 0 until 16) for (x in 0 until 16) {
            val block = (y / 4) * 2 + x / 8
            val o = block * 32 + (y % 4) * 8 + (x % 8)
            val a = if (x in 3..12 && y in 2..13) 15 else 0
            val i = if (y < 8) 15 else 0
            out[o] = ((a shl 4) or i).toByte()
        }
        return out
    }

    fun build(mem: FakeMemory = DecoderTest.townImage()): FakeMemory {
        val s = spec
        val g = s.grid
        val at = s.acreTypes
        val tx = s.texture
        val cd = map.bases.getValue("common_data")
        // Player actor chain (GAME object and play_main come from townImage).
        val game = DecoderTest.GAME_OBJ
        mem.u32(game + 0x1DC4, 1).u32(game + 0x1DC8, ACTOR)
        mem.u16(ACTOR, 0).u8(ACTOR + 2, 3)
        mem.f32(ACTOR + s.player.posX, PLAYER_X).f32(ACTOR + s.player.posY, 280f).f32(ACTOR + s.player.posZ, PLAYER_Z)
        mem.u16(ACTOR + s.player.facingOffset, PLAYER_FACING)
        mem.u8(s.player.fieldTypeAddr!!, 0)

        // Acre types: the block-type table, and the same values through the save's combi table.
        val typeTable = 0x81295A3CL
        mem.u32(at.tablePointerAddr, at.tableExpected)
        for (bz in 0 until g.blockRows) for (bx in 0 until g.blockCols) {
            val i = g.blockIndex(bx, bz)
            val inside = bx in 1..5 && bz in 1..6
            val t = if (inside) typeOf(bx, bz) else 0
            mem.u8(typeTable + i, t)
            mem.u16(at.combiAddr + 2 * i, combiValue(i))
            val e = at.dataCombiAddr + (100L + i) * at.dataCombiEntryLen
            mem.u16(e, 0x0500 + i).u16(e + at.fgIdOffset, 0x0100 + i).u8(e + at.typeOffset, t)
        }
        // Extra-bridge table: type of block (2, 2) has a bridge variant (type 50).
        for (t in 0 until at.plussBridgeCount) mem.u8(at.plussBridgeAddr + t, at.plussNone)
        mem.u8(at.plussBridgeAddr + typeOf(2, 2), 50)

        // Textures: one 512-byte C4 slot per type, two synthetic palettes.
        for (t in 0 until tx.pointerCount) {
            val slot = t % 69
            mem.u32(tx.pointerTableAddr + 4 * t, tx.rangeStart + slot * tx.size.toLong())
            mem.u8(tx.selectorAddr + t, t % 2)
        }
        for (t in 0 until 69) {
            // Slot t holds the texture of every type with t % 69 == slot; types 0..68 own their slot.
            mem.raw(tx.rangeStart + t * tx.size.toLong(), c4Texture { x, y ->
                when {
                    x >= 22 || y >= 22 -> 15
                    x == 0 && y == 0 -> 14
                    else -> indexOf(t)
                }
            })
        }
        val pal = listOf(0x806CDC60L, 0x806CDC80L)
        pal.forEachIndexed { sel, a ->
            mem.u32(tx.palettePointerAddr + 4 * sel, a)
            for (k in 0 until 16) mem.u16(a + 2 * k, paletteEntry(sel, k))
        }

        // Building acres: dump A-1, post A-2, houses+shop B-3 (houses win), dock F-5.
        val kinds = 0x81295924L
        mem.u32(s.buildings.kindsPointerAddr, s.buildings.kindsExpected)
        mem.u32(kinds + 4L * g.blockIndex(1, 1), 0x10000).u32(kinds + 4L * g.blockIndex(2, 1), 0x10)
            .u32(kinds + 4L * g.blockIndex(3, 2), 0x3).u32(kinds + 4L * g.blockIndex(5, 6), 0x40000000)

        // Villager houses: icon, tier display lists, position list, homes and heights.
        val vh = s.houses
        mem.raw(vh.iconAddr, ia4Icon())
        vh.tiers.forEachIndexed { k, t ->
            val dl = t.displayListAddr
            mem.u8(dl, 0xFA).u8(dl + 8, 0xFB)
            for (c in 0 until 3) {
                mem.u8(dl + t.primOffset + c, PRIM[k][c])
                mem.u8(dl + t.envOffset + c, ENV[c])
            }
            mem.u8(dl + t.primOffset + 3, 255).u8(dl + t.envOffset + 3, 255)
        }
        fun entry(e: Int, fg: Int, vararg slots: Int) {
            val o = vh.posListAddr + e.toLong() * vh.posEntryLen
            mem.u16(o + vh.fgNameOffset, fg)
            slots.forEachIndexed { k, v -> mem.u8(o + vh.slotsOffset + k, v) }
        }
        entry(0, 0x0100 + g.blockIndex(2, 3), 9, 9, 4, 3, 4, 7, 0, 0, 0)
        entry(1, 0x0100 + g.blockIndex(4, 1), 5, 5, 1, 6, 6, 2, 7, 7, 3)
        for (e in 2 until vh.terminatorIndex) entry(e, 0x0001, 0, 0, 0)
        entry(vh.terminatorIndex, vh.terminator)

        val vil = map.villagers!!
        fun home(slot: Int, id: Int, bx: Int, bz: Int, ux: Int, uz: Int, y: Float) {
            val rec = map.recordAddr(vil, slot)!!
            mem.u16(rec, id)
            mem.u8(rec + vil.field("home_block_x")!!.offset, bx).u8(rec + vil.field("home_block_z")!!.offset, bz)
                .u8(rec + vil.field("home_ut_x")!!.offset, ux).u8(rec + vil.field("home_ut_z")!!.offset, uz)
            mem.f32(vh.houseYAddr + slot * vh.houseYStride + vh.houseYFieldOffset, y)
        }
        home(0, 0xE001, 2, 3, 3, 5, 280f)   // exact slot match -> idx 7, tier 0
        home(2, 0xE00A, 4, 1, 1, 1, 160f)   // no exact match -> slot 0 (idx 1), tier 1
        home(14, 0xE0FF, 5, 6, 0, 0, 40f)   // fg not listed -> entry 0 slot 0 (idx 4), tier 2
        home(5, 0x1234, 1, 1, 0, 0, 280f)   // invalid villager id -> no icon
        home(7, 0xE007, 6, 3, 0, 0, 280f)   // home outside the map -> no icon
        check(cd == DecoderTest.CD)
        return mem
    }
}
