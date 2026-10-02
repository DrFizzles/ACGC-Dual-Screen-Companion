package com.acdualscreen.companion.core

import org.json.JSONArray
import org.json.JSONObject

/**
 * Parsed form of the spec's "map" section (spec/README.md section 13): where the town map's acre
 * types, acre textures, building acres, villager-house icons and the player actor live in RAM,
 * and the rules the game's map screen (m_map_ovl.c) applies to them. Every address here is
 * absolute (base + offset already applied).
 */
class MapSpec(
    val grid: Grid,
    val acreTypes: AcreTypes,
    val texture: AcreTexture,
    val buildings: Buildings,
    val houses: VillagerHouses,
    val player: Player,
) {
    class Grid(
        val cols: Int,
        val rows: Int,
        val colLabels: List<String>,
        val rowLabels: List<String>,
        val firstBlockX: Int,
        val firstBlockZ: Int,
        val blockCols: Int,
        val blockRows: Int,
        val acreUnits: Int,
    ) {
        val blockCount: Int get() = blockCols * blockRows
        fun blockIndex(bx: Int, bz: Int) = bz * blockCols + bx
        /** "C-3" style label of block ([bx], [bz]), or null outside the map. */
        fun acreLabel(bx: Int, bz: Int): String? {
            val r = rowLabels.getOrNull(bz - firstBlockZ) ?: return null
            val c = colLabels.getOrNull(bx - firstBlockX) ?: return null
            return "$r-$c"
        }
    }

    class Bridge(val addr: Long, val blockXOffset: Int, val blockZOffset: Int, val flagsOffset: Int, val existsMask: Int) {
        val readSize: Int get() = maxOf(blockXOffset, blockZOffset, flagsOffset) + 1
    }

    class AcreTypes(
        val typeCount: Int,
        val tablePointerAddr: Long,
        val tableExpected: Long,
        val tableCount: Int,
        val combiAddr: Long,
        val combiCount: Int,
        val dataCombiAddr: Long,
        val dataCombiEntryLen: Int,
        val dataCombiCount: Int,
        val fgIdOffset: Int,
        val typeOffset: Int,
        val skipTypes: Set<Int>,
        val padType: Int,
        val bridge: Bridge?,
        val plussBridgeAddr: Long,
        val plussBridgeCount: Int,
        val plussNone: Int,
    )

    class AcreTexture(
        val pointerTableAddr: Long,
        val pointerCount: Int,
        val rangeStart: Long,
        val rangeEnd: Long,
        val format: GcFormat,
        val width: Int,
        val height: Int,
        val size: Int,
        val visX: Int,
        val visY: Int,
        val visW: Int,
        val visH: Int,
        val paletteFormat: TlutFormat,
        val paletteEntries: Int,
        val paletteSize: Int,
        val selectorAddr: Long,
        val selectorCount: Int,
        val palettePointerAddr: Long,
        val palettePointerCount: Int,
        val paletteExpected: List<Long>,
    ) {
        /** start <= ptr < end and (ptr - start) % size == 0 */
        fun validPointer(p: Long): Boolean = p in rangeStart until rangeEnd && (p - rangeStart) % size == 0L
    }

    class Indicator(
        val key: String,
        val label: String,
        val kindMask: Long,
        val blockType: Int,
        val fallbackLabel: String,
        val fallbackColor: Int,
    )

    class Buildings(val kindsPointerAddr: Long, val kindsExpected: Long, val kindsCount: Int, val indicators: List<Indicator>)

    class Tier(
        val tier: Int,
        val displayListAddr: Long,
        val primOffset: Int,
        val envOffset: Int,
        val colorLen: Int,
        /** 0xFFRRGGBB */
        val expectedPrim: Int,
        val expectedEnv: Int,
    )

    class VillagerHouses(
        val iconAddr: Long,
        val iconFormat: GcFormat,
        val iconW: Int,
        val iconH: Int,
        val iconSize: Int,
        val iconUnits: Int,
        val tiers: List<Tier>,
        val houseYAddr: Long,
        val houseYStride: Long,
        val houseYFieldOffset: Long,
        val posListAddr: Long,
        val posEntryLen: Int,
        val posCount: Int,
        val terminator: Int,
        val terminatorIndex: Int,
        val fgNameOffset: Int,
        val slotsOffset: Int,
        val slotCount: Int,
        val slotLen: Int,
        val slotUtX: Int,
        val slotUtZ: Int,
        val slotIdx: Int,
        val xOffsets: List<Int>,
        val yOffsets: List<Int>,
    )

    /** One pointer-chain step: read [type] at [addr], or at the value of step [from] + [offset]. */
    class ChainStep(
        val step: String,
        val addr: Long?,
        val from: String?,
        val offset: Long,
        val type: FieldType,
        val checkMem1: Boolean,
        val equals: Long?,
        val min: Long?,
    )

    class Check(val offset: Long, val type: FieldType, val equals: Long)

    class Player(
        val chain: List<ChainStep>,
        val actorChecks: List<Check>,
        val posX: Long,
        val posY: Long,
        val posZ: Long,
        val facingOffset: Long,
        val fullTurn: Int,
        val worldUnitsPerAcre: Float,
        val mapUnitsPerAcre: Float,
        val showScenes: Set<Long>,
        val fieldTypeAddr: Long?,
        val fieldTypeEquals: Long,
        val blockXMin: Int,
        val blockXMax: Int,
        val blockZMin: Int,
        val blockZMax: Int,
        val markerColor: Int,
        val markerOutline: Int,
        val highlightColor: Int,
        val nextSceneAddr: Long?,
        val exitPositionAddr: Long?,
    ) {
        /** The last chain step: the actor pointer the position and checks are relative to. */
        val actorStep: String get() = chain.last().step
    }

    companion object {
        /** Parses the "map" object. Throws (JSONException / SpecException) when it is unusable. */
        fun parse(o: JSONObject, bases: Map<String, Long>): MapSpec {
            fun num(v: Any?) = MemoryMap.num(v)
            fun int(obj: JSONObject, k: String) = num(obj.get(k)).toInt()
            fun long(obj: JSONObject, k: String) = num(obj.get(k))
            fun addrOf(obj: JSONObject): Long {
                if (obj.has("addr")) return num(obj.get("addr"))
                val base = obj.optString("base", "")
                val b = if (base.isEmpty()) 0L else bases[base] ?: throw SpecException("map: unknown base '$base'")
                return b + num(obj.get("offset"))
            }
            fun ints(a: JSONArray) = List(a.length()) { num(a.get(it)).toInt() }
            fun strings(a: JSONArray) = List(a.length()) { a.getString(it) }
            fun type(obj: JSONObject, k: String = "type") =
                FieldType.parse(obj.getString(k)) ?: throw SpecException("map: unknown type '${obj.getString(k)}'")
            fun rgb(a: JSONArray): Int {
                if (a.length() < 3) throw SpecException("map: colour needs 3 components")
                return GcTexture.argb(255, num(a.get(0)).toInt() and 0xFF, num(a.get(1)).toInt() and 0xFF, num(a.get(2)).toInt() and 0xFF)
            }

            val g = o.getJSONObject("grid")
            val grid = Grid(
                cols = int(g, "cols"), rows = int(g, "rows"),
                colLabels = strings(g.getJSONArray("col_labels")), rowLabels = strings(g.getJSONArray("row_labels")),
                firstBlockX = int(g, "first_block_x"), firstBlockZ = int(g, "first_block_z"),
                blockCols = int(g, "block_cols"), blockRows = int(g, "block_rows"),
                acreUnits = int(g, "acre_map_units"),
            )
            if (grid.cols <= 0 || grid.rows <= 0 || grid.colLabels.size != grid.cols || grid.rowLabels.size != grid.rows) {
                throw SpecException("map.grid: inconsistent size/labels")
            }

            val at = o.getJSONObject("acre_types")
            val table = at.getJSONObject("table")
            val save = at.getJSONObject("from_save")
            val combi = save.getJSONObject("combi_table")
            val dct = save.getJSONObject("data_combi_table")
            val pluss = at.getJSONObject("pluss_bridge")
            val acreTypes = AcreTypes(
                typeCount = int(at, "type_count"),
                tablePointerAddr = long(table, "pointer_addr"),
                tableExpected = long(table, "expected_pointer"),
                tableCount = int(table, "count"),
                combiAddr = addrOf(combi),
                combiCount = int(combi, "count"),
                dataCombiAddr = long(dct, "addr"),
                dataCombiEntryLen = int(dct, "entry_len"),
                dataCombiCount = int(dct, "count"),
                fgIdOffset = int(dct, "fg_id_offset"),
                typeOffset = int(dct, "type_offset"),
                skipTypes = ints(at.getJSONArray("skip_types")).toSet(),
                padType = int(at, "pad_type"),
                bridge = at.optJSONObject("bridge")?.let { b ->
                    Bridge(addrOf(b), int(b, "block_x_offset"), int(b, "block_z_offset"), int(b, "flags_offset"), int(b, "exists_mask"))
                },
                plussBridgeAddr = long(pluss, "addr"),
                plussBridgeCount = int(pluss, "count"),
                plussNone = int(pluss, "none_value"),
            )
            if (acreTypes.tableCount < grid.blockCount || acreTypes.combiCount < grid.blockCount) {
                throw SpecException("map.acre_types: tables smaller than the block grid")
            }

            val tx = o.getJSONObject("acre_texture")
            val ptab = tx.getJSONObject("pointer_table")
            val range = tx.getJSONObject("valid_range")
            val vis = tx.getJSONObject("visible")
            val pal = tx.getJSONObject("palette")
            val sel = pal.getJSONObject("selector_table")
            val ppt = pal.getJSONObject("pointer_table")
            val texture = AcreTexture(
                pointerTableAddr = long(ptab, "addr"),
                pointerCount = int(ptab, "count"),
                rangeStart = long(range, "start"),
                rangeEnd = long(range, "end"),
                format = GcFormat.parse(tx.getString("format")) ?: throw SpecException("map: texture format ${tx.getString("format")}"),
                width = int(tx, "width"),
                height = int(tx, "height"),
                size = int(tx, "size"),
                visX = int(vis, "x"), visY = int(vis, "y"), visW = int(vis, "width"), visH = int(vis, "height"),
                paletteFormat = TlutFormat.parse(pal.getString("format")) ?: throw SpecException("map: palette format"),
                paletteEntries = int(pal, "entries"),
                paletteSize = int(pal, "size"),
                selectorAddr = long(sel, "addr"),
                selectorCount = int(sel, "count"),
                palettePointerAddr = long(ppt, "addr"),
                palettePointerCount = int(ppt, "count"),
                paletteExpected = pal.optJSONArray("expected_pointers")?.let { a -> List(a.length()) { num(a.get(it)) } } ?: emptyList(),
            )
            if (texture.format.byteSize(texture.width, texture.height) != texture.size ||
                texture.visX + texture.visW > texture.width || texture.visY + texture.visH > texture.height ||
                texture.rangeEnd <= texture.rangeStart || texture.size <= 0
            ) {
                throw SpecException("map.acre_texture: inconsistent size")
            }

            val b = o.getJSONObject("buildings")
            val kinds = b.getJSONObject("block_kinds")
            val inds = b.getJSONArray("indicators")
            val buildings = Buildings(
                kindsPointerAddr = long(kinds, "pointer_addr"),
                kindsExpected = long(kinds, "expected_pointer"),
                kindsCount = int(kinds, "count"),
                indicators = List(inds.length()) { i ->
                    val d = inds.getJSONObject(i)
                    val fb = d.optJSONObject("fallback_marker")
                    Indicator(
                        key = d.getString("key"),
                        label = d.optString("label", d.getString("key")),
                        kindMask = long(d, "kind_mask"),
                        blockType = d.optInt("block_type", -1),
                        fallbackLabel = fb?.optString("label", "")?.ifEmpty { null } ?: d.optString("label", ""),
                        fallbackColor = parseColor(fb?.optString("color", "") ?: "") ?: DEFAULT_FALLBACK,
                    )
                },
            )

            val vh = o.getJSONObject("villager_houses")
            val mk = vh.getJSONObject("marker")
            val tiers = vh.getJSONArray("tiers")
            val hy = vh.getJSONObject("tier_rule").getJSONObject("house_y")
            val sr = vh.getJSONObject("slot_rule")
            val hl = sr.getJSONObject("house_pos_list")
            val sf = hl.getJSONObject("slot_fields")
            val houses = VillagerHouses(
                iconAddr = long(mk, "addr"),
                iconFormat = GcFormat.parse(mk.getString("format")) ?: throw SpecException("map: icon format"),
                iconW = int(mk, "width"),
                iconH = int(mk, "height"),
                iconSize = int(mk, "size"),
                iconUnits = int(mk, "map_size_units"),
                tiers = List(tiers.length()) { i ->
                    val t = tiers.getJSONObject(i)
                    Tier(
                        tier = int(t, "tier"),
                        displayListAddr = long(t, "display_list_addr"),
                        primOffset = int(t, "prim_offset"),
                        envOffset = int(t, "env_offset"),
                        colorLen = int(t, "color_len"),
                        expectedPrim = rgb(t.getJSONArray("expected_prim")),
                        expectedEnv = rgb(t.getJSONArray("expected_env")),
                    )
                }.sortedBy { it.tier },
                houseYAddr = addrOf(hy),
                houseYStride = long(hy, "stride"),
                houseYFieldOffset = long(hy, "field_offset"),
                posListAddr = long(hl, "addr"),
                posEntryLen = int(hl, "entry_len"),
                posCount = int(hl, "count"),
                terminator = int(hl, "terminator"),
                terminatorIndex = int(hl, "terminator_index"),
                fgNameOffset = int(hl, "fg_name_offset"),
                slotsOffset = int(hl, "slots_offset"),
                slotCount = int(hl, "slot_count"),
                slotLen = int(hl, "slot_len"),
                slotUtX = int(sf, "ut_x"),
                slotUtZ = int(sf, "ut_z"),
                slotIdx = int(sf, "idx"),
                xOffsets = ints(sr.getJSONArray("x_offsets")),
                yOffsets = ints(sr.getJSONArray("y_offsets")),
            )
            if (houses.iconFormat.byteSize(houses.iconW, houses.iconH) != houses.iconSize || houses.tiers.isEmpty() ||
                houses.xOffsets.size != 3 || houses.yOffsets.size != 3 || houses.terminatorIndex >= houses.posCount
            ) {
                throw SpecException("map.villager_houses: inconsistent icon/slot data")
            }

            val p = o.getJSONObject("player")
            val chainArr = p.getJSONArray("chain")
            val chain = List(chainArr.length()) { i ->
                val s = chainArr.getJSONObject(i)
                ChainStep(
                    step = s.getString("step"),
                    addr = if (s.has("addr")) num(s.get("addr")) else null,
                    from = s.optString("from", "").ifEmpty { null },
                    offset = if (s.has("offset")) num(s.get("offset")) else 0L,
                    type = type(s),
                    checkMem1 = s.optString("check", "").equals("in MEM1", ignoreCase = true),
                    equals = if (s.has("equals")) num(s.get("equals")) else null,
                    min = if (s.has("min")) num(s.get("min")) else null,
                )
            }
            if (chain.isEmpty() || chain.first().addr == null) throw SpecException("map.player.chain: first step needs addr")
            val names = HashSet<String>()
            for (s in chain) {
                if (s.addr == null && (s.from == null || s.from !in names)) throw SpecException("map.player.chain: bad 'from' in ${s.step}")
                names += s.step
            }
            val checksArr = p.getJSONArray("actor_checks")
            val pos = p.getJSONObject("position")
            val facing = p.getJSONObject("facing")
            val tr = p.getJSONObject("transform")
            val show = p.getJSONObject("show_when")
            val ft = show.optJSONObject("field_type")
            val indoor = p.optJSONObject("indoor_acre")
            val player = Player(
                chain = chain,
                actorChecks = List(checksArr.length()) { i ->
                    val c = checksArr.getJSONObject(i)
                    Check(long(c, "offset"), type(c), long(c, "equals"))
                },
                posX = long(pos, "x_offset"), posY = long(pos, "y_offset"), posZ = long(pos, "z_offset"),
                facingOffset = long(facing, "offset"),
                fullTurn = int(facing, "full_turn"),
                worldUnitsPerAcre = num(tr.get("world_units_per_acre")).toFloat(),
                mapUnitsPerAcre = num(tr.get("map_units_per_acre")).toFloat(),
                showScenes = show.optJSONArray("scene_no")?.let { a -> List(a.length()) { num(a.get(it)) }.toSet() } ?: emptySet(),
                fieldTypeAddr = ft?.let { addrOf(it) },
                fieldTypeEquals = ft?.let { num(it.get("equals")) } ?: 0L,
                blockXMin = int(show, "block_x_min"), blockXMax = int(show, "block_x_max"),
                blockZMin = int(show, "block_z_min"), blockZMax = int(show, "block_z_max"),
                markerColor = parseColor(p.getJSONObject("marker").optString("color")) ?: 0xFFFF2D2D.toInt(),
                markerOutline = parseColor(p.getJSONObject("marker").optString("outline")) ?: -1,
                highlightColor = parseColor(p.optJSONObject("acre_highlight")?.optString("color") ?: "") ?: 0xFFFF00E6.toInt(),
                nextSceneAddr = indoor?.optJSONObject("next_scene")?.let { addrOf(it) },
                exitPositionAddr = indoor?.optJSONObject("exit_position")?.let { addrOf(it) },
            )
            if (player.worldUnitsPerAcre <= 0f || player.fullTurn <= 0) throw SpecException("map.player: bad transform")

            return MapSpec(grid, acreTypes, texture, buildings, houses, player)
        }

        private const val DEFAULT_FALLBACK = 0xFF808080.toInt()

        /** "#RRGGBB" to 0xFFRRGGBB, or null. */
        fun parseColor(s: String?): Int? {
            val t = s?.trim()?.removePrefix("#") ?: return null
            if (t.length != 6) return null
            return t.toLongOrNull(16)?.let { (0xFF000000L or it).toInt() }
        }
    }
}
