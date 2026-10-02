package com.acdualscreen.companion.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * Python/Kotlin decoder cross-check. src/test/resources/crosscheck/ holds a copy of the shared
 * spec and the sparse synthetic images that tools/pc_client/crosscheck_export.py decoded with
 * the Python client (acmap.py), plus the canonical summaries it produced. This test loads the
 * same bytes into [FakeMemory], decodes them with [Decoder] and expects identical summaries.
 * map_cases.json does the same for the town map ([TownMapReader], [TownMapRenderer.compose]).
 *
 * Regenerate after any spec or decoder change:  python tools/pc_client/crosscheck_export.py
 */
class CrossCheckTest {

    private fun resource(name: String): String =
        CrossCheckTest::class.java.classLoader!!.getResource("crosscheck/$name")!!.readText()

    private val map by lazy { MemoryMap.parse(resource("ac_memory_map.json")) }

    @Test
    fun specCopyMatchesSharedSpec() {
        val shared = listOf("../../spec/ac_memory_map.json", "../spec/ac_memory_map.json")
            .map(::File).firstOrNull { it.isFile } ?: return // shared spec not available: nothing to compare
        assertEquals(
            "crosscheck/ac_memory_map.json is stale: rerun tools/pc_client/crosscheck_export.py",
            shared.readText().replace("\r\n", "\n"), resource("ac_memory_map.json").replace("\r\n", "\n"),
        )
    }

    @Test
    fun decodesLikeThePythonClient() {
        val root = JSONObject(resource("cases.json"))
        val cases = root.getJSONArray("cases")
        assertTrue(cases.length() >= 8)
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val mem = FakeMemory()
            val regions = c.getJSONArray("regions")
            for (k in 0 until regions.length()) {
                val r = regions.getJSONObject(k)
                mem.raw(MemoryMap.num(r.getString("addr")), hex(r.getString("hex")))
            }
            val d = Decoder(map)
            d.onHello("GAFE01", c.getString("game_hash"))
            d.poll(mem) // first poll learns names, like the Python side
            val actual = summary(d.poll(mem))
            val expected = plain(c.getJSONObject("expected"))
            assertEquals("case ${c.getString("name")}", expected, actual)
        }
    }

    /**
     * Town map: map_cases.json holds synthetic town-map images and what townmap.py made of them
     * (crosscheck_export.map_decode). The same bytes through [TownMapReader] must give the same
     * acres (type, block, texel hash), buildings, houses (slot, block, spot, tier, centre), tinted
     * icon per tier, player marker (block, map position, facing, direction) and highlighted acre,
     * and [TownMapRenderer.compose] must give the same pixels as townmap.py's static map.
     */
    @Test
    fun townMapLikeThePythonClient() {
        val spec = map.townMap
        assertNotNull("the shared spec has a map section", spec)
        val cases = JSONObject(resource("map_cases.json")).getJSONArray("cases")
        assertTrue(cases.length() >= 4)
        var composed = 0
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val e = c.getJSONObject("expected")
            val r = TownMapReader(map, spec!!).also { it.onHello("GAFE01", c.getString("game_hash")) }
            val s = r.poll(memoryOf(c), 0L)
            assertEquals("$name: python status", "ok", e.getString("status"))
            val lay = s.layout
            assertNotNull("$name: layout (${s.note})", lay)
            lay!!

            fun block(v: Int): Long? = if (v < 0) null else v.toLong()
            assertEquals("$name acres", norm(e.getJSONArray("acres")),
                lay.acres.map { listOf(it.type.toLong(), block(it.blockX), block(it.blockZ), it.pixels?.let(::sha)) })
            assertEquals("$name buildings", norm(e.getJSONArray("buildings")),
                lay.buildings.map { listOf(it.key, it.blockX.toLong(), it.blockZ.toLong()) }
                    .sortedWith(compareBy({ it[0] as String }, { it[1] as Long }, { it[2] as Long })))

            val eh = e.getJSONArray("houses")
            val houses = s.houses.sortedBy { it.slot }
            assertEquals("$name house count", eh.length(), houses.size)
            for ((k, h) in houses.withIndex()) {
                val x = eh.getJSONArray(k)
                assertEquals("$name house $k", listOf(x.getInt(0), x.getInt(1), x.getInt(2), x.getInt(3), x.getInt(4)),
                    listOf(h.slot, h.blockX, h.blockZ, h.idx, h.tier))
                assertEquals("$name house $k cx", x.getDouble(5), h.cx.toDouble(), 1e-6)
                assertEquals("$name house $k cy", x.getDouble(6), h.cy.toDouble(), 1e-6)
            }
            val icons = e.getJSONObject("icons")
            for (t in icons.keys()) {
                val art = s.houseArt.firstOrNull { it.tier == t.toInt() }
                assertNotNull("$name icon tier $t", art)
                assertEquals("$name icon tier $t pixels", icons.getString(t), art!!.pixels?.let(::sha))
            }

            val ep = e.optJSONObject("player")
            val p = s.player
            if (ep == null) {
                assertNull("$name player", p)
            } else {
                assertNotNull("$name player", p)
                p!!
                val b = ep.getJSONArray("block")
                val m = ep.getJSONArray("map")
                val d = ep.getJSONArray("dir")
                assertEquals("$name player block", b.getInt(0) to b.getInt(1), p.blockX to p.blockZ)
                assertEquals("$name player facing", ep.getInt("facing"), p.facing)
                // townmap.py reports the position rounded to 3 decimals.
                assertEquals("$name player x", m.getDouble(0), p.mapX.toDouble(), 6e-4)
                assertEquals("$name player y", m.getDouble(1), p.mapY.toDouble(), 6e-4)
                assertEquals("$name player dir x", d.getDouble(0), p.dirX.toDouble(), 1e-6)
                assertEquals("$name player dir y", d.getDouble(1), p.dirY.toDouble(), 1e-6)
                assertEquals("$name marker acre", b.getInt(0) to b.getInt(1), TownMapRenderer.blockAt(lay, p.mapX, p.mapY))
            }
            val hl = e.optJSONArray("highlight")
            val box = p?.let { it.blockX to it.blockZ } ?: s.indoorAcre
            assertEquals("$name highlight", hl?.let { it.getInt(0) to it.getInt(1) }, box)

            val ec = e.optJSONObject("compose") ?: continue
            val scale = ec.getInt("scale")
            val ras = TownMapRenderer.compose(lay, s.houseArt, s.houses, scale)
            assertEquals("$name compose size", ec.getInt("width") to ec.getInt("height"), ras.width to ras.height)
            val rows = ec.getJSONArray("rows")
            val bad = (0 until ras.height).filter {
                rows.getString(it) != sha(ras.pixels.copyOfRange(it * ras.width, (it + 1) * ras.width)).substring(0, 16)
            }
            assertTrue("$name compose: ${bad.size} pixel rows differ from townmap.py, first ${bad.firstOrNull()}", bad.isEmpty())
            composed++
        }
        assertTrue("at least one case compares the composition", composed > 0)
    }

    private fun memoryOf(c: JSONObject): FakeMemory {
        val mem = FakeMemory()
        val regions = c.getJSONArray("regions")
        for (k in 0 until regions.length()) {
            val r = regions.getJSONObject(k)
            mem.raw(MemoryMap.num(r.getString("addr")), hex(r.getString("hex")))
        }
        return mem
    }

    /** SHA-256 (hex) of ARGB pixels as RGBA bytes, like the Python side hashes its RGBA. */
    private fun sha(argb: IntArray): String {
        val b = ByteArray(argb.size * 4)
        for ((k, c) in argb.withIndex()) {
            b[4 * k] = (c shr 16).toByte()
            b[4 * k + 1] = (c shr 8).toByte()
            b[4 * k + 2] = c.toByte()
            b[4 * k + 3] = (c ushr 24).toByte()
        }
        return MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }
    }

    // ---- canonical summary (mirrors crosscheck_export.summary) ----

    private fun summary(s: GameState): Map<String, Any?> {
        val phase = when (s.phase) {
            Phase.IN_TOWN -> if (s.playerIndex != null) "in_town" else "visiting"
            Phase.NOT_IN_TOWN -> "not_in_town"
            Phase.WRONG_GAME -> "wrong_game"
            else -> s.phase.name.lowercase()
        }
        val out = LinkedHashMap<String, Any?>()
        out["phase"] = phase
        if (s.phase != Phase.IN_TOWN) return plainMap(out)
        fun extra(key: String) = s.extras.firstOrNull { it.first == key }?.second
        out["town"] = s.town
        out["player_no"] = s.playerNo
        out["clock"] = s.clock?.let {
            listOf(it.year, it.month, it.day, it.weekday, it.hour, it.minute, it.weekdayLabel, it.monthLabel)
        }
        out["weather"] = s.weather
        out["turnips"] = s.turnips.map { listOf(it.label, it.price, it.today) }
        out["villagers"] = s.villagers.map { listOf(it.slot, it.id, it.name) }
        out["town_fruit"] = extra("town fruit")
        if (phase == "in_town") {
            out["player"] = listOf(s.playerIndex, s.playerName, s.playerTown, s.wallet, s.bank, s.loan)
            out["pockets"] = s.pockets.map { listOf(it.id, it.name, it.empty, it.cond) }
            out["equipment"] = extra("equipment")
            out["shirt"] = extra("shirt")
        }
        return plainMap(out)
    }

    // ---- JSON / number normalisation so Int, Long and JSON numbers compare equal ----

    private fun plainMap(m: Map<String, Any?>): Map<String, Any?> = m.mapValues { norm(it.value) }

    private fun norm(v: Any?): Any? = when (v) {
        null, JSONObject.NULL -> null
        is Int -> v.toLong()
        is Long -> v
        is Number -> v.toLong()
        is List<*> -> v.map(::norm)
        is JSONArray -> List(v.length()) { norm(v.get(it)) }
        is JSONObject -> plain(v)
        else -> v
    }

    private fun plain(o: JSONObject): Map<String, Any?> =
        o.keys().asSequence().toList().associateWith { norm(o.get(it)) }

    private fun hex(s: String): ByteArray = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }
}
