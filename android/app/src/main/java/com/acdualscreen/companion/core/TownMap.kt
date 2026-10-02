package com.acdualscreen.companion.core

import java.io.IOException
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * One drawn acre of the town map: entry n of the game's acre list (mMP_make_max_no_table), drawn
 * at column n % cols, row n / cols. [pixels] is the visible part of the acre texture, decoded from
 * RAM ([texW] x [texH] ARGB), or null when the texture failed validation (draw a plain fallback).
 * [blockX]/[blockZ] is the block the entry came from (-1 for padding entries).
 */
class AcreCell(
    val col: Int,
    val row: Int,
    val blockX: Int,
    val blockZ: Int,
    val type: Int,
    val pixels: IntArray?,
)

/**
 * A building acre (spec map.buildings). The building is part of the acre texture, so normally
 * nothing extra is drawn; when that texture is missing ([inTexture] false) the client draws the
 * spec's original fallback marker: a [color] square labelled [label].
 */
data class BuildingAcre(
    val key: String,
    val label: String,
    val blockX: Int,
    val blockZ: Int,
    val inTexture: Boolean,
    val color: Int,
)

/** The static part of the map for one town: acre art and building acres. Compared by identity. */
class TownLayout(
    val cols: Int,
    val rows: Int,
    val firstBlockX: Int,
    val firstBlockZ: Int,
    val acreUnits: Int,
    val colLabels: List<String>,
    val rowLabels: List<String>,
    val texW: Int,
    val texH: Int,
    val acres: List<AcreCell>,
    val buildings: List<BuildingAcre>,
    /** Problems met while building the layout (fallbacks used), for the status line / logs. */
    val notes: List<String>,
    /**
     * Colour of the grid line the acre art carries on its left column (sampled from RAM), for
     * closing the grid on the right and bottom; [TownMapRenderer.FALLBACK_LINE] without art.
     */
    val lineColor: Int = TownMapRenderer.FALLBACK_LINE,
) {
    val widthUnits: Int get() = cols * acreUnits
    val heightUnits: Int get() = rows * acreUnits

    fun inGrid(bx: Int, bz: Int) = bx in firstBlockX until firstBlockX + cols && bz in firstBlockZ until firstBlockZ + rows

    /** Map-unit rectangle (left, top, right, bottom) of block ([bx], [bz]). */
    fun acreRect(bx: Int, bz: Int): FloatArray {
        val l = ((bx - firstBlockX) * acreUnits).toFloat()
        val t = ((bz - firstBlockZ) * acreUnits).toFloat()
        return floatArrayOf(l, t, l + acreUnits, t + acreUnits)
    }

    fun acreLabel(bx: Int, bz: Int): String? {
        val r = rowLabels.getOrNull(bz - firstBlockZ) ?: return null
        val c = colLabels.getOrNull(bx - firstBlockX) ?: return null
        return "$r-$c"
    }
}

/**
 * Villager-house icon art for one colour tier: the 16x16 IA4 icon from RAM, tinted with the tier's
 * PRIM/ENV colours ([pixels] [w] x [h] ARGB), drawn over [units] map units. [pixels] is null when
 * the icon could not be read: draw a [units] square in [fallbackColor] instead.
 */
class HouseArt(val tier: Int, val pixels: IntArray?, val w: Int, val h: Int, val units: Int, val fallbackColor: Int)

/** One villager-house icon, centred at ([cx], [cy]) map units. */
data class HouseIcon(
    val slot: Int,
    val blockX: Int,
    val blockZ: Int,
    val idx: Int,
    val tier: Int,
    val cx: Float,
    val cy: Float,
    /** True when the house-position list had a slot matching the house's unit exactly. */
    val exact: Boolean,
)

/**
 * The player's position: map units ([mapX], [mapY], x right / y down, origin at the map's
 * top-left), its block, and the facing angle (0..65535, 0 = south, 0x4000 = east).
 */
data class PlayerMarker(
    val mapX: Float,
    val mapY: Float,
    val blockX: Int,
    val blockZ: Int,
    val facing: Int,
) {
    /** Unit facing vector in map space (right, down). */
    val dirX: Float get() = sin(2.0 * PI * facing / 65536.0).toFloat()
    val dirY: Float get() = cos(2.0 * PI * facing / 65536.0).toFloat()
}

/** Map data for the panel. Equality: [layout] and [houseArt] by identity, the rest by value. */
data class TownMapState(
    val layout: TownLayout?,
    val houseArt: List<HouseArt> = emptyList(),
    /** The game's player figure (tinted), or null: draw the original dot instead. */
    val playerArt: HouseArt? = null,
    val houses: List<HouseIcon> = emptyList(),
    val player: PlayerMarker? = null,
    /** Acre to outline when indoors (spec player.indoor_acre), as (blockX, blockZ). */
    val indoorAcre: Pair<Int, Int>? = null,
    val markerColor: Int = 0xFFFF2D2D.toInt(),
    val markerOutline: Int = -1,
    val highlightColor: Int = 0xFFFF00E6.toInt(),
    /** Why there is no layout / what fell back; empty when all is well. */
    val note: String = "",
)

/**
 * Reads the town map from emulated RAM as the spec's "map" section describes (read-only).
 *
 * - Static tables (texture pointers, palettes, the acre-texture block, the house icon, the
 *   house-position list) are read once per game image (boot.dol hash) and kept; while some of
 *   them fail validation they are re-read every few seconds.
 * - The layout (acre types, textures, building acres) is rebuilt only when the town's
 *   signature (combi table, extra bridge, table pointers) or the static data it uses changes.
 * - Villager houses are refreshed every few seconds (sooner after a torn read, which keeps the
 *   slot's last icon meanwhile); the player marker on every [poll].
 *
 * It never reads the item/fg grid: no buried items, fossils or other hidden things.
 * Not thread-safe: use from the polling thread.
 */
class TownMapReader(private val map: MemoryMap, private val spec: MapSpec) {

    companion object {
        const val HOUSE_REFRESH_NS = 4_000_000_000L
        /** Next house refresh after one whose reads tore (the torn slots kept their last icon). */
        const val HOUSE_RETRY_NS = 500_000_000L
        const val STATIC_RETRY_NS = 5_000_000_000L
        /** Polls a missing marker is kept for, so a torn read does not blink it. */
        const val PLAYER_GRACE = 3
        // mMP_check_layer / mCoBG_Height2GetLayer thresholds (spec villager_houses.tier_rule.rule).
        const val LAYER_Y_LOW = 100f
        const val LAYER_Y_HIGH = 220f

        fun isPtr(p: Long?) = Decoder.isPtr(p)

        /** Integer value of a big-endian field of [type] (sign-extended for signed types). */
        fun valueOf(type: FieldType, b: ByteArray): Long = when (type) {
            FieldType.U8 -> (b[0].toLong() and 0xFF)
            FieldType.S8 -> b[0].toLong()
            FieldType.U16 -> Decoder.be16(b).toLong()
            FieldType.S16 -> Decoder.be16(b).toShort().toLong()
            FieldType.S32 -> Decoder.be32(b).toInt().toLong()
            else -> Decoder.be32(b)
        }

        fun f32(b: ByteArray, off: Int = 0): Float = Float.fromBits(Decoder.be32(b, off).toInt())

        /** Same tiers, sizes, colours and pixels (HouseArt itself compares by identity). */
        fun sameArt(a: List<HouseArt>, b: List<HouseArt>): Boolean = a.size == b.size && a.indices.all { i ->
            val x = a[i]
            val y = b[i]
            x.tier == y.tier && x.w == y.w && x.h == y.h && x.units == y.units &&
                x.fallbackColor == y.fallbackColor && x.pixels.contentEquals(y.pixels)
        }

        /** mMP_check_layer: the colour tier of a house at height [y] (spec tier_rule.rule). */
        fun tierFor(y: Float, step3: Boolean): Int {
            var layer = if (y < LAYER_Y_LOW) 2 else if (step3) (if (y < LAYER_Y_HIGH) 1 else 0) else 1
            if (!step3) layer = maxOf(layer - 1, 0)
            return layer
        }

        /**
         * env + (prim - env) * I / 255 per channel, rounded (as townmap.py tints), alpha = A
         * (kan_win_npc2T combiner).
         */
        fun tint(iconArgb: IntArray, prim: Int, env: Int): IntArray = IntArray(iconArgb.size) { k ->
            val c = iconArgb[k]
            val a = c ushr 24
            val i = c and 0xFF
            fun ch(shift: Int): Int {
                val p = (prim shr shift) and 0xFF
                val e = (env shr shift) and 0xFF
                return (e + (p - e) * i / 255.0 + 0.5).toInt().coerceIn(0, 255)
            }
            (a shl 24) or (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
        }
    }

    private class Statics(
        val texPtrs: LongArray?,
        val palSel: IntArray?,
        val palettes: List<IntArray?>,
        val texRegion: ByteArray?,
        val pluss: IntArray?,
        val dataCombi: ByteArray?,
        val housePos: ByteArray?,
        val houseArt: List<HouseArt>,
        val playerArt: HouseArt?,
        val complete: Boolean,
        val notes: List<String>,
    ) {
        val decoded = HashMap<Int, IntArray?>()

        /** True when [other] holds the same data the layout is built from (acre art and types). */
        fun sameLayoutInputs(other: Statics?): Boolean {
            if (other == null) return false
            if (other === this) return true
            return texPtrs.contentEquals(other.texPtrs) && palSel.contentEquals(other.palSel) &&
                texRegion.contentEquals(other.texRegion) && pluss.contentEquals(other.pluss) &&
                dataCombi.contentEquals(other.dataCombi) && palettes.size == other.palettes.size &&
                palettes.indices.all { palettes[it].contentEquals(other.palettes[it]) }
        }
    }

    /** Result of one villager-house refresh; [torn] when some slot's reads did not agree. */
    private class HouseRead(val icons: List<HouseIcon>, val torn: Boolean)

    private val grid = spec.grid
    private val at = spec.acreTypes
    private val tx = spec.texture
    private val vh = spec.houses
    private val pl = spec.player
    private val sceneField = map.global("scene_no")
    private val villagers = map.villagers
    private val npcIdField = villagers?.field("npc_id")
    private val homeFields = listOf("home_block_x", "home_block_z", "home_ut_x", "home_ut_z").map { villagers?.field(it) }

    private var cacheKey: String? = null
    private var statics: Statics? = null
    private var staticsAt = 0L
    private var layout: TownLayout? = null
    private var layoutSig: List<ByteArray?>? = null
    private var combi: IntArray? = null
    private var houses: List<HouseIcon> = emptyList()
    private var housesAt = 0L
    private var houseDelay = HOUSE_REFRESH_NS
    private var lastPlayer: PlayerMarker? = null
    private var playerMisses = 0

    /** Call with every EmuLink handshake; a different game image drops everything cached. */
    fun onHello(gameId: String, gameHash: String) {
        val key = "$gameId/$gameHash"
        if (key == cacheKey) return
        cacheKey = key
        statics = null
        builtFrom = null
        layout = null
        layoutSig = null
        combi = null
        houses = emptyList()
        houseDelay = HOUSE_REFRESH_NS
        lastPlayer = null
    }

    /** Number of times the static tables were read (tests use it to check caching). */
    var staticReads = 0
        private set
    var layoutBuilds = 0
        private set
    var houseReads = 0
        private set

    // ------------------------------------------------------------------ poll

    @Throws(IOException::class)
    fun poll(reader: MemoryReader, nowNs: Long = System.nanoTime()): TownMapState {
        // D1: everything at a fixed address that is needed every poll.
        val reqs = ArrayList<ReadReq>()
        fun add(addr: Long, size: Int): Int { reqs += ReadReq(addr, size); return reqs.size - 1 }
        val iTypePtr = add(at.tablePointerAddr, 4)
        val iKindsPtr = add(spec.buildings.kindsPointerAddr, 4)
        val iCombi = add(at.combiAddr, 2 * at.combiCount)
        val iBridge = at.bridge?.let { add(it.addr, it.readSize) } ?: -1
        val absSteps = pl.chain.filter { it.addr != null }.associate { it.step to add(it.addr!!, it.type.elemSize) }
        val iScene = sceneField?.let { f -> map.globalAddr(f)?.let { add(it, f.byteSize) } } ?: -1
        val iField = pl.fieldTypeAddr?.let { add(it, 1) } ?: -1
        val iNext = pl.nextSceneAddr?.let { add(it, 4) } ?: -1
        val iExit = pl.exitPositionAddr?.let { add(it, 6) } ?: -1
        val d1 = readStable(reader, reqs).values

        val st = staticsFor(reader, nowNs)

        val combiBytes = d1[iCombi]
        val sig = listOf(d1[iTypePtr], d1[iKindsPtr], combiBytes, if (iBridge >= 0) d1[iBridge] else ByteArray(0))
        var rebuilt = false
        // A torn (null) part of the signature must not trigger a rebuild of a layout we have.
        val sigStable = sig.all { it != null }
        // Incomplete statics are re-read every few seconds; a re-read that changed nothing the
        // layout uses must not rebuild it (that would hand the views a new layout every time).
        if (layout != null && st !== builtFrom && st.sameLayoutInputs(builtFrom)) builtFrom = st
        if (combiBytes != null && (layout == null || (sigStable && (!sameSig(sig, layoutSig) || st !== builtFrom)))) {
            combi = IntArray(at.combiCount) { Decoder.be16(combiBytes, 2 * it) }
            layout = buildLayout(reader, st, d1[iTypePtr]?.let { Decoder.be32(it) }, d1[iKindsPtr]?.let { Decoder.be32(it) },
                if (iBridge >= 0) d1[iBridge] else null)
            layoutSig = sig
            builtFrom = st
            rebuilt = true
            layoutBuilds++
        }
        val lay = layout
        if (lay != null && (rebuilt || nowNs - housesAt >= houseDelay)) {
            // After a rebuild (another town) the previous icons say nothing about this one.
            val hr = readHouses(reader, st, if (rebuilt) emptyList() else houses)
            houses = hr.icons
            housesAt = nowNs
            houseDelay = if (hr.torn) HOUSE_RETRY_NS else HOUSE_REFRESH_NS
            houseReads++
        }

        val scene = if (iScene >= 0) d1[iScene]?.let { valueOf(sceneField!!.type, it) } else null
        val fieldType = if (iField >= 0) d1[iField]?.let { it[0].toLong() and 0xFF } else null
        var player = readPlayer(reader, absSteps.mapValues { (_, i) -> d1[i] }, scene, fieldType)
        if (player == null && lastPlayer != null && playerMisses < PLAYER_GRACE && scene != null &&
            scene in pl.showScenes && fieldType == pl.fieldTypeEquals
        ) {
            // A torn read: keep the last marker for a few polls rather than blink it.
            playerMisses++
            player = lastPlayer
        } else {
            playerMisses = 0
            lastPlayer = player
        }

        var indoor: Pair<Int, Int>? = null
        if (player == null && fieldType != null && fieldType != pl.fieldTypeEquals && iNext >= 0 && iExit >= 0) {
            val next = d1[iNext]?.let { Decoder.be32(it).toInt() }
            val exit = d1[iExit]
            if (next != null && next != 0 && exit != null) {
                val bx = (Decoder.be16(exit, 0).toShort() / pl.worldUnitsPerAcre).toInt()
                val bz = (Decoder.be16(exit, 4).toShort() / pl.worldUnitsPerAcre).toInt()
                if (inShowRange(bx, bz)) indoor = bx to bz
            }
        }

        val notes = LinkedHashSet<String>()
        if (combiBytes == null && lay == null) notes += "town data not readable yet"
        notes += st.notes
        lay?.notes?.let { notes += it }
        return TownMapState(
            layout = lay,
            houseArt = st.houseArt,
            playerArt = st.playerArt,
            houses = if (lay != null) houses else emptyList(),
            player = player,
            indoorAcre = indoor,
            markerColor = pl.markerColor,
            markerOutline = pl.markerOutline,
            highlightColor = pl.highlightColor,
            note = notes.joinToString("; "),
        )
    }

    private var builtFrom: Statics? = null

    private fun sameSig(a: List<ByteArray?>, b: List<ByteArray?>?): Boolean {
        if (b == null || a.size != b.size) return false
        return a.indices.all { i -> val x = a[i]; val y = b[i]; if (x == null || y == null) x == null && y == null else x.contentEquals(y) }
    }

    private fun inShowRange(bx: Int, bz: Int) = bx in pl.blockXMin..pl.blockXMax && bz in pl.blockZMin..pl.blockZMax

    // ------------------------------------------------------------------ static tables

    private fun staticsFor(reader: MemoryReader, nowNs: Long): Statics {
        val cur = statics
        if (cur != null && (cur.complete || nowNs - staticsAt < STATIC_RETRY_NS)) return cur
        val s = readStatics(reader, cur)
        staticReads++
        statics = s
        staticsAt = nowNs
        return s
    }

    /** Reads the static tables; house art equal to [prev]'s keeps [prev]'s list (same identity). */
    private fun readStatics(reader: MemoryReader, prev: Statics?): Statics {
        val notes = ArrayList<String>()
        val reqs = ArrayList<ReadReq>()
        fun add(addr: Long, size: Int): Int { reqs += ReadReq(addr, size); return reqs.size - 1 }
        val iPtrs = add(tx.pointerTableAddr, 4 * tx.pointerCount)
        val iSel = add(tx.selectorAddr, tx.selectorCount)
        val iPalPtrs = add(tx.palettePointerAddr, 4 * tx.palettePointerCount)
        val iPluss = add(at.plussBridgeAddr, at.plussBridgeCount)
        val iCombi = add(at.dataCombiAddr, at.dataCombiEntryLen * at.dataCombiCount)
        val iIcon = add(vh.iconAddr, vh.iconSize)
        val iTiers = vh.tiers.map { add(it.displayListAddr, maxOf(it.primOffset, it.envOffset) + it.colorLen) }
        val iPos = add(vh.posListAddr, vh.posEntryLen * vh.posCount)
        val pi = pl.icon
        val iPlayerIcon = if (pi != null) add(pi.addr, pi.size) else -1
        val iPlayerDl = if (pi != null) add(pi.displayListAddr, maxOf(pi.primOffset, pi.envOffset) + pi.colorLen) else -1
        val iRegion = add(tx.rangeStart, (tx.rangeEnd - tx.rangeStart).toInt())
        val r = reader.read(reqs)

        val ptrBytes = r[iPtrs]
        val texPtrs = ptrBytes?.let { b -> LongArray(tx.pointerCount) { Decoder.be32(b, 4 * it) } }
        val palSel = r[iSel]?.let { b -> IntArray(b.size) { b[it].toInt() and 0xFF } }
        val palPtrs = r[iPalPtrs]?.let { b -> List(tx.palettePointerCount) { Decoder.be32(b, 4 * it) } }
        val palOk = palPtrs != null && palPtrs.all(::isPtr) &&
            (tx.paletteExpected.isEmpty() || palPtrs == tx.paletteExpected)
        if (!palOk) notes += "acre palettes did not verify"
        var palettes: List<IntArray?> = List(tx.palettePointerCount) { null }
        if (palOk) {
            val pr = reader.read(palPtrs.map { ReadReq(it, tx.paletteSize) })
            palettes = pr.map { b ->
                if (b == null || b.size < 2 * tx.paletteEntries) null
                else GcTexture.decodePalette(b, tx.paletteEntries, tx.paletteFormat)
            }
        }
        val region = r[iRegion]
        if (texPtrs == null || region == null) notes += "acre textures not readable"

        val dataCombi = r[iCombi]
        val pos = r[iPos]?.takeIf { b ->
            val o = vh.terminatorIndex * vh.posEntryLen + vh.fgNameOffset
            o + 2 <= b.size && Decoder.be16(b, o) == vh.terminator
        }
        if (pos == null) notes += "house-position list did not verify"

        val icon = r[iIcon]?.let { b ->
            runCatching { GcTexture.decode(b, vh.iconW, vh.iconH, vh.iconFormat) }.getOrNull()
        }?.takeIf { px -> px.any { (it ushr 24) != 0 } }
        if (icon == null) notes += "house icon not readable"
        val art = vh.tiers.mapIndexed { k, t ->
            val dl = r[iTiers[k]]
            fun rgbAt(off: Int): Int? = dl?.let {
                GcTexture.argb(255, it[off].toInt() and 0xFF, it[off + 1].toInt() and 0xFF, it[off + 2].toInt() and 0xFF)
            }
            var prim = rgbAt(t.primOffset)
            var env = rgbAt(t.envOffset)
            if (prim != t.expectedPrim || env != t.expectedEnv) {
                // Not the display list the spec describes: use the spec's verified colours.
                notes += "house colours (tier ${t.tier}) did not verify"
                prim = t.expectedPrim
                env = t.expectedEnv
            }
            HouseArt(t.tier, icon?.let { tint(it, prim, env) }, vh.iconW, vh.iconH, vh.iconUnits, prim)
        }.let { a -> prev?.houseArt?.takeIf { sameArt(it, a) } ?: a }

        val playerArt = if (pi == null) null else {
            val px = r[iPlayerIcon]?.let { b -> runCatching { GcTexture.decode(b, pi.w, pi.h, pi.format) }.getOrNull() }
                ?.takeIf { p -> p.any { (it ushr 24) != 0 } }
            val dl = r[iPlayerDl]
            fun rgbAt(off: Int): Int? = dl?.let {
                GcTexture.argb(255, it[off].toInt() and 0xFF, it[off + 1].toInt() and 0xFF, it[off + 2].toInt() and 0xFF)
            }
            var prim = rgbAt(pi.primOffset)
            var env = rgbAt(pi.envOffset)
            if (prim != pi.expectedPrim || env != pi.expectedEnv) {
                prim = pi.expectedPrim
                env = pi.expectedEnv
            }
            if (px == null) {
                notes += "player icon not readable"
                null
            } else {
                HouseArt(-1, tint(px, prim, env), pi.w, pi.h, pi.units, prim)
                    .let { a -> prev?.playerArt?.takeIf { sameArt(listOf(it), listOf(a)) } ?: a }
            }
        }

        val complete = palOk && texPtrs != null && region != null && palSel != null && r[iPluss] != null &&
            dataCombi != null && pos != null && icon != null && palettes.all { it != null }
        return Statics(
            texPtrs = texPtrs,
            palSel = palSel,
            palettes = palettes,
            texRegion = region,
            pluss = r[iPluss]?.let { b -> IntArray(b.size) { b[it].toInt() and 0xFF } },
            dataCombi = dataCombi,
            housePos = pos,
            houseArt = art,
            playerArt = playerArt,
            complete = complete,
            notes = notes,
        )
    }

    /** Decoded visible pixels of acre [type], or null when its texture fails validation. */
    private fun texture(st: Statics, type: Int): IntArray? {
        if (type < 0 || type >= at.typeCount) return null
        return st.decoded.getOrPut(type) {
            val ptrs = st.texPtrs ?: return@getOrPut null
            val region = st.texRegion ?: return@getOrPut null
            val p = ptrs.getOrNull(type) ?: return@getOrPut null
            if (!tx.validPointer(p)) return@getOrPut null
            val sel = st.palSel?.getOrNull(type) ?: return@getOrPut null
            val pal = st.palettes.getOrNull(sel) ?: return@getOrPut null
            val off = (p - tx.rangeStart).toInt()
            runCatching {
                val full = GcTexture.decode(region, tx.width, tx.height, tx.format, pal, off)
                GcTexture.crop(full, tx.width, tx.visX, tx.visY, tx.visW, tx.visH)
            }.getOrNull()
        }
    }

    // ------------------------------------------------------------------ layout

    private fun buildLayout(reader: MemoryReader, st: Statics, typePtr: Long?, kindsPtr: Long?, bridge: ByteArray?): TownLayout {
        val notes = ArrayList<String>()
        val n = grid.blockCount
        val reqs = ArrayList<ReadReq>()
        val useTable = typePtr != null && typePtr == at.tableExpected
        val useKinds = kindsPtr != null && kindsPtr == spec.buildings.kindsExpected
        if (useTable) reqs += ReadReq(typePtr, at.tableCount)
        if (useKinds) reqs += ReadReq(kindsPtr, 4 * spec.buildings.kindsCount)
        val r = readStable(reader, reqs).values
        val tableBytes = if (useTable) r[0] else null
        val kindBytes = if (useKinds) r[if (useTable) 1 else 0] else null

        val types = IntArray(n) { -1 }
        val cmb = combi
        if (tableBytes != null) {
            for (i in 0 until n) types[i] = tableBytes[i].toInt() and 0xFF
        } else if (cmb != null && st.dataCombi != null) {
            notes += "acre types from the save (block-type table did not verify)"
            for (i in 0 until n) {
                val ct = cmb[i] shr 2
                val o = ct * at.dataCombiEntryLen + at.typeOffset
                types[i] = if (ct < at.dataCombiCount && o < st.dataCombi.size) st.dataCombi[o].toInt() and 0xFF else -1
            }
        } else {
            notes += "acre types not readable"
        }

        val bridgeOn = bridge != null && at.bridge != null && (bridge[at.bridge.flagsOffset].toInt() and at.bridge.existsMask) != 0
        val bbx = if (bridgeOn) bridge[at.bridge.blockXOffset].toInt() and 0xFF else -1
        val bbz = if (bridgeOn) bridge[at.bridge.blockZOffset].toInt() and 0xFF else -1

        // mMP_make_max_no_table: scan z then x, skip border types, apply the bridge, pad.
        val entries = ArrayList<Triple<Int, Int, Int>>()
        for (bz in grid.firstBlockZ until grid.firstBlockZ + grid.rows) {
            for (bx in grid.firstBlockX until grid.firstBlockX + grid.cols) {
                var t = types[grid.blockIndex(bx, bz)]
                if (t in at.skipTypes) continue
                if (bridgeOn && bx == bbx && bz == bbz && t >= 0) {
                    val alt = st.pluss?.getOrNull(t)
                    if (alt != null && alt != at.plussNone) t = alt
                }
                entries += Triple(bx, bz, t)
            }
        }
        val total = grid.cols * grid.rows
        while (entries.size < total) entries += Triple(-1, -1, at.padType)
        val acres = List(total) { k ->
            val (bx, bz, t) = entries[k]
            AcreCell(k % grid.cols, k / grid.cols, bx, bz, t, texture(st, t))
        }
        val missing = acres.count { it.pixels == null }
        if (missing > 0) notes += "$missing acre image(s) not available"

        val buildings = ArrayList<BuildingAcre>()
        if (kindBytes != null) {
            for (bz in grid.firstBlockZ until grid.firstBlockZ + grid.rows) {
                for (bx in grid.firstBlockX until grid.firstBlockX + grid.cols) {
                    val k = Decoder.be32(kindBytes, 4 * grid.blockIndex(bx, bz))
                    val ind = spec.buildings.indicators.firstOrNull { (k and it.kindMask) != 0L } ?: continue
                    val cell = acres.firstOrNull { it.blockX == bx && it.blockZ == bz }
                    buildings += BuildingAcre(ind.key, ind.fallbackLabel, bx, bz, cell?.pixels != null, ind.fallbackColor)
                }
            }
        } else {
            notes += "building acres not readable"
        }
        return TownLayout(
            cols = grid.cols, rows = grid.rows, firstBlockX = grid.firstBlockX, firstBlockZ = grid.firstBlockZ,
            acreUnits = grid.acreUnits, colLabels = grid.colLabels, rowLabels = grid.rowLabels,
            texW = tx.visW, texH = tx.visH, acres = acres, buildings = buildings, notes = notes,
            lineColor = acres.firstNotNullOfOrNull { c -> c.pixels?.get((tx.visH / 2) * tx.visW)?.takeIf { (it ushr 24) == 255 } }
                ?: TownMapRenderer.FALLBACK_LINE,
        )
    }

    // ------------------------------------------------------------------ villager houses

    /**
     * Reads every villager's house. A slot whose id or home did not read the same twice (a torn
     * read) keeps its icon from [prev] rather than vanishing until the next refresh; a torn height
     * keeps the previous tier of a house in the same acre. Either marks the result [HouseRead.torn].
     */
    private fun readHouses(reader: MemoryReader, st: Statics, prev: List<HouseIcon>): HouseRead {
        val none = HouseRead(emptyList(), false)
        val group = villagers ?: return none
        val idField = npcIdField ?: return none
        if (homeFields.any { it == null }) return none
        val cmb = combi ?: return none
        val homes = homeFields.map { it!! }
        val homeStart = homes.minOf { it.offset }
        val homeLen = (homes.maxOf { it.offset + it.byteSize } - homeStart).toInt()
        val reqs = ArrayList<ReadReq>()
        for (i in 0 until group.count) {
            val rec = map.recordAddr(group, i) ?: return none
            reqs += ReadReq(rec + idField.offset, idField.byteSize)
            reqs += ReadReq(rec + homeStart, homeLen)
            reqs += ReadReq(vh.houseYAddr + i * vh.houseYStride + vh.houseYFieldOffset, 4)
        }
        val r = readStable(reader, reqs).values
        val step3 = (cmb[0] and 3) == 2
        val previous = prev.associateBy { it.slot }
        var torn = false
        val out = ArrayList<HouseIcon>()
        for (i in 0 until group.count) {
            val idB = r[3 * i]
            val home = r[3 * i + 1]
            if (idB == null || home == null) {
                torn = true
                previous[i]?.let { out += it }
                continue
            }
            val id = valueOf(idField.type, idB).toInt()
            if (!Decoder.isVillagerId(id)) continue // validity rule 6
            fun h(k: Int) = home[(homes[k].offset - homeStart).toInt()].toInt() and 0xFF
            val bx = h(0)
            val bz = h(1)
            val ux = h(2)
            val uz = h(3)
            if (bx !in grid.firstBlockX until grid.firstBlockX + grid.cols ||
                bz !in grid.firstBlockZ until grid.firstBlockZ + grid.rows
            ) continue
            val yB = r[3 * i + 2]
            val rawTier = if (yB != null) {
                val y = f32(yB)
                if (y.isFinite()) tierFor(y, step3) else 0
            } else {
                torn = true
                previous[i]?.takeIf { it.blockX == bx && it.blockZ == bz }?.tier ?: 0
            }
            val tier = rawTier.coerceIn(0, vh.tiers.size - 1)
            val (idx, exact) = houseSlot(st, cmb[grid.blockIndex(bx, bz)], ux, uz)
            out += HouseIcon(
                slot = i, blockX = bx, blockZ = bz, idx = idx, tier = tier,
                cx = ((bx - grid.firstBlockX) * grid.acreUnits + vh.xOffsets[idx % 3]).toFloat(),
                cy = ((bz - grid.firstBlockZ) * grid.acreUnits + vh.yOffsets[idx / 3]).toFloat(),
                exact = exact,
            )
        }
        return HouseRead(out, torn)
    }

    /** mMP_set_house_data slot search: (idx 0-8, exact match). */
    private fun houseSlot(st: Statics, combiValue: Int, utX: Int, utZ: Int): Pair<Int, Boolean> {
        val pos = st.housePos ?: return 0 to false
        val dc = st.dataCombi
        val ct = combiValue shr 2
        val fgOff = ct * at.dataCombiEntryLen + at.fgIdOffset
        val fg = if (dc != null && ct < at.dataCombiCount && fgOff + 2 <= dc.size) Decoder.be16(dc, fgOff) else -1
        fun slot(entry: Int, s: Int): IntArray {
            val o = entry * vh.posEntryLen + vh.slotsOffset + s * vh.slotLen
            return intArrayOf(pos[o + vh.slotUtX].toInt() and 0xFF, pos[o + vh.slotUtZ].toInt() and 0xFF, pos[o + vh.slotIdx].toInt() and 0xFF)
        }
        var chosen: IntArray? = null
        var exact = false
        for (e in 0 until vh.posCount) {
            val name = Decoder.be16(pos, e * vh.posEntryLen + vh.fgNameOffset)
            if (name == vh.terminator) break
            if (name == fg) {
                val m = (0 until vh.slotCount).map { slot(e, it) }.firstOrNull { it[0] == utX && it[1] == utZ - 1 }
                exact = m != null
                chosen = m ?: slot(e, 0)
                break
            }
        }
        val idx = (chosen ?: slot(0, 0))[2]
        return (if (idx in 0..8) idx else 0) to exact
    }

    // ------------------------------------------------------------------ player

    private fun readPlayer(reader: MemoryReader, absValues: Map<String, ByteArray?>, scene: Long?, fieldType: Long?): PlayerMarker? {
        if (scene == null || scene !in pl.showScenes) return null
        if (pl.fieldTypeAddr != null && fieldType != pl.fieldTypeEquals) return null
        val values = HashMap<String, Long>()
        fun accept(s: MapSpec.ChainStep, b: ByteArray?): Boolean {
            if (b == null) return false
            val v = valueOf(s.type, b)
            if (s.checkMem1 && !isPtr(v)) return false
            if (s.equals != null && v != s.equals) return false
            if (s.min != null && v < s.min) return false
            values[s.step] = v
            return true
        }
        for (s in pl.chain) if (s.addr != null && !accept(s, absValues[s.step])) return null
        var pending = pl.chain.filter { it.addr == null }
        while (pending.isNotEmpty()) {
            val ready = pending.filter { it.from in values }
            if (ready.isEmpty()) return null
            val r = readStable(reader, ready.map { ReadReq(values.getValue(it.from!!) + it.offset, it.type.elemSize) }).values
            ready.forEachIndexed { k, s -> if (!accept(s, r[k])) return null }
            pending = pending - ready.toSet()
        }
        val actor = values[pl.actorStep] ?: return null
        val reqs = pl.actorChecks.map { ReadReq(actor + it.offset, it.type.elemSize) } + listOf(
            ReadReq(actor + pl.posX, 4), ReadReq(actor + pl.posZ, 4), ReadReq(actor + pl.facingOffset, 2),
        )
        val r = readStable(reader, reqs).values
        pl.actorChecks.forEachIndexed { k, c -> if (r[k] == null || valueOf(c.type, r[k]!!) != c.equals) return null }
        val n = pl.actorChecks.size
        val x = r[n]?.let { f32(it) } ?: return null
        val z = r[n + 1]?.let { f32(it) } ?: return null
        val facing = r[n + 2]?.let { Decoder.be16(it) } ?: return null
        if (!x.isFinite() || !z.isFinite()) return null
        val bx = (x / pl.worldUnitsPerAcre).toInt()
        val bz = (z / pl.worldUnitsPerAcre).toInt()
        if (x < 0f || z < 0f || !inShowRange(bx, bz)) return null
        return PlayerMarker(
            mapX = (x / pl.worldUnitsPerAcre - grid.firstBlockX) * pl.mapUnitsPerAcre,
            mapY = (z / pl.worldUnitsPerAcre - grid.firstBlockZ) * pl.mapUnitsPerAcre,
            blockX = bx,
            blockZ = bz,
            facing = facing and 0xFFFF,
        )
    }
}
