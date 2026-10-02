package com.acdualscreen.companion

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.Phase

/**
 * One page of the panel in the game-menu style ([AcStyle]): grass, the Info | Map | Tracker tabs
 * with a settings button, and the red-framed paper card. Subclasses draw the card's content in
 * design pixels; the whole page is scaled uniformly to fit the view and centred.
 *
 * Until the game's data is in, the page shows the waiting screen instead: no tabs (there is
 * nothing to switch between), the card filling the screen, and the settings button in its corner.
 *
 * A tap on a tab calls [onTab], a tap on the gear [onSettings]; a long press anywhere else is the
 * view's long-click.
 */
@SuppressLint("ViewConstructor")
abstract class AcPage(
    context: Context,
    private val page: PanelTab,
    private val onTab: (PanelTab) -> Unit,
    private val onSettings: () -> Unit,
) : View(context) {

    private companion object {
        const val DOT_MS = 450L
        const val BADGE_BG = 0xFF9BDCF7.toInt()
        const val BADGE_RING = 0xFF2F7CC0.toInt()
        const val GEAR = 96f
    }

    private sealed interface Hit {
        data class Tab(val tab: PanelTab) : Hit
        data object Settings : Hit
    }

    protected val ac = AcPainter(context)
    @JvmField protected var state: GameState = GameState.of(Phase.CONNECTING, "Starting…")

    /** Real pixels per design pixel (set on every draw). */
    protected var pageScale = 1f
        private set
    private var offX = 0f
    private var offY = 0f
    private val tabRects = LinkedHashMap<PanelTab, RectF>()
    /** The gear: right of the tabs in town, in the card's top-right corner while waiting. */
    private val gearInTown = RectF(AcStyle.W - 24f - GEAR, 24f, AcStyle.W - 24f, 120f)
    private val gearWaiting = RectF(AcStyle.W - 62f - GEAR, 62f, AcStyle.W - 62f, 62f + GEAR)
    private var down: Hit? = null
    private var downAt = 0L

    private val waiting: Boolean get() = state.phase != Phase.IN_TOWN

    init {
        isClickable = true
        isLongClickable = true
        val labels = listOf(PanelTab.STATUS, PanelTab.MAP, PanelTab.TRACKER)
        val tabW = (AcStyle.W - 48f - GEAR - 3 * 20f) / 3
        labels.forEachIndexed { i, t ->
            val l = 24f + i * (tabW + 20f)
            tabRects[t] = RectF(l, 24f, l + tabW, 120f)
        }
    }

    /** What this page needs from the state; true when it changed (and the page redraws). */
    protected abstract fun accept(s: GameState): Boolean

    /** Draws the in-town content into [r] (design pixels, inside the card's padding). */
    protected abstract fun drawContent(c: Canvas, r: RectF)

    fun setState(s: GameState) {
        if (accept(s)) {
            state = s
            contentDescription = if (s.phase == Phase.IN_TOWN) describeInTown(s) else describe(s)
            invalidate()
        }
    }

    /** A short spoken summary of the page (TalkBack). */
    protected open fun describeInTown(s: GameState): String = s.status

    override fun onDraw(canvas: Canvas) {
        if (width == 0 || height == 0) return
        pageScale = minOf(width / AcStyle.W, height / AcStyle.H_TABS)
        offX = (width - AcStyle.W * pageScale) / 2
        offY = (height - AcStyle.H_TABS * pageScale) / 2
        ac.grass(canvas, width.toFloat(), height.toFloat())
        canvas.save()
        canvas.translate(offX, offY)
        canvas.scale(pageScale, pageScale)
        val top = if (waiting) 24f else 140f
        val bottom = AcStyle.H_TABS - 24f
        if (!waiting) drawTabs(canvas)
        ac.shadowBox(canvas, 24f, top, AcStyle.W - 24f, bottom - 10f, 72f, AcStyle.RED, AcStyle.RED, 0f, AcStyle.RED_DARK, 10f)
        ac.paper(canvas, 38f, top + 14f, AcStyle.W - 38f, bottom - 24f, 58f)
        val content = RectF(24f + 14f + 44f, top + 14f + 32f, AcStyle.W - 24f - 14f - 44f, bottom - 10f - 14f - 32f)
        if (waiting) {
            drawMessage(canvas, content)
            drawGear(canvas, gearWaiting)
        } else {
            drawGear(canvas, gearInTown)
            drawContent(canvas, content)
        }
        canvas.restore()
    }

    private fun drawTabs(c: Canvas) {
        for ((t, r) in tabRects) {
            val on = t == page
            if (on) {
                ac.shadowBox(c, r.left, r.top, r.right, r.bottom, 48f, AcStyle.PAPER, AcStyle.RED, 8f, AcStyle.RED_DARK, 8f)
            } else {
                ac.shadowBox(c, r.left, r.top, r.right, r.bottom, 48f, AcStyle.GREEN_PALE, AcStyle.GREEN_DARK, 8f, AcStyle.GREEN_DARKER, 8f)
            }
            ac.font(48f, on, if (on) AcStyle.BROWN else AcStyle.GREEN_DARK, Paint.Align.CENTER)
            ac.line(c, tabLabel(t), r.centerX(), r.centerY(), r.width() - 40f)
        }
    }

    private fun drawGear(c: Canvas, r: RectF) {
        ac.shadowBox(c, r.left, r.top, r.right, r.bottom, GEAR / 2, AcStyle.GREEN_PALE, AcStyle.GREEN_DARK, 8f, AcStyle.GREEN_DARKER, 8f)
        ac.gear(c, r.centerX(), r.centerY(), 26f, AcStyle.GREEN_DARK)
    }

    private fun tabLabel(t: PanelTab) = when (t) {
        PanelTab.STATUS -> "Info"
        PanelTab.MAP -> "Map"
        PanelTab.TRACKER -> "Tracker"
    }

    /**
     * The waiting screen: the leaf badge over a speech bubble with a short title and one friendly
     * line, and three dots that pulse while it waits. Deliberately no connection details.
     */
    private fun drawMessage(c: Canvas, r: RectF) {
        val w = waitingCopy(state.phase)
        val cx = r.centerX()
        val bubbleH = if (w.waiting) 360f else 300f
        // Centre the badge + bubble group (the badge rises 138 above the bubble).
        val bubbleTop = r.centerY() - (bubbleH + 138f) / 2 + 138f
        val bubbleBottom = bubbleTop + bubbleH
        ac.shadowBox(c, cx - 430f, bubbleTop, cx + 430f, bubbleBottom, 72f, AcStyle.CREAM, AcStyle.ORANGE, 8f, 0x33A3150F, 8f)

        // Leaf badge, overlapping the bubble's top edge.
        val by = bubbleTop - 30f
        ac.ringCircle(c, cx, by, 108f, if (w.error) 0xFFFFE1DC.toInt() else BADGE_BG, if (w.error) AcStyle.ALERT else BADGE_RING, 9f)
        ac.leaf(c, cx - 66f, by - 66f, 132f)

        ac.font(68f, true, if (w.error) AcStyle.LOAN else AcStyle.BROWN, Paint.Align.CENTER)
        ac.line(c, w.title, cx, bubbleTop + 148f, 800f)
        ac.font(38f, false, AcStyle.BROWN_SOFT, Paint.Align.CENTER)
        ac.line(c, w.line, cx, bubbleTop + 218f, 780f)

        if (w.waiting) {
            val step = ((SystemClock.uptimeMillis() / DOT_MS) % 3).toInt()
            for (i in 0 until 3) {
                val on = i == step
                ac.fill.color = if (on) AcStyle.ORANGE else 0xFFF6D29A.toInt()
                c.drawCircle(cx + (i - 1) * 48f, bubbleTop + 296f, if (on) 14f else 11f, ac.fill)
            }
            postInvalidateDelayed(DOT_MS)
        }
    }

    private class Waiting(val title: String, val line: String, val waiting: Boolean, val error: Boolean)

    private fun waitingCopy(p: Phase): Waiting = when (p) {
        Phase.CONNECTING, Phase.NO_DOLPHIN ->
            Waiting("Waiting for Dolphin", "Start Animal Crossing in dolphin-lnk to connect", true, false)
        Phase.NOT_IN_TOWN, Phase.IN_TOWN ->
            Waiting("Waiting for your town", "Load your save to see what's happening in town", true, false)
        Phase.WRONG_GAME ->
            Waiting("Game not supported", "AC Duo works with Animal Crossing (USA)", false, true)
        Phase.SPEC_MISSING ->
            Waiting("Something's missing", "Reinstall AC Duo to restore its game data", false, true)
    }

    private fun describe(s: GameState): String = waitingCopy(s.phase).let { "${it.title}. ${it.line}" }

    private fun hitAt(x: Float, y: Float): Hit? = when {
        waiting -> if (gearWaiting.contains(x, y)) Hit.Settings else null
        gearInTown.contains(x, y) -> Hit.Settings
        else -> tabRects.entries.firstOrNull { it.value.contains(x, y) }?.key?.let { Hit.Tab(it) }
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        val hit = hitAt((e.x - offX) / pageScale, (e.y - offY) / pageScale)
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                down = hit
                downAt = e.eventTime
            }
            MotionEvent.ACTION_UP -> {
                val d = down
                down = null
                if (d != null && d == hit && e.eventTime - downAt < ViewConfiguration.getLongPressTimeout()) {
                    when (d) {
                        is Hit.Tab -> if (d.tab != page) onTab(d.tab)
                        Hit.Settings -> onSettings()
                    }
                }
            }
            MotionEvent.ACTION_CANCEL -> down = null
        }
        return super.onTouchEvent(e)
    }
}
