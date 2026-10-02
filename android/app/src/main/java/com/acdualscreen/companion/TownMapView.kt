package com.acdualscreen.companion

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.HouseArt
import com.acdualscreen.companion.core.TownLayout
import com.acdualscreen.companion.core.TownMapGeometry
import com.acdualscreen.companion.core.TownMapRenderer
import com.acdualscreen.companion.core.TownMapState

/**
 * The Map page, laid out like the game's town map screen: the season and "TOWN MAP" with a bubble
 * naming the player's acre and the neighbours who live there, a key, and the map with outlined
 * column numbers and row letters. The map itself is the 5x6 acre images decoded from RAM (with
 * their buildings), the villager-house icons, the player's acre outlined and a dot with a facing
 * arrow. Nothing else: no items, no buried spots.
 *
 * The acre art is one small bitmap at its native size (one pixel per texel), rebuilt only when the
 * layout object changes; the house icons are their own 16x16 bitmaps. Both are scaled up without
 * filtering, so the pixel art stays crisp.
 */
@SuppressLint("ViewConstructor")
class TownMapView(context: Context, compact: Boolean, showTabs: Boolean, onTab: (PanelTab) -> Unit) :
    AcPage(context, compact, showTabs, PanelTab.MAP, onTab) {

    private companion object {
        const val LEFT_COL = 372f
        const val GAP = 32f
        const val ROW_LABEL_W = 56f
        const val COL_LABEL_H = 64f
        const val FRAME = 10f
        /** Below this many pixels per map unit the art is shrunk, so filter it instead. */
        const val FILTER_BELOW_U = 2f
        val BUILDING_LABELS = mapOf(
            "player_houses" to "Houses", "shop" to "Nook's", "police" to "Police", "post_office" to "Post office",
            "station" to "Station", "dump" to "Dump", "museum" to "Museum", "tailor" to "Tailor",
            "wishing_well" to "Well", "dock" to "Dock",
        )
    }

    private val bitmapPaint = Paint()
    private val path = Path()
    private val rect = RectF()
    private val src = Rect()

    private var acres: Bitmap? = null
    private var acresLayout: TownLayout? = null
    private var icons: List<Bitmap?> = emptyList()
    private var iconsArt: List<HouseArt>? = null

    override fun accept(s: GameState): Boolean {
        // Only what this page draws: the clock, pockets and so on must not redraw it.
        val cur = state
        if (s.phase == cur.phase && s.status == cur.status && s.map == cur.map && s.villagers == cur.villagers &&
            s.season == cur.season
        ) return false
        state = s.copy(daily = null)
        if (s.map?.layout == null) dropBitmaps()
        return true
    }

    override fun describeInTown(s: GameState): String {
        val m = s.map ?: return "Map: ${s.status}"
        val p = m.player
        val acre = p?.let { m.layout?.acreLabel(it.blockX, it.blockZ) }
        return if (acre != null) "Town map, you are in acre $acre" else "Town map"
    }

    override fun onDetachedFromWindow() {
        dropBitmaps()
        super.onDetachedFromWindow()
    }

    private fun dropBitmaps() {
        acres?.recycle()
        acres = null
        acresLayout = null
        icons.forEach { it?.recycle() }
        icons = emptyList()
        iconsArt = null
    }

    /** The acre art at one pixel per texel; reuses the bitmap when the size is unchanged. */
    private fun acresFor(layout: TownLayout): Bitmap {
        val cur = acres
        if (cur != null && acresLayout === layout) return cur
        val ras = TownMapRenderer.composeAcres(layout, 1)
        val bmp = if (cur != null && cur.isMutable && cur.width == ras.width && cur.height == ras.height) {
            cur
        } else {
            cur?.recycle()
            Bitmap.createBitmap(ras.width, ras.height, Bitmap.Config.ARGB_8888)
        }
        bmp.setPixels(ras.pixels, 0, ras.width, 0, 0, ras.width, ras.height)
        acres = bmp
        acresLayout = layout
        return bmp
    }

    /** One bitmap per house tier (null where the icon could not be read: draw a square). */
    private fun iconsFor(art: List<HouseArt>): List<Bitmap?> {
        if (iconsArt === art) return icons
        icons.forEach { it?.recycle() }
        icons = art.map { a -> a.pixels?.let { Bitmap.createBitmap(it, a.w, a.h, Bitmap.Config.ARGB_8888) } }
        iconsArt = art
        return icons
    }

    override fun drawContent(c: Canvas, r: RectF) {
        val m = state.map
        val layout = m?.layout
        drawSide(c, r, m, layout)
        val area = RectF(r.left + LEFT_COL + GAP, r.top, r.right, r.bottom)
        when {
            m == null -> centred(c, area, "Loading map…")
            layout == null -> centred(c, area, m.note.ifEmpty { "Map unavailable" })
            else -> drawMap(c, area, m, layout)
        }
    }

    private fun centred(c: Canvas, r: RectF, msg: String) {
        ac.font(44f, true, AcStyle.BROWN_SOFT, Paint.Align.CENTER)
        ac.line(c, msg, r.centerX(), r.centerY(), r.width())
    }

    // ---------------------------------------------------------------- left column

    private fun drawSide(c: Canvas, r: RectF, m: TownMapState?, layout: TownLayout?) {
        val x = r.left
        val w = LEFT_COL
        ac.font(52f, true, AcStyle.PINK)
        ac.line(c, state.season ?: "", x, r.top + 30f, w)
        ac.font(50f, true, AcStyle.BROWN)
        ac.text.letterSpacing = 0.04f
        ac.line(c, "TOWN MAP", x, r.top + 92f, w)
        ac.text.letterSpacing = 0f

        // The acre bubble: where the player is, and who lives there.
        val bTop = r.top + 140f
        val bBottom = r.top + 470f
        ac.roundBox(c, x, bTop, x + w, bBottom, 64f, AcStyle.CREAM, AcStyle.ORANGE, 8f)
        val cx = x + w / 2
        ac.font(42f, true, AcStyle.BROWN, Paint.Align.CENTER)
        ac.line(c, "Acre", cx, bTop + 62f)
        val acre = m?.player?.let { it.blockX to it.blockZ } ?: m?.indoorAcre
        val rowL = acre?.let { layout?.rowLabels?.getOrNull(it.second - layout.firstBlockZ) }
        val colL = acre?.let { layout?.colLabels?.getOrNull(it.first - layout.firstBlockX) }
        val ly = bTop + 170f
        if (rowL != null && colL != null) {
            ac.outlined(c, rowL, cx - 80f, ly, 104f, AcStyle.ROW_FILL, AcStyle.ROW_EDGE, 12f)
            ac.font(64f, true, AcStyle.BROWN, Paint.Align.CENTER)
            ac.line(c, "–", cx, ly)
            ac.outlined(c, colL, cx + 80f, ly, 104f, AcStyle.COL_FILL, AcStyle.COL_EDGE, 12f)
        } else {
            ac.font(64f, true, AcStyle.BROWN_SOFT, Paint.Align.CENTER)
            ac.line(c, "?", cx, ly)
        }
        // Neighbours whose house is in this acre (up to two).
        val names = if (acre == null || m == null) emptyList() else m.houses
            .filter { it.blockX == acre.first && it.blockZ == acre.second }
            .mapNotNull { h -> state.villagers.firstOrNull { it.slot == h.slot }?.name }
            .take(2)
        names.forEachIndexed { i, n ->
            val y = bTop + 262f + i * 54f
            ac.font(40f, true, AcStyle.VILLAGER_NAME)
            val tw = minOf(ac.text.measureText(n), w - 120f)
            val left = cx - (44f + 12f + tw) / 2
            ac.homeIcon(c, left, y - 22f, 44f, villagerHouseColor())
            ac.line(c, n, left + 56f, y, w - 120f)
        }

        drawKey(c, x, bBottom + 24f, w, r.bottom, m, layout)
    }

    /**
     * The key, in two columns: the player marker and a villager house as the map draws them, then
     * every building in this town shown as its own acre tile from the game's map art.
     */
    private fun drawKey(c: Canvas, x: Float, top: Float, w: Float, bottom: Float, m: TownMapState?, layout: TownLayout?) {
        val entries = ArrayList<Pair<String, (Float, Float, Float) -> Unit>>()
        entries += "You" to { ix, iy, size -> drawMarker(c, ix + size / 2, iy + size / 2, size * 0.22f, 0f, 1f, m) }
        val houseIcon = m?.houseArt?.let { iconsFor(it) }?.firstOrNull()
        entries += "Villagers" to { ix, iy, size ->
            if (houseIcon != null) {
                bitmapPaint.isFilterBitmap = false
                rect.set(ix, iy, ix + size, iy + size)
                c.drawBitmap(houseIcon, null, rect, bitmapPaint)
            } else {
                ac.homeIcon(c, ix, iy, size, villagerHouseColor())
            }
        }
        if (layout != null) {
            val art = acresFor(layout)
            for (b in layout.buildings.distinctBy { it.key }) {
                val label = BUILDING_LABELS[b.key] ?: b.label
                entries += label to { ix, iy, size ->
                    val ar = layout.acreRect(b.blockX, b.blockZ)
                    if (b.inTexture) {
                        // One pixel per map unit in the acre bitmap.
                        src.set(ar[0].toInt(), ar[1].toInt(), ar[2].toInt(), ar[3].toInt())
                        rect.set(ix, iy, ix + size, iy + size)
                        bitmapPaint.isFilterBitmap = false
                        c.drawBitmap(art, src, rect, bitmapPaint)
                    } else {
                        ac.fill.color = b.color
                        c.drawRect(ix + size * 0.2f, iy + size * 0.2f, ix + size * 0.8f, iy + size * 0.8f, ac.fill)
                    }
                }
            }
        }
        val rows = (entries.size + 1) / 2
        val rowH = minOf(56f, (bottom - top - 32f) / rows)
        val size = rowH - 10f
        ac.roundBox(c, x, top, x + w, top + rows * rowH + 32f, 36f, AcStyle.CREAM, AcStyle.ORANGE, 6f)
        val colW = (w - 40f) / 2
        entries.forEachIndexed { i, (label, draw) ->
            val ex = x + 20f + (i % 2) * colW
            val ey = top + 16f + (i / 2) * rowH
            draw(ex, ey + (rowH - size) / 2, size)
            ac.font(minOf(28f, rowH * 0.5f), false, AcStyle.BROWN)
            ac.line(c, label, ex + size + 10f, ey + rowH / 2, colW - size - 14f)
        }
    }

    /** The player dot with its facing arrow, as on the map. */
    private fun drawMarker(c: Canvas, cx: Float, cy: Float, radius: Float, dirX: Float, dirY: Float, m: TownMapState?) {
        val color = m?.markerColor ?: 0xFFFF2D2D.toInt()
        val outline = m?.markerOutline ?: -1
        val a = TownMapGeometry.arrow(cx, cy, dirX, dirY, radius)
        path.reset()
        path.moveTo(a[0], a[1])
        path.lineTo(a[2], a[3])
        path.lineTo(a[4], a[5])
        path.close()
        ac.stroke.color = outline
        ac.stroke.strokeWidth = maxOf(3f, radius * 0.3f) * 2
        c.drawPath(path, ac.stroke)
        c.drawCircle(cx, cy, radius, ac.stroke)
        ac.fill.color = color
        c.drawPath(path, ac.fill)
        c.drawCircle(cx, cy, radius, ac.fill)
    }

    private fun villagerHouseColor(): Int = state.map?.houseArt?.firstOrNull()?.fallbackColor ?: 0xFF3C6FE0.toInt()

    // ---------------------------------------------------------------- the map

    private fun drawMap(c: Canvas, area: RectF, m: TownMapState, layout: TownLayout) {
        // One extra unit right and below: the grid line the acre art lacks on those edges.
        val availW = area.width() - ROW_LABEL_W - 6f - 2 * FRAME - 8f
        val availH = area.height() - COL_LABEL_H - 4f - 2 * FRAME - 10f
        val u = minOf(availW / (layout.widthUnits + 1), availH / (layout.heightUnits + 1))
        val gw = layout.widthUnits * u
        val gh = layout.heightUnits * u
        val gx = area.left + ROW_LABEL_W + 6f + FRAME
        val gy = area.top + COL_LABEL_H + 4f + FRAME

        // Frame with its shadow, then the grid line colour under the art.
        ac.fill.color = AcStyle.MAP_SHADOW
        rect.set(gx - FRAME + 6f, gy - FRAME + 8f, gx + gw + u + FRAME + 6f, gy + gh + u + FRAME + 8f)
        c.drawRoundRect(rect, 10f, 10f, ac.fill)
        ac.fill.color = AcStyle.MAP_FRAME
        rect.set(gx - FRAME, gy - FRAME, gx + gw + u + FRAME, gy + gh + u + FRAME)
        c.drawRoundRect(rect, 10f, 10f, ac.fill)
        ac.fill.color = layout.lineColor
        c.drawRect(gx, gy, gx + gw + u, gy + gh + u, ac.fill)
        bitmapPaint.isFilterBitmap = u * pageScale < FILTER_BELOW_U
        rect.set(gx, gy, gx + gw, gy + gh)
        c.drawBitmap(acresFor(layout), null, rect, bitmapPaint)

        // Villager houses, lowest tier first as the game draws them.
        val tierIcons = iconsFor(m.houseArt)
        for (h in m.houses.sortedBy { it.tier }) {
            val a = TownMapGeometry.artFor(h, m.houseArt) ?: continue
            val hr = TownMapGeometry.houseRect(h, a)
            rect.set(gx + hr[0] * u, gy + hr[1] * u, gx + hr[2] * u, gy + hr[3] * u)
            val icon = tierIcons.getOrNull(if (h.tier in m.houseArt.indices) h.tier else 0)
            if (icon != null) {
                c.drawBitmap(icon, null, rect, bitmapPaint)
            } else {
                ac.fill.color = a.fallbackColor
                c.drawRect(rect, ac.fill)
            }
        }

        // Outlined column numbers across the top, row letters down the left.
        val acreU = layout.acreUnits * u
        layout.colLabels.forEachIndexed { i, l ->
            ac.outlined(c, l, gx + (i + 0.5f) * acreU, area.top + COL_LABEL_H / 2, 60f, AcStyle.COL_FILL, AcStyle.COL_EDGE, 9f)
        }
        layout.rowLabels.forEachIndexed { i, l ->
            ac.outlined(c, l, area.left + ROW_LABEL_W / 2, gy + (i + 0.5f) * acreU, 60f, AcStyle.ROW_FILL, AcStyle.ROW_EDGE, 9f)
        }

        // Labels of fallback building markers (the squares themselves are in the bitmap).
        for (b in layout.buildings) {
            if (b.inTexture || b.label.isEmpty()) continue
            val ar = layout.acreRect(b.blockX, b.blockZ)
            ac.font(3.6f * u, false, AcStyle.WHITE, Paint.Align.CENTER)
            ac.line(c, b.label, gx + (ar[0] + ar[2]) / 2 * u, gy + (ar[3] - 1.8f) * u, acreU)
        }

        // The player's acre (or, indoors, the acre of the building's door): white under pink.
        val p = m.player
        val box = p?.let { it.blockX to it.blockZ } ?: m.indoorAcre
        if (box != null && layout.inGrid(box.first, box.second)) {
            val ar = layout.acreRect(box.first, box.second)
            rect.set(gx + ar[0] * u + 5f, gy + ar[1] * u + 5f, gx + ar[2] * u - 5f, gy + ar[3] * u - 5f)
            ac.stroke.color = AcStyle.WHITE
            ac.stroke.strokeWidth = 10f
            c.drawRoundRect(rect, 6f, 6f, ac.stroke)
            ac.stroke.color = m.highlightColor
            ac.stroke.strokeWidth = 6f
            c.drawRoundRect(rect, 6f, 6f, ac.stroke)
        }

        if (p != null) {
            drawMarker(c, gx + p.mapX * u, gy + p.mapY * u, maxOf(TownMapGeometry.DOT_RADIUS * u, 9f), p.dirX, p.dirY, m)
        }
    }
}
