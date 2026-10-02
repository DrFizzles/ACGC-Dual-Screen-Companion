package com.acdualscreen.companion.core

import java.io.IOException

/**
 * Reads the daily-tracker data ([DailyState]) for an in-town [GameState]:
 *
 * - **Talked today:** each villager keeps up to 7 memories (Anmmem_c), one per player it knows. The
 *   memory whose PersonalID (20 bytes) equals the current player's holds `last_speak_time`, which the
 *   game sets when a conversation ends. Talked today = that date equals today's.
 * - **Fossils:** buried fossils = units of the fg grid holding the fossil item with their buried
 *   bit set. The daily renewal tops them up to [DailySpec.fossilDailyMax], so dug = max - buried.
 *   The grid is about 16 KB, so it is re-read at most every [GRID_REFRESH_NS].
 * - **Glowing spot:** `shine_pos[player]`; all zero = none today, else the unit still holds the
 *   glowing-spot item until it is dug.
 *
 * Nothing here says where anything is buried; only counts and yes/no answers leave this class.
 */
class DailyReader(private val map: MemoryMap) {

    companion object {
        const val GRID_REFRESH_NS = 2_000_000_000L
        const val PERSONAL_ID_BYTES = 0x14
        /** last_speak_time.day / month / year inside Anmmem_c, used when the spec lacks them. */
        private const val SPEAK_DAY = 0x17
        private const val SPEAK_MONTH = 0x19
        private const val SPEAK_YEAR = 0x1A
    }

    private var buried: Int? = null
    private var gridAt = 0L

    /** Forget the cached fossil count (a different game image, or the day changed). */
    fun reset() {
        buried = null
        gridAt = 0L
    }

    private var lastDay: Triple<Int, Int, Int>? = null

    @Throws(IOException::class)
    fun poll(reader: MemoryReader, s: GameState, now: Long = System.nanoTime()): DailyState {
        val daily = map.daily
        val clock = s.clock
        val day = clock?.let { Triple(it.year, it.month, it.day) }
        if (day != lastDay) {
            lastDay = day
            reset()
        }
        val talked = talkedToday(reader, s)
        if (daily == null) return DailyState(talked, note = "this spec has no daily section")

        if (buried == null || now - gridAt >= GRID_REFRESH_NS) {
            buried = countBuriedFossils(reader, daily) ?: buried
            gridAt = now
        }
        return DailyState(
            talkedToday = talked,
            fossilsBuried = buried,
            fossilMax = daily.fossilDailyMax,
            shineSpot = shineSpot(reader, daily, s.playerIndex),
        )
    }

    // ---------------------------------------------------------------- neighbors

    @Throws(IOException::class)
    private fun talkedToday(reader: MemoryReader, s: GameState): Map<Int, Boolean?> {
        val clock = s.clock
        val index = s.playerIndex
        val players = map.players
        val villagers = map.villagers
        val mem = map.memories
        if (clock == null || index == null || players == null || villagers == null || mem == null || s.villagers.isEmpty()) {
            return s.villagers.associate { it.slot to null }
        }
        val playerAddr = map.recordAddr(players, index) ?: return s.villagers.associate { it.slot to null }
        val dayOff = mem.field("last_speak_day")?.offset ?: SPEAK_DAY.toLong()
        val monthOff = mem.field("last_speak_month")?.offset ?: SPEAK_MONTH.toLong()
        val yearOff = mem.field("last_speak_year")?.offset ?: SPEAK_YEAR.toLong()
        val memBytes = (maxOf(dayOff, monthOff, yearOff + 1) + 1).toInt().coerceAtLeast(PERSONAL_ID_BYTES)

        val reqs = ArrayList<ReadReq>()
        reqs += ReadReq(playerAddr, PERSONAL_ID_BYTES)
        for (v in s.villagers) {
            val rec = map.recordAddr(villagers, v.slot) ?: continue
            for (k in 0 until mem.count) reqs += ReadReq(rec + mem.offset + mem.stride * k, memBytes)
        }
        val r = readStable(reader, reqs).values
        val me = r[0] ?: return s.villagers.associate { it.slot to null }

        val out = LinkedHashMap<Int, Boolean?>()
        var i = 1
        for (v in s.villagers) {
            if (map.recordAddr(villagers, v.slot) == null) {
                out[v.slot] = null
                continue
            }
            var result: Boolean? = false
            for (k in 0 until mem.count) {
                val b = r[i++]
                if (b == null) {
                    if (result == false) result = null
                    continue
                }
                if (!b.copyOfRange(0, PERSONAL_ID_BYTES).contentEquals(me)) continue
                val d = b[dayOff.toInt()].toInt() and 0xFF
                val m = b[monthOff.toInt()].toInt() and 0xFF
                val y = Decoder.be16(b, yearOff.toInt())
                result = d == clock.day && m == clock.month && y == clock.year
                break
            }
            out[v.slot] = result
        }
        return out
    }

    // ---------------------------------------------------------------- fossils

    @Throws(IOException::class)
    private fun countBuriedFossils(reader: MemoryReader, d: DailySpec): Int? {
        val blocks = d.blocksX * d.blocksZ
        val reqs = ArrayList<ReadReq>(blocks + 1)
        for (b in 0 until blocks) reqs += ReadReq(d.fgAddr + b.toLong() * d.blockBytes, d.units * d.units * 2)
        reqs += ReadReq(d.depositAddr, blocks * d.units * 2)
        val r = readStable(reader, reqs).values
        val deposit = r[blocks] ?: return null
        var count = 0
        for (b in 0 until blocks) {
            val items = r[b] ?: return null
            for (z in 0 until d.units) {
                val row = Decoder.be16(deposit, (b * d.units + z) * 2)
                if (row == 0) continue
                for (x in 0 until d.units) {
                    if ((row shr x) and 1 == 0) continue
                    if (Decoder.be16(items, (z * d.units + x) * 2) == d.fossilItem) count++
                }
            }
        }
        return count
    }

    // ---------------------------------------------------------------- glowing spot

    @Throws(IOException::class)
    private fun shineSpot(reader: MemoryReader, d: DailySpec, playerIndex: Int?): ShineSpot {
        if (playerIndex == null) return ShineSpot.UNKNOWN
        val pos = readStable(reader, listOf(ReadReq(d.shinePosAddr + playerIndex.toLong() * d.shinePosStride, 4)))
            .values[0] ?: return ShineSpot.UNKNOWN
        val bx = pos[0].toInt() and 0xFF
        val bz = pos[1].toInt() and 0xFF
        val ux = pos[2].toInt() and 0xFF
        val uz = pos[3].toInt() and 0xFF
        if (bx == 0 && bz == 0 && ux == 0 && uz == 0) return ShineSpot.NONE_TODAY
        if (bx !in 1..d.blocksX || bz !in 1..d.blocksZ || ux >= d.units || uz >= d.units) return ShineSpot.UNKNOWN
        val block = (bz - 1) * d.blocksX + (bx - 1)
        val addr = d.fgAddr + block.toLong() * d.blockBytes + (uz * d.units + ux) * 2L
        val item = readStable(reader, listOf(ReadReq(addr, 2))).values[0] ?: return ShineSpot.UNKNOWN
        return if (Decoder.be16(item) == d.shineSpotItem) ShineSpot.NOT_DUG else ShineSpot.DUG
    }
}
