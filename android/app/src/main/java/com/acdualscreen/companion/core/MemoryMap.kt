package com.acdualscreen.companion.core

import org.json.JSONArray
import org.json.JSONObject

/** Thrown when the spec is unusable (bad JSON, wrong schema, missing essentials). */
class SpecException(message: String) : Exception(message)

enum class FieldType(val elemSize: Int, val isArray: Boolean = false) {
    U8(1), S8(1), U16(2), S16(2), U32(4), S32(4), F32(4), PTR(4), STR(1),
    U8_ARR(1, true), U16_ARR(2, true), U32_ARR(4, true);

    companion object {
        fun parse(s: String): FieldType? = when (s.trim().lowercase()) {
            "u8" -> U8; "s8" -> S8; "u16" -> U16; "s16" -> S16
            "u32" -> U32; "s32" -> S32; "f32" -> F32; "ptr" -> PTR; "str" -> STR
            "u8[]" -> U8_ARR; "u16[]" -> U16_ARR; "u32[]" -> U32_ARR
            else -> null
        }
    }
}

/**
 * One field from the spec. [offset] is relative to its base (or to the record start for
 * players/villagers); [absolute] is true when the field gives an absolute address instead.
 */
data class Field(
    val key: String,
    val base: String?,
    val offset: Long,
    val type: FieldType,
    val len: Int,
    val count: Int,
    val enumName: String?,
    val mask: Long?,
    val status: String?,
    val note: String?,
    /** The value is an item id (resolve through item_names). */
    val itemId: Boolean = false,
    /** Applied after [mask]: value = (raw and mask) ushr shift (ignored without a mask). */
    val shift: Int = 0,
) {
    /** Number of bytes to read for this field. */
    val byteSize: Int
        get() = when {
            type == FieldType.STR -> len
            type.isArray -> type.elemSize * count
            else -> type.elemSize
        }
}

data class RecordGroup(
    val base: String?,
    val offset: Long,
    val stride: Long,
    val count: Int,
    val fields: List<Field>,
) {
    fun field(key: String): Field? = fields.firstOrNull { it.key == key }
}

data class ItemRange(
    val idMin: Int,
    val idMax: Int,
    val tableAddr: Long,
    val shift: Int,
    val entryLen: Int,
    val note: String?,
)

/** 16 BE32 pointers that must match before item-name tables are trusted. */
data class RuntimeCheck(val pointerTableAddr: Long, val expected: List<Long>)

data class ItemNames(
    val entryLen: Int,
    val status: String?,
    val ranges: List<ItemRange>,
    val emptyIds: Set<Int>,
    val check: RuntimeCheck? = null,
) {
    fun rangeFor(id: Int): ItemRange? = ranges.firstOrNull { id in it.idMin..it.idMax }
}

/**
 * extras.npc_name_cache: the game's last looked-up villager name, harvested at runtime.
 * [dmaAddr] / [dmaLen]: the name-lookup buffer (8 names of ids b..b+7), when the spec gives it.
 */
data class NpcNameCache(
    val addr: Long,
    val idField: Field,
    val nameField: Field,
    val typeField: Field? = null,
    val dmaAddr: Long? = null,
    val dmaLen: Int = 0,
) {
    /** Start of the one contiguous read covering the buffer and the cache. */
    val readStart: Long get() = if (dmaAddr != null && addr - dmaAddr in 1..256) dmaAddr else addr
    val readLen: Int get() = (addr - readStart).toInt() + maxOf(idField.offset + idField.byteSize,
        nameField.offset + nameField.byteSize, (typeField?.let { it.offset + it.byteSize } ?: 0L)).toInt()
}

/**
 * extras.daily: the buried-item grid (fossils) and each player's glowing spot. Addresses are
 * absolute; see the spec note for the layout.
 */
data class DailySpec(
    val fgAddr: Long,
    val blockBytes: Int,
    val blocksX: Int,
    val blocksZ: Int,
    val units: Int,
    val depositAddr: Long,
    val fossilItem: Int,
    val fossilDailyMax: Int,
    val shineSpotItem: Int,
    val shinePosAddr: Long,
    val shinePosStride: Int,
)

data class GameInfo(val id: String, val idAddr: Long, val revision: Int, val revisionAddr: Long)

/**
 * Parsed form of spec/ac_memory_map.json (schema 1). Parsing is lenient about individual
 * fields (unknown types or bases become [warnings]) but strict about the essentials the
 * validity rules need (game id, bases, charmap).
 */
class MemoryMap(
    val schema: Int,
    val game: GameInfo,
    val bases: Map<String, Long>,
    val globals: List<Field>,
    val players: RecordGroup?,
    val villagers: RecordGroup?,
    val enums: Map<String, Map<Long, String>>,
    val charmap: List<String>,
    val itemNames: ItemNames?,
    val validityRules: List<String>,
    val warnings: List<String>,
    /** scene_no values that also run play_main but are not real gameplay (title demo etc.). */
    val notInTownScenes: Set<Long> = emptySet(),
    val npcNameCache: NpcNameCache? = null,
    /** The "map" section (town map); null when the spec has none or it did not parse. */
    val townMap: MapSpec? = null,
    /** mHm_hs_c homes[4] (house size, basement, statue); null when the spec has none. */
    val homes: RecordGroup? = null,
    /** villagers.memories: Anmmem_c inside each villager record (offset relative to the record). */
    val memories: RecordGroup? = null,
    val daily: DailySpec? = null,
) {
    fun global(key: String): Field? = globals.firstOrNull { it.key == key }

    fun base(name: String?): Long? = if (name == null) 0L else bases[name]

    /** Absolute address of a global field, or null if its base is unknown. */
    fun globalAddr(f: Field): Long? = base(f.base)?.let { it + f.offset }

    /** Absolute address of the start of record [index] in [group]. */
    fun recordAddr(group: RecordGroup, index: Int): Long? =
        base(group.base)?.let { it + group.offset + group.stride * index }

    fun enumLabel(enumName: String?, value: Long): String? =
        enumName?.let { enums[it]?.get(value) }

    fun summary(): String = buildString {
        append("schema $schema, ${game.id} rev ${game.revision}, ")
        append("${globals.size} globals, ")
        append("${players?.fields?.size ?: 0} player fields, ")
        append("${villagers?.fields?.size ?: 0} villager fields, ")
        append("${itemNames?.ranges?.size ?: 0} item-name ranges")
        if (townMap != null) append(", town map")
        if (warnings.isNotEmpty()) append(", ${warnings.size} warning(s)")
    }

    companion object {
        const val SUPPORTED_SCHEMA = 1
        private const val CHARMAP_SIZE = 256

        fun parse(text: String): MemoryMap {
            val root = try {
                JSONObject(text)
            } catch (e: Exception) {
                throw SpecException("spec is not valid JSON: ${e.message}")
            }
            val warnings = ArrayList<String>()

            val schema = root.optInt("schema", -1)
            if (schema != SUPPORTED_SCHEMA) throw SpecException("unsupported spec schema $schema")

            val g = root.optJSONObject("game") ?: throw SpecException("spec has no 'game'")
            val game = GameInfo(
                id = g.getString("id"),
                idAddr = num(g.get("id_addr")),
                revision = num(g.opt("revision") ?: 0).toInt(),
                revisionAddr = num(g.get("revision_addr")),
            )

            val basesObj = root.optJSONObject("bases") ?: throw SpecException("spec has no 'bases'")
            val bases = LinkedHashMap<String, Long>()
            for (k in basesObj.keys()) bases[k] = num(basesObj.get(k))
            for (required in listOf("common_data", "gamePT", "play_main")) {
                if (required !in bases) throw SpecException("spec bases lack '$required'")
            }

            val globals = parseFields(root.optJSONArray("globals"), "globals", bases, warnings)
            val players = parseGroup(root.optJSONObject("players"), "players", bases, warnings)
            val villagers = parseGroup(root.optJSONObject("villagers"), "villagers", bases, warnings)

            val enums = LinkedHashMap<String, Map<Long, String>>()
            root.optJSONObject("enums")?.let { e ->
                for (name in e.keys()) {
                    val m = LinkedHashMap<Long, String>()
                    val obj = e.optJSONObject(name) ?: continue
                    for (k in obj.keys()) {
                        val v = runCatching { num(k) }.getOrNull()
                        if (v == null) warnings += "enum $name: bad key '$k'" else m[v] = obj.optString(k)
                    }
                    enums[name] = m
                }
            }

            val cm = root.optJSONArray("charmap") ?: throw SpecException("spec has no 'charmap'")
            val charmap = ArrayList<String>(CHARMAP_SIZE)
            for (i in 0 until minOf(cm.length(), CHARMAP_SIZE)) charmap += cm.optString(i, "?")
            if (cm.length() != CHARMAP_SIZE) warnings += "charmap has ${cm.length()} entries, expected 256"
            while (charmap.size < CHARMAP_SIZE) charmap += "?"

            val itemNames = root.optJSONObject("item_names")?.let { parseItemNames(it, warnings) }

            val rules = ArrayList<String>()
            val scenes = HashSet<Long>()
            root.optJSONObject("validity")?.let { v ->
                v.optJSONArray("rules")?.let { a -> for (i in 0 until a.length()) rules += a.optString(i) }
                v.optJSONArray("not_in_town_scenes")?.let { a -> for (i in 0 until a.length()) scenes += num(a.get(i)) }
            }

            val npcCache = root.optJSONObject("extras")?.optJSONObject("npc_name_cache")?.let { o ->
                val fields = parseFields(o.optJSONArray("fields"), "npc_name_cache", bases, warnings)
                val id = fields.firstOrNull { it.key == "npc_id" }
                val name = fields.firstOrNull { it.key == "name" && it.type == FieldType.STR }
                val type = fields.firstOrNull { it.key == "npc_type" }
                val dma = if (o.has("dma_area_addr")) num(o.get("dma_area_addr")) else null
                val dmaLen = if (dma != null) num(o.opt("dma_area_len") ?: 0).toInt() else 0
                if (o.has("addr") && id != null && name != null) NpcNameCache(num(o.get("addr")), id, name, type, dma, dmaLen)
                else null.also { warnings += "npc_name_cache: needs addr, npc_id and name" }
            }

            val homes = root.optJSONObject("homes")?.let { parseGroup(it, "homes", bases, warnings) }
            val memories = root.optJSONObject("villagers")?.optJSONObject("memories")?.let {
                parseGroup(it, "villagers.memories", bases, warnings)
            }
            val daily = root.optJSONObject("extras")?.optJSONObject("daily")?.let { d ->
                try {
                    DailySpec(
                        fgAddr = num(d.get("fg_addr")),
                        blockBytes = num(d.get("fg_block_bytes")).toInt(),
                        blocksX = num(d.get("fg_blocks_x")).toInt(),
                        blocksZ = num(d.get("fg_blocks_z")).toInt(),
                        units = num(d.get("units")).toInt(),
                        depositAddr = num(d.get("deposit_addr")),
                        fossilItem = num(d.get("fossil_item")).toInt(),
                        fossilDailyMax = num(d.get("fossil_daily_max")).toInt(),
                        shineSpotItem = num(d.get("shine_spot_item")).toInt(),
                        shinePosAddr = num(d.get("shine_pos_addr")),
                        shinePosStride = num(d.get("shine_pos_stride")).toInt(),
                    ).takeIf { it.units in 1..16 && it.blockBytes >= it.units * it.units * 2 && it.blocksX > 0 && it.blocksZ > 0 }
                        ?: null.also { warnings += "extras.daily: inconsistent sizes" }
                } catch (e: Exception) {
                    warnings += "extras.daily: ${e.message}"
                    null
                }
            }

            val townMap = root.optJSONObject("map")?.let { m ->
                try {
                    MapSpec.parse(m, bases)
                } catch (e: Exception) {
                    warnings += "map: ${e.message}"
                    null
                }
            }

            return MemoryMap(schema, game, bases, globals, players, villagers, enums, charmap,
                itemNames, rules, warnings, scenes, npcCache, townMap, homes, memories, daily)
        }

        private fun parseGroup(
            obj: JSONObject?, where: String, bases: Map<String, Long>, warnings: MutableList<String>,
        ): RecordGroup? {
            if (obj == null) {
                warnings += "no '$where' section"
                return null
            }
            val base = obj.optString("base", "").ifEmpty { null }
            if (base != null && base !in bases) {
                warnings += "$where: unknown base '$base'"
                return null
            }
            val stride = num(obj.get("stride"))
            val count = num(obj.get("count")).toInt()
            if (stride <= 0 || count <= 0) {
                warnings += "$where: bad stride/count"
                return null
            }
            return RecordGroup(
                base = base,
                offset = num(obj.opt("offset") ?: 0),
                stride = stride,
                count = count,
                fields = parseFields(obj.optJSONArray("fields"), where, bases, warnings),
            )
        }

        private fun parseFields(
            arr: JSONArray?, where: String, bases: Map<String, Long>, warnings: MutableList<String>,
        ): List<Field> {
            if (arr == null) return emptyList()
            val out = ArrayList<Field>()
            for (i in 0 until arr.length()) {
                val o = arr.optJSONObject(i) ?: continue
                val key = o.optString("key", "")
                if (key.isEmpty()) { warnings += "$where[$i]: no key"; continue }
                val type = FieldType.parse(o.optString("type", ""))
                if (type == null) { warnings += "$where.$key: unknown type '${o.optString("type")}'"; continue }
                // Either base + offset, or an absolute "addr".
                var base: String? = o.optString("base", "").ifEmpty { null }
                val offset: Long
                if (o.has("addr")) {
                    base = null
                    offset = num(o.get("addr"))
                } else {
                    offset = num(o.opt("offset") ?: 0)
                }
                if (base != null && base !in bases) {
                    warnings += "$where.$key: unknown base '$base'"; continue
                }
                val len = if (o.has("len")) num(o.get("len")).toInt() else 0
                val count = if (o.has("count")) num(o.get("count")).toInt() else 1
                if (type == FieldType.STR && len <= 0) { warnings += "$where.$key: str without len"; continue }
                if (type.isArray && count <= 0) { warnings += "$where.$key: array without count"; continue }
                out += Field(
                    key = key,
                    base = base,
                    offset = offset,
                    type = type,
                    len = len,
                    count = count,
                    enumName = o.optString("enum", "").ifEmpty { null },
                    mask = if (o.has("mask")) num(o.get("mask")) else null,
                    status = o.optString("status", "").ifEmpty { null },
                    note = o.optString("note", "").ifEmpty { null },
                    itemId = o.optBoolean("item_id", false),
                    shift = if (o.has("shift")) num(o.get("shift")).toInt() else 0,
                )
            }
            return out
        }

        private fun parseItemNames(o: JSONObject, warnings: MutableList<String>): ItemNames {
            val entryLen = num(o.opt("entry_len") ?: 16).toInt()
            val ranges = ArrayList<ItemRange>()
            o.optJSONArray("ranges")?.let { a ->
                for (i in 0 until a.length()) {
                    val r = a.optJSONObject(i) ?: continue
                    try {
                        ranges += ItemRange(
                            idMin = num(r.get("id_min")).toInt(),
                            idMax = num(r.get("id_max")).toInt(),
                            tableAddr = num(r.get("table_addr")),
                            shift = num(r.opt("shift") ?: 0).toInt(),
                            entryLen = if (r.has("entry_len")) num(r.get("entry_len")).toInt() else entryLen,
                            note = r.optString("note", "").ifEmpty { null },
                        )
                    } catch (e: Exception) {
                        warnings += "item_names.ranges[$i]: ${e.message}"
                    }
                }
            }
            val empty = HashSet<Int>()
            val emptyArr = o.optJSONArray("empty_ids")
            if (emptyArr != null) {
                for (i in 0 until emptyArr.length()) empty += num(emptyArr.get(i)).toInt()
            } else {
                empty += 0
            }
            val check = o.optJSONObject("runtime_check")?.let { c ->
                val exp = c.optJSONArray("expected")
                if (!c.has("pointer_table_addr") || exp == null || exp.length() == 0) null
                else RuntimeCheck(num(c.get("pointer_table_addr")), List(exp.length()) { num(exp.get(it)) })
            }
            return ItemNames(entryLen, o.optString("status", "").ifEmpty { null }, ranges, empty, check)
        }

        /** Accepts JSON numbers, hex strings ("0x1F") and decimal strings. */
        fun num(v: Any?): Long = when (v) {
            is Number -> v.toLong()
            is String -> {
                val s = v.trim()
                val neg = s.startsWith("-")
                val body = s.removePrefix("-")
                val n = if (body.startsWith("0x", ignoreCase = true)) body.substring(2).toLong(16)
                else body.toLong()
                if (neg) -n else n
            }
            else -> throw SpecException("expected a number, got $v")
        }
    }
}
