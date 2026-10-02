package com.acdualscreen.companion.core

import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.roundToInt

/** A plain ARGB image with alpha-blended drawing. */
class Raster(val width: Int, val height: Int, val pixels: IntArray = IntArray(width * height)) {

    init {
        require(width > 0 && height > 0 && pixels.size == width * height) { "bad raster size" }
    }

    operator fun get(x: Int, y: Int): Int = pixels[y * width + x]

    /**
     * Source-over blend of [argb] at ([x], [y]), channels rounded to nearest (over an opaque
     * pixel this is townmap.py's Canvas.blend exactly); outside the raster is ignored.
     */
    fun blend(x: Int, y: Int, argb: Int) {
        if (x < 0 || y < 0 || x >= width || y >= height) return
        val a = argb ushr 24
        if (a == 0) return
        val i = y * width + x
        if (a == 255) {
            pixels[i] = argb
            return
        }
        val d = pixels[i]
        val da = d ushr 24
        val oa = a + da * (255 - a) / 255
        if (oa == 0) return
        val den = oa * 255
        fun ch(shift: Int): Int {
            val s = (argb shr shift) and 0xFF
            val t = (d shr shift) and 0xFF
            // (s * a + t * da * (255 - a) / 255) / oa, rounded
            return ((s * a * 255 + t * da * (255 - a) + den / 2) / den).coerceIn(0, 255)
        }
        pixels[i] = (oa shl 24) or (ch(16) shl 16) or (ch(8) shl 8) or ch(0)
    }

    /** Fills [x0, x1) x [y0, y1), clipped. */
    fun fill(x0: Int, y0: Int, x1: Int, y1: Int, argb: Int) {
        for (y in maxOf(0, y0) until minOf(height, y1)) for (x in maxOf(0, x0) until minOf(width, x1)) blend(x, y, argb)
    }
}

/**
 * Placement of the map inside a view: [u] pixels per map unit, the grid's top-left
 * ([gridX], [gridY]) and size, and the top-left of the whole drawing (labels included).
 */
class MapFrame(
    val u: Float,
    val left: Float,
    val top: Float,
    val gridX: Float,
    val gridY: Float,
    val gridW: Float,
    val gridH: Float,
) {
    fun x(mapX: Float) = gridX + mapX * u
    fun y(mapY: Float) = gridY + mapY * u
}

/** Geometry shared by the Android view and [TownMapRenderer] (all in map units, y down). */
object TownMapGeometry {
    /** Column labels above the grid / row labels left of it. */
    const val LABEL_UNITS = 9f
    /** Outer frame around the grid. */
    const val FRAME_UNITS = 2.5f
    /** Optional note line under the frame. */
    const val NOTE_UNITS = 6f

    /**
     * Fits labels + frame + grid (+ the closing 1-unit grid line on the right and bottom, + an
     * optional note line) into a [width] x [height] view with [margin] on every side, centred.
     * Null when there is no room.
     */
    fun frame(layout: TownLayout, width: Float, height: Float, margin: Float, withNote: Boolean): MapFrame? {
        val totalW = LABEL_UNITS + 2 * FRAME_UNITS + layout.widthUnits + 1
        val totalH = LABEL_UNITS + 2 * FRAME_UNITS + layout.heightUnits + 1 + if (withNote) NOTE_UNITS else 0f
        val availW = width - 2 * margin
        val availH = height - 2 * margin
        if (availW <= 0f || availH <= 0f) return null
        val u = minOf(availW / totalW, availH / totalH)
        val left = margin + (availW - totalW * u) / 2
        val top = margin + (availH - totalH * u) / 2
        return MapFrame(
            u = u, left = left, top = top,
            gridX = left + (LABEL_UNITS + FRAME_UNITS) * u,
            gridY = top + (LABEL_UNITS + FRAME_UNITS) * u,
            gridW = layout.widthUnits * u,
            gridH = layout.heightUnits * u,
        )
    }

    /** Dot radius of the player marker, in map units. */
    const val DOT_RADIUS = 1.1f
    /** Arrow tip distance from the dot centre, in dot radii. */
    const val ARROW_TIP = 2.5f
    const val ARROW_BASE = 0.6f
    const val ARROW_HALF_WIDTH = 0.95f

    /**
     * The facing arrow as a triangle (tipX, tipY, leftX, leftY, rightX, rightY) around centre
     * ([cx], [cy]) for unit direction ([dx], [dy]) and dot radius [r] (any unit).
     */
    fun arrow(cx: Float, cy: Float, dx: Float, dy: Float, r: Float): FloatArray {
        val px = -dy
        val py = dx
        val bx = cx + dx * r * ARROW_BASE
        val by = cy + dy * r * ARROW_BASE
        return floatArrayOf(
            cx + dx * r * ARROW_TIP, cy + dy * r * ARROW_TIP,
            bx + px * r * ARROW_HALF_WIDTH, by + py * r * ARROW_HALF_WIDTH,
            bx - px * r * ARROW_HALF_WIDTH, by - py * r * ARROW_HALF_WIDTH,
        )
    }

    /**
     * Map-unit rectangle (left, top, right, bottom) of house icon [h] drawn with [art]: [art]'s
     * size, centred on the house.
     */
    fun houseRect(h: HouseIcon, art: HouseArt): FloatArray {
        val half = art.units / 2f
        return floatArrayOf(h.cx - half, h.cy - half, h.cx + half, h.cy + half)
    }

    /** The art for [h]'s tier (the first tier's when that one is missing), or null without art. */
    fun artFor(h: HouseIcon, art: List<HouseArt>): HouseArt? = art.getOrNull(h.tier) ?: art.firstOrNull()

    /** Pixel span [start, end) of texel [i] when [count] texels cover [units] map units from [origin]. */
    fun texelSpan(origin: Float, i: Int, count: Int, units: Float, scale: Int): Pair<Int, Int> {
        val step = units / count * scale
        return (origin * scale + i * step).roundToInt() to (origin * scale + (i + 1) * step).roundToInt()
    }
}

/**
 * Composes the town map into a [Raster] at [scale] pixels per map unit: acre images from RAM in
 * the game's grid order, plain fallbacks where an image failed validation, building fallback
 * squares, and villager-house icons (lowest tier first). The player marker and acre highlight
 * are dynamic and drawn separately ([drawOverlay], or the Android view with the same geometry).
 *
 * The Android view uses [composeAcres] at scale 1 (one pixel per acre texel, 110x132 for a whole
 * town) and draws the house icons itself from their 16x16 art ([TownMapGeometry.houseRect]),
 * which gives the same picture as [compose] without building a large image.
 */
object TownMapRenderer {
    /** Behind the acre art (shows only through transparent texels). */
    const val BACKGROUND = 0xFF1E3C1E.toInt()
    /** Original plain fill for an acre whose image is unavailable, and its grid line. */
    const val FALLBACK_ACRE = 0xFF77B866.toInt()
    const val FALLBACK_LINE = 0xFF2F6A2F.toInt()
    /** Size of fallback squares (buildings), in map units. */
    const val FALLBACK_SQUARE = 10

    /** The whole static map: [composeAcres] plus the villager-house icons. */
    fun compose(layout: TownLayout, art: List<HouseArt>, houses: List<HouseIcon>, scale: Int): Raster {
        val ras = composeAcres(layout, scale)
        // Lowest tier first, as the game draws them (only matters where icons overlap).
        for (hs in houses.sortedBy { it.tier }) {
            val a = TownMapGeometry.artFor(hs, art) ?: continue
            val rect = TownMapGeometry.houseRect(hs, a)
            val left = rect[0]
            val top = rect[1]
            val icon = a.pixels
            if (icon == null) {
                ras.fill((left * scale).roundToInt(), (top * scale).roundToInt(),
                    (rect[2] * scale).roundToInt(), (rect[3] * scale).roundToInt(), a.fallbackColor)
                continue
            }
            for (iy in 0 until a.h) {
                val (y0, y1) = TownMapGeometry.texelSpan(top, iy, a.h, a.units.toFloat(), scale)
                for (ix in 0 until a.w) {
                    val (x0, x1) = TownMapGeometry.texelSpan(left, ix, a.w, a.units.toFloat(), scale)
                    ras.fill(x0, y0, x1, y1, icon[iy * a.w + ix])
                }
            }
        }
        return ras
    }

    /** Acre images (or their plain fallbacks) and building fallback squares, no house icons. */
    fun composeAcres(layout: TownLayout, scale: Int): Raster {
        require(scale > 0) { "scale must be positive" }
        val u = layout.acreUnits
        val ras = Raster(layout.widthUnits * scale, layout.heightUnits * scale)
        ras.pixels.fill(BACKGROUND)
        for (cell in layout.acres) {
            val ox = cell.col * u
            val oy = cell.row * u
            val px = cell.pixels
            if (px != null) {
                val xs = IntArray(layout.texW + 1) { TownMapGeometry.texelSpan(ox.toFloat(), it, layout.texW, u.toFloat(), scale).first }
                for (ty in 0 until layout.texH) {
                    val (y0, y1) = TownMapGeometry.texelSpan(oy.toFloat(), ty, layout.texH, u.toFloat(), scale)
                    for (tx in 0 until layout.texW) ras.fill(xs[tx], y0, xs[tx + 1], y1, px[ty * layout.texW + tx])
                }
            } else {
                ras.fill(ox * scale, oy * scale, (ox + u) * scale, (oy + u) * scale, FALLBACK_ACRE)
                ras.fill(ox * scale, oy * scale, (ox + u) * scale, (oy + 1) * scale, FALLBACK_LINE)
                ras.fill(ox * scale, oy * scale, (ox + 1) * scale, (oy + u) * scale, FALLBACK_LINE)
            }
        }
        for (b in layout.buildings) {
            if (b.inTexture) continue
            val r = layout.acreRect(b.blockX, b.blockZ)
            val cx = (r[0] + r[2]) / 2
            val cy = (r[1] + r[3]) / 2
            val h = FALLBACK_SQUARE / 2f
            ras.fill(((cx - h) * scale).roundToInt(), ((cy - h) * scale).roundToInt(),
                ((cx + h) * scale).roundToInt(), ((cy + h) * scale).roundToInt(), b.color)
        }
        return ras
    }

    /**
     * Raster version of the dynamic overlay (the Android view draws the same shapes with Canvas):
     * the acre box around the player's (or the indoor) acre and the player dot with its arrow.
     */
    fun drawOverlay(ras: Raster, state: TownMapState, scale: Int) {
        val layout = state.layout ?: return
        val p = state.player
        val box = p?.let { it.blockX to it.blockZ } ?: state.indoorAcre
        if (box != null && layout.inGrid(box.first, box.second)) {
            val r = layout.acreRect(box.first, box.second)
            val t = maxOf(2, scale * 3 / 8)
            val l = (r[0] * scale).toInt()
            val tp = (r[1] * scale).toInt()
            val rt = (r[2] * scale).toInt()
            val b = (r[3] * scale).toInt()
            ras.fill(l, tp, rt, tp + t, state.highlightColor)
            ras.fill(l, b - t, rt, b, state.highlightColor)
            ras.fill(l, tp, l + t, b, state.highlightColor)
            ras.fill(rt - t, tp, rt, b, state.highlightColor)
        }
        if (p == null) return
        val cx = p.mapX * scale
        val cy = p.mapY * scale
        val r = TownMapGeometry.DOT_RADIUS * scale
        val tri = TownMapGeometry.arrow(cx, cy, p.dirX, p.dirY, r)
        val outline = maxOf(1f, r * 0.3f)
        val reach = (r * TownMapGeometry.ARROW_TIP + outline + 2).toInt()
        for (y in (cy - reach).toInt()..(cy + reach).toInt()) {
            for (x in (cx - reach).toInt()..(cx + reach).toInt()) {
                val fx = x + 0.5f
                val fy = y + 0.5f
                val d = hypot(fx - cx, fy - cy)
                val inTri = inTriangle(fx, fy, tri)
                when {
                    d <= r || inTri -> ras.blend(x, y, state.markerColor)
                    d <= r + outline || nearTriangle(fx, fy, tri, outline) -> ras.blend(x, y, state.markerOutline)
                }
            }
        }
    }

    private fun inTriangle(x: Float, y: Float, t: FloatArray): Boolean {
        fun side(ax: Float, ay: Float, bx: Float, by: Float) = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        val s1 = side(t[0], t[1], t[2], t[3])
        val s2 = side(t[2], t[3], t[4], t[5])
        val s3 = side(t[4], t[5], t[0], t[1])
        return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0)
    }

    private fun nearTriangle(x: Float, y: Float, t: FloatArray, w: Float): Boolean {
        fun seg(ax: Float, ay: Float, bx: Float, by: Float): Float {
            val vx = bx - ax
            val vy = by - ay
            val len2 = vx * vx + vy * vy
            val k = if (len2 == 0f) 0f else (((x - ax) * vx + (y - ay) * vy) / len2).coerceIn(0f, 1f)
            return hypot(x - (ax + k * vx), y - (ay + k * vy))
        }
        return seg(t[0], t[1], t[2], t[3]) <= w || seg(t[2], t[3], t[4], t[5]) <= w || seg(t[4], t[5], t[0], t[1]) <= w
    }

    /** Map units to the acre (blockX, blockZ) containing that point, for tests/tools. */
    fun blockAt(layout: TownLayout, mapX: Float, mapY: Float): Pair<Int, Int> =
        (floor(mapX / layout.acreUnits).toInt() + layout.firstBlockX) to (floor(mapY / layout.acreUnits).toInt() + layout.firstBlockZ)
}
