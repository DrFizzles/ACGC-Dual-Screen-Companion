package com.acdualscreen.companion

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.ShineSpot
import java.util.Locale

/**
 * The Tracker page: which neighbours the player has talked to today (a green ring with a check,
 * or a red ring), how many of the day's fossils have been dug up, and whether the glowing spot has
 * been dug. It never shows where anything is buried.
 *
 * Portraits are placeholders (the villager's initial on a colour picked from its id): the game's
 * villager art is not read from RAM.
 */
@SuppressLint("ViewConstructor")
class TrackerView(context: Context, onTab: (PanelTab) -> Unit, onSettings: () -> Unit) :
    AcPage(context, PanelTab.TRACKER, onTab, onSettings) {

    private companion object {
        val MONTHS = arrayOf("Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")
        const val COLS = 8
        const val PORTRAIT_R = 50f
    }

    override fun accept(s: GameState): Boolean {
        val cur = state
        if (s.phase == cur.phase && s.status == cur.status && s.daily == cur.daily && s.villagers == cur.villagers &&
            s.clock?.day == cur.clock?.day
        ) return false
        state = s.copy(map = null)
        return true
    }

    override fun describeInTown(s: GameState): String {
        val d = s.daily ?: return "Tracker: ${s.status}"
        val talked = d.talkedToday.values.count { it == true }
        return "Tracker: talked to $talked of ${s.villagers.size} neighbours, ${d.fossilsDug ?: "?"} of ${d.fossilMax} fossils dug"
    }

    override fun drawContent(c: Canvas, r: RectF) {
        val s = state
        val cy = r.top + 36f
        ac.font(50f, true, AcStyle.BROWN)
        ac.text.letterSpacing = 0.04f
        ac.line(c, "DAILY TRACKER", r.left, cy, 600f)
        ac.text.letterSpacing = 0f
        val date = s.clock?.let { ck ->
            val wd = ck.weekdayLabel?.take(3)?.let { "$it., " } ?: ""
            "$wd${MONTHS.getOrElse(ck.month - 1) { "?" }} ${ck.day}"
        } ?: ""
        ac.font(42f, true, AcStyle.BROWN, Paint.Align.RIGHT)
        ac.line(c, date, r.right, cy, 420f)

        if (s.playerIndex == null) {
            ac.font(40f, false, AcStyle.BROWN_SOFT, Paint.Align.CENTER)
            ac.line(c, "The tracker needs a resident player", r.centerX(), r.centerY(), r.width())
            return
        }
        val nBottom = drawNeighbors(c, RectF(r.left, r.top + 96f, r.right, 0f))
        val row = RectF(r.left, nBottom + 24f, r.right, nBottom + 24f + 248f)
        val fossilW = 620f
        drawFossils(c, RectF(row.left, row.top, row.left + fossilW, row.bottom))
        drawShine(c, RectF(row.left + fossilW + 20f, row.top, row.right, row.bottom))
        val note = s.daily?.note.orEmpty()
        if (note.isNotEmpty()) {
            ac.font(24f, false, AcStyle.BROWN_SOFT)
            ac.line(c, note, r.left, row.bottom + 20f, r.width())
        }
    }

    /** Returns the panel's bottom edge. */
    private fun drawNeighbors(c: Canvas, area: RectF): Float {
        val s = state
        val villagers = s.villagers
        val talked = s.daily?.talkedToday
        val rows = ((villagers.size + COLS - 1) / COLS).coerceIn(1, 2)
        val bottom = area.top + 6f + 20f + 56f + 14f + rows * 140f + (rows - 1) * 14f + 20f + 6f
        ac.roundBox(c, area.left, area.top, area.right, bottom, 48f, AcStyle.CREAM, AcStyle.ORANGE, 6f)
        val inL = area.left + 6f + 28f
        val inR = area.right - 6f - 28f
        val hy = area.top + 6f + 20f + 28f
        ac.font(42f, true, AcStyle.BROWN)
        ac.line(c, "Neighbors", inL, hy)

        // "7 / 12 talked" pill.
        val n = talked?.values?.count { it == true }
        val pill = if (n == null) "…" else "$n / ${villagers.size} talked"
        ac.font(32f, true, AcStyle.GREEN_DARK)
        val pw = ac.text.measureText(pill) + 44f
        ac.roundBox(c, inR - pw, hy - 26f, inR, hy + 26f, 26f, AcStyle.GREEN_PALE, AcStyle.GREEN_DARK, 4f)
        ac.font(32f, true, AcStyle.GREEN_DARK, Paint.Align.CENTER)
        ac.line(c, pill, inR - pw / 2, hy)

        if (villagers.isEmpty()) {
            ac.font(36f, false, AcStyle.BROWN_SOFT)
            ac.line(c, "No neighbors yet", inL, hy + 110f)
            return bottom
        }
        val cellW = (inR - inL - (COLS - 1) * 10f) / COLS
        val gridTop = area.top + 6f + 20f + 56f + 14f
        villagers.take(COLS * 2).forEachIndexed { i, v ->
            val col = i % COLS
            val row = i / COLS
            val cx = inL + col * (cellW + 10f) + cellW / 2
            val cy = gridTop + row * 154f + PORTRAIT_R
            val status = talked?.get(v.slot)
            val ring = when (status) {
                true -> AcStyle.GREEN
                false -> AcStyle.ALERT
                null -> AcStyle.UNKNOWN
            }
            val bg = AcStyle.PASTELS[(v.id and 0xFF) % AcStyle.PASTELS.size]
            ac.ringCircle(c, cx, cy, PORTRAIT_R, bg, ring)
            val name = v.name
            ac.font(46f, true, AcStyle.BROWN, Paint.Align.CENTER)
            ac.line(c, name?.take(1)?.uppercase(Locale.US) ?: "?", cx, cy)
            if (status == true) ac.checkBadge(c, cx + PORTRAIT_R - 6f, cy + PORTRAIT_R - 6f)
            ac.font(26f, true, AcStyle.BROWN, Paint.Align.CENTER)
            ac.line(c, name ?: "???", cx, cy + PORTRAIT_R + 24f, cellW)
        }
        return bottom
    }

    private fun drawFossils(c: Canvas, b: RectF) {
        val d = state.daily
        ac.roundBox(c, b.left, b.top, b.right, b.bottom, 48f, AcStyle.CREAM, AcStyle.ORANGE, 6f)
        val inL = b.left + 6f + 28f
        val inR = b.right - 6f - 28f
        val hy = b.top + 6f + 20f + 25f
        ac.font(42f, true, AcStyle.BROWN)
        ac.line(c, "Fossils", inL, hy)
        val max = d?.fossilMax ?: 5
        val dug = d?.fossilsDug
        ac.font(32f, true, AcStyle.BROWN_SOFT, Paint.Align.RIGHT)
        ac.line(c, "${dug ?: "?"} / $max dug", inR, hy)
        val cy = hy + 25f + 14f + 48f
        val step = 96f + 18f
        for (i in 0 until max.coerceAtMost(5)) {
            val cx = inL + 48f + i * step
            when {
                dug == null -> {
                    ac.ringCircle(c, cx, cy, 48f, AcStyle.DIM_FILL, AcStyle.UNKNOWN)
                    ac.fossil(c, cx - 29f, cy - 29f, 58f, 90)
                }
                i < dug -> {
                    ac.ringCircle(c, cx, cy, 48f, AcStyle.WHITE, AcStyle.GREEN)
                    ac.fossil(c, cx - 29f, cy - 29f, 58f)
                    ac.checkBadge(c, cx + 42f, cy + 42f)
                }
                else -> {
                    ac.ringCircle(c, cx, cy, 48f, AcStyle.DIM_FILL, AcStyle.ALERT)
                    ac.fossil(c, cx - 29f, cy - 29f, 58f, 102)
                }
            }
        }
    }

    private fun drawShine(c: Canvas, b: RectF) {
        val spot = state.daily?.shineSpot ?: ShineSpot.UNKNOWN
        ac.roundBox(c, b.left, b.top, b.right, b.bottom, 48f, AcStyle.CREAM, AcStyle.ORANGE, 6f)
        ac.font(38f, true, AcStyle.BROWN, Paint.Align.CENTER)
        ac.line(c, "Glowing spot", b.centerX(), b.top + 6f + 20f + 25f, b.width() - 48f)
        val (ring, bg, label, color) = when (spot) {
            ShineSpot.DUG -> Quad(AcStyle.GREEN, AcStyle.WHITE, "Dug up", AcStyle.GREEN_DARK)
            ShineSpot.NOT_DUG -> Quad(AcStyle.ALERT, AcStyle.WHITE, "Not dug", AcStyle.LOAN)
            ShineSpot.NONE_TODAY -> Quad(AcStyle.UNKNOWN, AcStyle.DIM_FILL, "None today", AcStyle.BROWN_SOFT)
            ShineSpot.UNKNOWN -> Quad(AcStyle.UNKNOWN, AcStyle.DIM_FILL, "Unknown", AcStyle.BROWN_SOFT)
        }
        ac.font(32f, true, color)
        val tw = minOf(ac.text.measureText(label), b.width() - 48f - 96f - 16f)
        val total = 96f + 16f + tw
        val cx = b.centerX() - total / 2 + 48f
        val cy = b.top + 6f + 20f + 50f + 14f + 48f
        ac.ringCircle(c, cx, cy, 48f, bg, ring)
        val dim = spot == ShineSpot.NONE_TODAY || spot == ShineSpot.UNKNOWN
        ac.sparkle(c, cx - 30f, cy - 30f, 60f, if (dim) 90 else 255)
        if (spot == ShineSpot.DUG) ac.checkBadge(c, cx + 42f, cy + 42f)
        ac.font(32f, true, color)
        ac.line(c, label, cx + 48f + 16f, cy, tw)
    }

    private data class Quad(val ring: Int, val bg: Int, val label: String, val color: Int)
}
