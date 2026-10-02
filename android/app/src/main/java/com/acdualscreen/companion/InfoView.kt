package com.acdualscreen.companion

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.LoanState
import java.util.Locale

/**
 * The Info page: season, date, time and weather; the player bubble (name, town, town fruit); Bells
 * in the pocket, savings and the loan; the held item, birthday and today's fortune. Redraws only
 * when what it shows changes (not for the map marker or the tracker).
 */
@SuppressLint("ViewConstructor")
class InfoView(context: Context, onTab: (PanelTab) -> Unit, onSettings: () -> Unit) :
    AcPage(context, PanelTab.STATUS, onTab, onSettings) {

    private companion object {
        val MONTHS = arrayOf("Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.")
        val MONTHS_LONG = arrayOf("January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December")
        const val AVATAR_BG = 0xFF9BDCF7.toInt()
        const val AVATAR_RING = 0xFF2F7CC0.toInt()
    }

    override fun accept(s: GameState): Boolean {
        val bare = s.withoutExtras()
        if (bare == state) return false
        state = bare
        return true
    }

    override fun describeInTown(s: GameState): String =
        if (s.playerName != null) "Info: ${s.playerName} of ${s.town ?: "?"}, ${money(s.wallet)} Bells" else "Info: ${s.status}"

    private fun money(v: Long?): String = v?.let { String.format(Locale.US, "%,d", it) } ?: "?"

    override fun drawContent(c: Canvas, r: RectF) {
        val s = state
        drawHeader(c, r)
        drawBubble(c, RectF(r.left, r.top + 96f, r.right, r.top + 306f))
        val gridTop = r.top + 330f
        val colW = (r.width() - 24f) / 2
        if (s.playerIndex != null) {
            drawMoney(c, r.left, gridTop, colW)
            drawDetails(c, r.left + colW + 24f, gridTop, colW)
        } else {
            ac.font(40f, false, AcStyle.BROWN_SOFT, Paint.Align.CENTER)
            ac.line(c, "Visiting — player details are hidden", r.centerX(), gridTop + 120f, r.width())
        }
    }

    private fun drawHeader(c: Canvas, r: RectF) {
        val s = state
        val cy = r.top + 36f
        ac.font(60f, true, AcStyle.PINK)
        ac.line(c, s.season ?: "", r.left, cy, 400f)

        val ck = s.clock
        val time = ck?.let {
            val h12 = if (it.hour % 12 == 0) 12 else it.hour % 12
            String.format(Locale.US, "%d:%02d %s", h12, it.minute, if (it.hour < 12) "AM" else "PM")
        } ?: "--:--"
        val sub = listOfNotNull(time, s.weather).joinToString(" · ")
        val date = ck?.let {
            val wd = it.weekdayLabel?.take(3)?.let { w -> "$w., " } ?: ""
            "$wd${MONTHS.getOrElse(it.month - 1) { "?" }} ${it.day}"
        } ?: "?"
        // Right-aligned: [icon] date  time · weather
        ac.font(36f, false, AcStyle.BROWN_SOFT, Paint.Align.RIGHT)
        val subW = ac.line(c, sub, r.right, cy, 380f)
        ac.font(42f, true, AcStyle.BROWN, Paint.Align.RIGHT)
        val dateRight = r.right - subW - 20f
        val dateW = ac.line(c, date, dateRight, cy, 300f)
        weatherIcon(c, dateRight - dateW - 20f - 64f, cy - 32f, 64f)
    }

    private fun weatherIcon(c: Canvas, x: Float, y: Float, size: Float) {
        val w = state.weather?.lowercase() ?: return
        when {
            w.startsWith("rain") -> ac.cloud(c, x, y, size, 3, false)
            w.startsWith("snow") -> ac.cloud(c, x, y, size, 3, true)
            w.startsWith("cherry") -> ac.blossom(c, x, y, size)
            else -> ac.sun(c, x, y, size)
        }
    }

    private fun drawBubble(c: Canvas, b: RectF) {
        val s = state
        ac.roundBox(c, b.left, b.top, b.right, b.bottom, 60f, AcStyle.CREAM, AcStyle.ORANGE, 8f)
        val acx = b.left + 8f + 36f + 75f
        val acy = b.centerY()
        ac.ringCircle(c, acx, acy, 75f, AVATAR_BG, AVATAR_RING, 7f)
        ac.leaf(c, acx - 45f, acy - 45f, 90f)

        val tx = acx + 75f + 32f
        val maxW = b.right - tx - 40f
        val slot = s.playerIndex?.let { "Player ${it + 1} · Resident" } ?: "Visitor"
        ac.font(32f, false, AcStyle.BROWN_SOFT)
        ac.line(c, slot, tx, acy - 66f, maxW)
        ac.font(92f, true, AcStyle.BROWN)
        ac.line(c, s.playerName ?: "?", tx, acy + 2f, maxW)
        ac.font(40f, false, AcStyle.BROWN)
        val of = "of "
        val ofW = ac.line(c, of, tx, acy + 70f)
        ac.font(40f, true, AcStyle.BROWN)
        val townW = ac.line(c, s.town ?: "?", tx + ofW, acy + 70f, maxW - ofW - 80f)
        val f = s.fruit ?: return
        val fx = tx + ofW + townW + 14f + 30f
        ac.ringCircle(c, fx, acy + 70f, 30f, AcStyle.WHITE, AcStyle.ORANGE, 4f)
        ac.fruit(c, f, fx - 21f, acy + 70f - 21f, 42f)
    }

    private fun drawMoney(c: Canvas, left: Float, top: Float, w: Float) {
        val s = state
        val rows = listOf(
            Triple("Pocket", 0, s.wallet),
            Triple("Savings", 1, s.bank),
            Triple("Current loan", 2, s.loan),
        )
        rows.forEachIndexed { i, (label, icon, v) ->
            val t = top + i * 120f
            val b = t + 104f
            val cy = (t + b) / 2
            ac.roundBox(c, left, t, left + w, b, 40f, AcStyle.ROW_BG, AcStyle.ROW_BORDER, 5f)
            val ix = left + 28f
            when (icon) {
                0 -> ac.bellBag(c, ix, cy - 26f, 52f)
                1 -> ac.bank(c, ix, cy - 26f, 52f)
                else -> ac.house(c, ix, cy - 26f, 52f)
            }
            val tx = ix + 52f + 20f
            val maxW = left + w - 28f - tx
            ac.font(30f, false, AcStyle.BROWN_SOFT)
            ac.line(c, label, tx, cy - 24f, maxW)
            if (icon == 2) drawLoanValue(c, tx, cy + 18f, maxW) else drawBells(c, money(v), AcStyle.BROWN, tx, cy + 18f, maxW)
        }
    }

    private fun drawBells(c: Canvas, value: String, color: Int, x: Float, cy: Float, maxW: Float) {
        ac.font(50f, true, color)
        val w = ac.line(c, value, x, cy, maxW - 90f)
        ac.font(30f, false, AcStyle.BROWN_SOFT)
        ac.line(c, " Bells", x + w, cy + 6f)
    }

    private fun drawLoanValue(c: Canvas, x: Float, cy: Float, maxW: Float) {
        val s = state
        when (s.loanState) {
            LoanState.PAID_OFF -> {
                ac.font(34f, true, AcStyle.GREEN_DARK)
                val tw = ac.text.measureText("All paid off!")
                val bw = 14f + 30f + 10f + tw + 22f
                ac.roundBox(c, x, cy - 26f, x + bw, cy + 26f, 26f, AcStyle.GREEN_PALE, AcStyle.GREEN_DARK, 4f)
                ac.check(c, x + 14f, cy - 15f, 30f, AcStyle.GREEN_DARK)
                ac.font(34f, true, AcStyle.GREEN_DARK)
                ac.line(c, "All paid off!", x + 14f + 30f + 10f, cy, maxW)
            }
            LoanState.NONE -> {
                ac.font(44f, true, AcStyle.BROWN_SOFT)
                ac.line(c, "No loan", x, cy, maxW)
            }
            else -> drawBells(c, money(s.loan), AcStyle.LOAN, x, cy, maxW)
        }
    }

    private fun drawDetails(c: Canvas, left: Float, top: Float, w: Float) {
        val s = state
        val birthday = s.birthday?.let { (m, d) -> "${MONTHS_LONG[m - 1]} $d" }
        val rows = listOf(
            Triple("Holding", s.heldItem, "Nothing"),
            Triple("Birthday", birthday, "Not set"),
            Triple("Today's fortune", s.fortune, "No fortune yet"),
        )
        rows.forEachIndexed { i, (label, value, none) ->
            val t = top + i * 120f
            val b = t + 104f
            val cy = (t + b) / 2
            ac.roundBox(c, left, t, left + w, b, 40f, AcStyle.CREAM, AcStyle.ORANGE, 6f)
            ac.font(30f, false, AcStyle.BROWN_SOFT)
            ac.line(c, label, left + 30f, cy - 24f, w - 60f)
            val color = when {
                value == null -> AcStyle.BROWN_SOFT
                i == 2 -> AcStyle.PINK
                else -> AcStyle.BROWN
            }
            ac.font(if (value == null) 40f else 46f, value != null, color)
            ac.line(c, value ?: none, left + 30f, cy + 18f, w - 60f)
        }
    }
}
