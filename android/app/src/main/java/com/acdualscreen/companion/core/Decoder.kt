package com.acdualscreen.companion.core

import java.io.IOException

/** One emulated-memory read: [size] bytes at GameCube address [addr]. */
data class ReadReq(val addr: Long, val size: Int)

/** Something that can read emulated memory in batches (the EmuLink client, or a test fake). */
fun interface MemoryReader {
    /**
     * Returns one entry per request, in order. An entry is null when the emulator reported the
     * range as invalid. Throws [IOException] when the emulator cannot be reached.
     */
    @Throws(IOException::class)
    fun read(reqs: List<ReadReq>): List<ByteArray?>
}

/**
 * Turns raw big-endian memory into a [GameState], driven entirely by the [MemoryMap].
 * Pure Kotlin: no Android dependencies, so it is covered by JVM unit tests.
 *
 * Each [poll] does two small batch reads (all fixed addresses, each read twice; then a re-check
 * of the GAME pointer) plus, only when new item ids appear, one read of item-name table entries.
 *
 * Validity rules (spec "validity"): right game + revision; GAME exec == play_main; scene not in
 * not_in_town_scenes; now_private inside the players array (3a), else a visitor only when
 * player_no == 4, now_private is a MEM1 pointer and (town_id & 0xFF00) == 0x3000 (3b), else not
 * in town (3c); that player's exists == 1; villager ids (id & 0xF000) == 0xE000; every fixed
 * read is taken twice and kept only when both copies agree; impossible values are dropped.
 */
class Decoder(
    private val map: MemoryMap,
    /** Every villager's name from the game's name table (npc id -> name), bundled with the app. */
    private val bundledNames: Map<Int, String> = emptyMap(),
) {

    companion object {
        const val PTR_MIN = 0x80000000L
        const val PTR_MAX = 0x817FFFFFL
        const val MAX_NAME_READS_PER_POLL = 64
        const val EMPTY_SLOT = "—" // em dash
        const val MAX_WALLET = 99_999L
        const val MAX_TURNIP = 2000L
        /** player_no of a visitor from another town (mPr_FOREIGNER). */
        const val PLAYER_NO_FOREIGNER = 4L

        /** All 0x00 or all 0xFF: cleared or unmapped memory, not a real string. */
        fun isBlank(b: ByteArray?): Boolean =
            b == null || b.isEmpty() || b.all { it == 0.toByte() } || b.all { it == 0xFF.toByte() }

        const val NAME_LEN = 8
        const val NAME_PUNCT = " '.-&!"
        /** npc_name_cache.npc_type of a villager (mNpc_NAME_TYPE_NPC). */
        const val NPC_NAME_TYPE_VILLAGER = 1L

        /** Keys rendered by dedicated UI; any other global ends up in [GameState.extras]. */
        val KNOWN_GLOBALS = setOf(
            "scene_no", "player_no", "now_private", "town_name",
            "rtc_sec", "rtc_min", "rtc_hour", "rtc_day", "rtc_weekday", "rtc_month", "rtc_year",
            "weather", "weather_intensity", "kabu_prices",
        )
        val PLAYER_KEYS = setOf("name", "town_name", "exists", "pockets", "item_conditions", "wallet", "loan", "bank")
        val VILLAGER_KEYS = setOf("npc_id", "name")
        /** Player fields read for the info page (they also stay in [GameState.extras]). */
        val INFO_PLAYER_KEYS = setOf(
            "player_id", "birthday_month", "birthday_day",
            "fortune_day", "fortune_month", "fortune_year", "fortune_type",
        )
        val HOME_KEYS = setOf("owner_player_id", "has_basement", "house_size", "house_next_size")

        /** Extras shown first (the panel only has room for a few). */
        val EXTRA_PRIORITY = listOf("town_fruit", "time_season", "kabu_trade_market", "equipment", "shirt")

        fun isPtr(p: Long?): Boolean = p != null && p in PTR_MIN..PTR_MAX

        fun isVillagerId(id: Int): Boolean = (id and 0xF000) == 0xE000

        fun be16(b: ByteArray, off: Int = 0): Int =
            ((b[off].toInt() and 0xFF) shl 8) or (b[off + 1].toInt() and 0xFF)

        fun be32(b: ByteArray, off: Int = 0): Long =
            ((b[off].toLong() and 0xFF) shl 24) or ((b[off + 1].toLong() and 0xFF) shl 16) or
                ((b[off + 2].toLong() and 0xFF) shl 8) or (b[off + 3].toLong() and 0xFF)
    }

    // ---- caches tied to one game image (cleared when the boot.dol hash changes) ----
    private val nameCache = HashMap<Int, String?>()
    private var namesVerified: Boolean? = null // null = not checked yet
    private val villagerNames = HashMap<Int, String>()
    private var lastNpcPair: Map<Int, String>? = null
    private var cacheKey: String? = null

    /** Call with every EmuLink handshake; a different game image invalidates cached names. */
    fun onHello(gameId: String, gameHash: String) {
        val key = "$gameId/$gameHash"
        if (key != cacheKey) {
            nameCache.clear()
            namesVerified = null
            villagerNames.clear()
            lastNpcPair = null
            cacheKey = key
        }
    }

    val cachedNameCount: Int get() = nameCache.size

    /** "gameId/gameHash" of the running image (null before the first handshake). */
    val gameKey: String? get() = cacheKey

    /** Villager names learned so far (npc id -> name); the ids map to a fixed table in the game. */
    fun learnedNames(): Map<Int, String> = HashMap(villagerNames)

    /** Names saved from an earlier session of the same game image (call after [onHello]). */
    fun preloadNames(names: Map<Int, String>) {
        for ((id, n) in names) if (isVillagerId(id) && n.isNotBlank()) villagerNames.putIfAbsent(id, n)
    }

    // ---- round-1 read plan: every fixed address, computed once ----
    private val plan = ArrayList<ReadReq>()
    private val idxGameId: Int
    private val idxRevision: Int
    private val idxGamePT: Int
    private val idxNpcSpan: Int
    private val globalIdx = LinkedHashMap<String, Int>()
    private val playerIdx = ArrayList<Map<String, Int>>()
    private val villagerIdx = ArrayList<Map<String, Int>>()
    private val homeIdx = ArrayList<Map<String, Int>>()
    private val gamePT = map.bases.getValue("gamePT")
    private val playMain = map.bases.getValue("play_main")

    private fun add(addr: Long, size: Int): Int {
        plan += ReadReq(addr, size)
        return plan.size - 1
    }

    init {
        idxGameId = add(map.game.idAddr, map.game.id.length)
        idxRevision = add(map.game.revisionAddr, 1)
        idxGamePT = add(gamePT, 4)
        for (f in map.globals) {
            val a = map.globalAddr(f) ?: continue
            globalIdx[f.key] = add(a, f.byteSize)
        }
        // Only the record fields the panel uses are read (the spec defines many more).
        fun planGroup(g: RecordGroup?, out: MutableList<Map<String, Int>>, keep: (Field) -> Boolean) {
            if (g == null) return
            for (i in 0 until g.count) {
                val rec = map.recordAddr(g, i) ?: return
                // Bitfields that share a byte (house_size / house_next_size) share one read.
                val seen = HashMap<Pair<Long, Int>, Int>()
                out += g.fields.filter(keep).associate {
                    it.key to seen.getOrPut(it.offset to it.byteSize) { add(rec + it.offset, it.byteSize) }
                }
            }
        }
        planGroup(map.players, playerIdx) { it.key in PLAYER_KEYS || it.key in INFO_PLAYER_KEYS || it.itemId }
        planGroup(map.homes, homeIdx) { it.key in HOME_KEYS }
        planGroup(map.villagers, villagerIdx) { it.key in VILLAGER_KEYS }
        val npc = map.npcNameCache
        // One read for the lookup buffer and the cache together, so they are seen at one moment.
        idxNpcSpan = if (npc != null) add(npc.readStart, npc.readLen) else -1
    }

    val planSize: Int get() = plan.size

    // ---- decoding helpers ----

    /** Decodes one field: Long for integers/pointers, Float, String, or LongArray. */
    fun value(f: Field, b: ByteArray?): Any? {
        if (b == null || b.size < f.byteSize) return null
        fun masked(v: Long) = if (f.mask != null) (v and f.mask) ushr f.shift else v
        return when (f.type) {
            FieldType.U8 -> masked((b[0].toLong() and 0xFF))
            FieldType.S8 -> masked(b[0].toLong())
            FieldType.U16 -> masked(be16(b).toLong())
            FieldType.S16 -> masked(be16(b).toShort().toLong())
            FieldType.U32, FieldType.PTR -> masked(be32(b))
            FieldType.S32 -> masked(be32(b).toInt().toLong())
            FieldType.F32 -> Float.fromBits(be32(b).toInt())
            FieldType.STR -> decodeStr(b, 0, f.len)
            FieldType.U8_ARR, FieldType.U16_ARR, FieldType.U32_ARR -> LongArray(f.count) { i ->
                val o = i * f.type.elemSize
                masked(when (f.type) {
                    FieldType.U8_ARR -> b[o].toLong() and 0xFF
                    FieldType.U16_ARR -> be16(b, o).toLong()
                    else -> be32(b, o)
                })
            }
        }
    }

    /**
     * Decodes game text through the charmap. Every byte is a glyph (0x00-0x1F included), so there
     * is no terminator: names are space-padded and only trailing spaces are trimmed.
     */
    fun decodeStr(b: ByteArray, off: Int, len: Int): String {
        val sb = StringBuilder(len)
        for (i in off until minOf(b.size, off + len)) sb.append(map.charmap[b[i].toInt() and 0xFF])
        return sb.toString().trimEnd(' ')
    }

    private fun label(f: Field?, v: Long?): String? {
        if (f == null || v == null) return null
        return map.enumLabel(f.enumName, v) ?: v.toString()
    }

    private fun itemName(id: Int): String? = if (namesVerified == false) null else nameCache[id]

    private fun isEmptyItem(id: Int) = id in (map.itemNames?.emptyIds ?: setOf(0))

    private fun format(f: Field, v: Any?): String = when {
        v == null -> "?"
        v is LongArray -> v.joinToString(" ")
        v is Long && f.itemId -> {
            val id = v.toInt()
            if (isEmptyItem(id)) EMPTY_SLOT else itemName(id) ?: "0x%04X".format(id)
        }
        v is Long -> map.enumLabel(f.enumName, v)
            ?: if (f.type == FieldType.PTR) "0x%08X".format(v) else v.toString()
        else -> v.toString()
    }

    private fun ascii(b: ByteArray): String =
        String(CharArray(b.size) { i -> (b[i].toInt() and 0xFF).let { if (it in 0x20..0x7E) it.toChar() else '?' } })

    /** Entries that still disagreed after the re-read in the last poll (shown as unknown). */
    var unstableReads: Int = 0
        private set

    /** Validity rule 8 (see the top-level [readStable]); records [unstableReads]. */
    @Throws(IOException::class)
    private fun readChecked(reader: MemoryReader, reqs: List<ReadReq>): List<ByteArray?> {
        val r = readStable(reader, reqs)
        unstableReads = r.unstable
        return r.values
    }

    /** Rule 3(b): a visitor from another town, as opposed to common_data being rebuilt (3c). */
    private fun isVisiting(g: Map<String, Any?>, nowPrivate: Long?): Boolean {
        if (g["player_no"] as? Long != PLAYER_NO_FOREIGNER || !isPtr(nowPrivate)) return false
        if (map.global("town_id") == null) return true // spec without town_id: skip that test
        val townId = g["town_id"] as? Long ?: return false
        return (townId and 0xFF00L) == 0x3000L
    }

    // ---- the poll ----

    @Throws(IOException::class)
    fun poll(reader: MemoryReader): GameState {
        val r1 = readChecked(reader, plan)
        val g = HashMap<String, Any?>()
        for ((k, i) in globalIdx) g[k] = value(map.global(k)!!, r1[i])
        val sceneNo = g["scene_no"] as? Long
        val scene = label(map.global("scene_no"), sceneNo)

        // Rule 1: right game and revision.
        val idBytes = r1[idxGameId] ?: return GameState.of(Phase.WRONG_GAME, "No game memory")
        val id = ascii(idBytes)
        if (id != map.game.id) {
            return GameState(Phase.WRONG_GAME, "Running $id, panel needs ${map.game.id}", gameId = id)
        }
        val rev = r1[idxRevision]?.let { it[0].toInt() and 0xFF }
        if (rev != map.game.revision) {
            return GameState(Phase.WRONG_GAME, "$id revision $rev, panel needs revision ${map.game.revision}", gameId = id)
        }

        // Rule 2: GAME* points at a play-scene GAME (exec == play_main). The pointer is read again
        // alongside exec so a scene change between the two reads cannot be misread as gameplay.
        val notInTown = GameState(Phase.NOT_IN_TOWN, "Not in town", gameId = id, scene = scene)
        val p = r1[idxGamePT]?.let { be32(it) }
        if (!isPtr(p)) return notInTown
        val r2 = reader.read(listOf(ReadReq(gamePT, 4), ReadReq(p!! + 4, 4)))
        val p2 = r2[0]?.let { be32(it) }
        val exec = r2[1]?.let { be32(it) }
        if (p2 != p || exec != playMain) return notInTown

        // Rule 5: title demo / intro scenes also run play_main.
        if (map.global("scene_no") != null && (sceneNo == null || sceneNo in map.notInTownScenes)) return notInTown

        // Rule 3: which local player (if any) is active.
        val players = map.players
        val nowPrivate = g["now_private"] as? Long
        var index: Int? = null
        if (players != null && nowPrivate != null) {
            val start = map.recordAddr(players, 0)
            if (start != null && nowPrivate >= start && nowPrivate < start + players.stride * players.count &&
                (nowPrivate - start) % players.stride == 0L
            ) {
                index = ((nowPrivate - start) / players.stride).toInt()
            }
        }
        // Rule 3(c): not a resident slot and not a valid visitor (NULL/torn pointer, or the game
        // zeroing and rebuilding common_data while it loads a foreign save).
        if (index == null && !isVisiting(g, nowPrivate)) {
            return GameState(Phase.NOT_IN_TOWN, "Not in town (player data not ready)", gameId = id, scene = scene)
        }

        // Rule 4: a local player's record must exist (exists == 1).
        var pv: Map<String, Any?> = emptyMap()
        if (index != null && players != null) {
            pv = playerIdx.getOrNull(index)?.mapValues { (k, i) -> value(players.field(k)!!, r1[i]) } ?: emptyMap()
            if (players.field("exists") != null && pv["exists"] != 1L) return notInTown
        }

        harvestVillagerName(r1)

        // Item names for pockets and item-id extras, fetched only for ids not seen before.
        val pocketIds = (pv["pockets"] as? LongArray)?.map { it.toInt() } ?: emptyList()
        val extraFields = map.globals.filter { it.key !in KNOWN_GLOBALS }.map { it to g[it.key] } +
            (players?.fields?.filter { it.key !in PLAYER_KEYS && it.key in pv }?.map { it to pv[it.key] } ?: emptyList())
        val extraIds = extraFields.filter { it.first.itemId }.mapNotNull { (it.second as? Long)?.toInt() }
        resolveNames(reader, pocketIds + extraIds)

        val clock = clockOf(g)
        val status = when {
            index == null -> "Visiting / foreign player"
            namesVerified == false -> "In town (item-name tables did not verify)"
            else -> "In town"
        }
        val extras = extraFields
            .sortedBy { (f, _) -> EXTRA_PRIORITY.indexOf(f.key).let { if (it < 0) Int.MAX_VALUE else it } }
            .map { (f, v) -> f.key.replace('_', ' ') to format(f, v) }

        return GameState(
            phase = Phase.IN_TOWN,
            status = status,
            gameId = id,
            town = g["town_name"] as? String,
            scene = scene,
            playerNo = (g["player_no"] as? Long)?.toInt(),
            playerIndex = index,
            playerName = pv["name"] as? String,
            playerTown = pv["town_name"] as? String,
            clock = clock,
            weather = weatherOf(g),
            wallet = (pv["wallet"] as? Long)?.takeIf { it <= MAX_WALLET },
            bank = pv["bank"] as? Long,
            loan = pv["loan"] as? Long,
            loanState = if (index != null) loanStateOf(index, pv, g, r1) else null,
            heldItem = if (index != null) heldItemOf(pv) else null,
            birthday = if (index != null) birthdayOf(pv) else null,
            fortune = if (index != null) fortuneOf(pv, clock) else null,
            fruit = Fruit.fromItemId((g["town_fruit"] as? Long)?.toInt()),
            season = label(map.global("time_season"), g["time_season"] as? Long),
            pockets = if (index != null) pocketsOf(pocketIds, pv["item_conditions"] as? Long ?: 0L) else emptyList(),
            turnips = turnipsOf(g, clock),
            villagers = villagersOf(r1),
            extras = extras,
        )
    }

    private fun heldItemOf(pv: Map<String, Any?>): String? {
        val id = (pv["equipment"] as? Long)?.toInt() ?: return null
        if (isEmptyItem(id)) return null
        return itemName(id) ?: "0x%04X".format(id)
    }

    private fun birthdayOf(pv: Map<String, Any?>): Pair<Int, Int>? {
        val m = (pv["birthday_month"] as? Long)?.toInt() ?: return null
        val d = (pv["birthday_day"] as? Long)?.toInt() ?: return null
        return if (m in 1..12 && d in 1..31) m to d else null
    }

    /** The fortune counts only on the day it was received. */
    private fun fortuneOf(pv: Map<String, Any?>, clock: Clock?): String? {
        if (clock == null) return null
        val y = (pv["fortune_year"] as? Long)?.toInt()
        val m = (pv["fortune_month"] as? Long)?.toInt()
        val d = (pv["fortune_day"] as? Long)?.toInt()
        if (y != clock.year || m != clock.month || d != clock.day) return null
        return label(map.players?.field("fortune_type"), pv["fortune_type"] as? Long)
    }

    /**
     * Loan > 0: paying. Loan 0: paid off once the house has grown (or an upgrade or basement is
     * under way); on an untouched starter house there has been no loan yet.
     */
    private fun loanStateOf(index: Int, pv: Map<String, Any?>, g: Map<String, Any?>, r1: List<ByteArray?>): LoanState? {
        val loan = pv["loan"] as? Long ?: return null
        if (loan > 0) return LoanState.PAYING
        val homes = map.homes ?: return LoanState.PAID_OFF
        val arrangement = g["house_arrangement"] as? Long ?: return LoanState.PAID_OFF
        val hi = ((arrangement shr (2 * index)) and 3L).toInt()
        val idx = homeIdx.getOrNull(hi) ?: return LoanState.PAID_OFF
        val hv = idx.mapValues { (k, i) -> value(homes.field(k)!!, r1[i]) as? Long }
        val ownerId = hv["owner_player_id"]
        val playerId = pv["player_id"] as? Long
        if (ownerId != null && playerId != null && ownerId != playerId) return LoanState.PAID_OFF
        val grown = (hv["house_size"] ?: 0L) > 0 || (hv["house_next_size"] ?: 0L) > 0 || (hv["has_basement"] ?: 0L) != 0L
        return if (grown) LoanState.PAID_OFF else LoanState.NONE
    }

    private fun clockOf(g: Map<String, Any?>): Clock? {
        fun i(k: String) = (g[k] as? Long)?.toInt()
        val year = i("rtc_year") ?: return null
        val month = i("rtc_month") ?: return null
        val day = i("rtc_day") ?: return null
        val hour = i("rtc_hour") ?: return null
        val minute = i("rtc_min") ?: return null
        if (month !in 1..12 || day !in 1..31 || hour !in 0..23 || minute !in 0..59) return null // torn/bad read
        val wd = i("rtc_weekday")?.takeIf { it in 0..6 } ?: -1 // -1 = unknown (rule 8)
        val wdField = map.global("rtc_weekday")
        val monthLabel = map.enumLabel(map.global("rtc_month")?.enumName, month.toLong())
        return Clock(year, month, day, wd, map.enumLabel(wdField?.enumName, wd.toLong()), hour, minute, monthLabel)
    }

    private fun weatherOf(g: Map<String, Any?>): String? {
        val w = g["weather"] as? Long ?: return null
        val base = label(map.global("weather"), w)
        val intensity = g["weather_intensity"] as? Long
        if (w == 0L || intensity == null || intensity == 0L) return base
        val f = map.global("weather_intensity")
        val il = map.enumLabel(f?.enumName, intensity) ?: "level $intensity"
        return "$base ($il)"
    }

    private fun turnipsOf(g: Map<String, Any?>, clock: Clock?): List<Turnip> {
        val prices = g["kabu_prices"] as? LongArray ?: return emptyList()
        val wdEnum = map.global("rtc_weekday")?.enumName ?: "weekday"
        return prices.mapIndexed { i, v ->
            val label = map.enumLabel(wdEnum, i.toLong()) ?: "D$i"
            Turnip(label, v.takeIf { it <= MAX_TURNIP }?.toInt(), clock?.weekday == i)
        }
    }

    private fun villagersOf(r1: List<ByteArray?>): List<Villager> {
        val group = map.villagers ?: return emptyList()
        val idField = group.field("npc_id") ?: return emptyList()
        val nameField = group.field("name")?.takeIf { it.type == FieldType.STR }
        return villagerIdx.mapIndexedNotNull { slot, idx ->
            val id = (value(idField, r1[idx.getValue("npc_id")]) as? Long)?.toInt() ?: return@mapIndexedNotNull null
            if (!isVillagerId(id)) return@mapIndexedNotNull null
            val name = nameField?.let { value(it, r1[idx.getValue(it.key)]) as? String }?.ifBlank { null }
            Villager(slot, id, name ?: villagerNames[id] ?: bundledNames[id])
        }
    }

    /** A villager name: not blank, and only letters, digits, spaces and ' . - & ! */
    private fun nameOk(raw: ByteArray): String? {
        if (isBlank(raw)) return null
        val s = decodeStr(raw, 0, raw.size)
        if (s.isEmpty() || s.any { !(it.isLetterOrDigit() || it in NAME_PUNCT) }) return null
        return s
    }

    /**
     * Villager names live in ARAM, not MEM1; the game caches the last one it looked up. The
     * cached (id, name) counts only when its npc_type says villager, the name looks like a name
     * and (when the spec gives it) the lookup buffer holds the same name in that id's slot; the
     * buffer's other slots then name the neighbouring ids. Names are learned once two consecutive
     * polls agree (guards against tearing).
     */
    private fun harvestVillagerName(r1: List<ByteArray?>) {
        val npc = map.npcNameCache ?: return
        val span = r1[idxNpcSpan]
        val learned = span?.let { harvestFrom(npc, it) }
        if (learned != null && learned == lastNpcPair) villagerNames.putAll(learned)
        lastNpcPair = learned
    }

    private fun harvestFrom(npc: NpcNameCache, span: ByteArray): Map<Int, String>? {
        val off = (npc.addr - npc.readStart).toInt()
        if (span.size < npc.readLen) return null
        fun field(f: Field): ByteArray = span.copyOfRange(off + f.offset.toInt(), off + f.offset.toInt() + f.byteSize)
        val id = (value(npc.idField, field(npc.idField)) as? Long)?.toInt() ?: return null
        if (!isVillagerId(id)) return null
        npc.typeField?.let { if (value(it, field(it)) != NPC_NAME_TYPE_VILLAGER) return null }
        val nameRaw = field(npc.nameField)
        val name = nameOk(nameRaw) ?: return null
        val out = LinkedHashMap<Int, String>()
        out[id] = name
        if (npc.readStart != npc.addr) {
            val nid = id and 0xFF
            val base = nid and 0xFC
            val k = nid - base
            val slot = span.copyOfRange(k * NAME_LEN, k * NAME_LEN + NAME_LEN)
            if (!slot.contentEquals(nameRaw.copyOf(NAME_LEN))) return null // stale or torn cache
            for (j in 0 until npc.dmaLen / NAME_LEN) {
                if (base + j >= 0xFF) break
                val n = nameOk(span.copyOfRange(j * NAME_LEN, j * NAME_LEN + NAME_LEN)) ?: continue
                out.putIfAbsent(0xE000 or (base + j), n)
            }
        }
        return out
    }

    @Throws(IOException::class)
    private fun resolveNames(reader: MemoryReader, ids: List<Int>) {
        val names = map.itemNames ?: return
        if (namesVerified == false) return
        val check = names.check
        val needCheck = check != null && namesVerified == null
        val missing = ids.distinct()
            .filter { !isEmptyItem(it) && it !in nameCache && names.rangeFor(it) != null }
            .take(MAX_NAME_READS_PER_POLL)
        if (missing.isEmpty()) return

        val reqs = ArrayList<ReadReq>()
        if (needCheck) reqs += ReadReq(check.pointerTableAddr, 4 * check.expected.size)
        for (id in missing) {
            val r = names.rangeFor(id)!!
            reqs += ReadReq(r.tableAddr + ((id - r.idMin).toLong() shr r.shift) * r.entryLen, r.entryLen)
        }
        val res = reader.read(reqs)
        var k = 0
        if (needCheck) {
            val b = res[0]
            namesVerified = b != null && check.expected.indices.all { be32(b, 4 * it) == check.expected[it] }
            k = 1
            if (namesVerified == false) return
        }
        missing.forEachIndexed { i, id ->
            // Rule 9: decode every byte, trim trailing spaces only; a cleared entry has no name.
            val b = res[k + i]
            nameCache[id] = if (isBlank(b)) null else decodeStr(b!!, 0, b.size).ifEmpty { null }
        }
    }

    private fun pocketsOf(ids: List<Int>, conds: Long): List<Pocket> = ids.mapIndexed { slot, id ->
        val empty = isEmptyItem(id)
        val cond = ((conds ushr (slot * 2)) and 3L).toInt()
        Pocket(slot, id, if (empty) null else itemName(id), empty, cond)
    }
}
