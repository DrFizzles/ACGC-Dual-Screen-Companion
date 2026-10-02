package com.acdualscreen.companion

import android.annotation.SuppressLint
import android.content.Context
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.acdualscreen.companion.core.GameState

/**
 * A panel: the Info ([InfoView]), Map ([TownMapView]) and Tracker ([TrackerView]) pages. When the
 * panel can be touched ([showTabs]) each page draws the "Info | Map | Tracker" tabs and a tap
 * switches page. The floating box on the main screen cannot be touched, so it shows one page,
 * chosen in the settings.
 *
 * Every minute the content moves a few pixels (OLED burn-in protection).
 */
@SuppressLint("ViewConstructor")
class PanelRoot(
    context: Context,
    compact: Boolean,
    private val showTabs: Boolean,
    initial: PanelTab,
    private val onTabChanged: (PanelTab) -> Unit,
) : LinearLayout(context) {

    private companion object {
        const val BG = 0xFF47A646.toInt()
        const val HINT_BG = 0xFF3A2E00.toInt()
        const val HINT_TEXT = 0xFFFFD54F.toInt()
        const val SHIFT_PERIOD_MS = 60_000L
        val SHIFTS = arrayOf(0 to 0, 1 to 0, 1 to 1, 0 to 1, -1 to 1, -1 to 0, -1 to -1, 0 to -1, 1 to -1)
    }

    private val density = resources.displayMetrics.density
    val status = InfoView(context, compact, showTabs) { select(it) }
    val map = TownMapView(context, compact, showTabs) { select(it) }
    val tracker = TrackerView(context, compact, showTabs) { select(it) }
    private val pages = mapOf(PanelTab.STATUS to status, PanelTab.MAP to map, PanelTab.TRACKER to tracker)
    private val content = FrameLayout(context)
    private val hint = TextView(context)
    private var shiftStep = 0
    private val shiftPx = 5f * density

    var tab: PanelTab = initial
        private set

    private val shifter = object : Runnable {
        override fun run() {
            shiftStep = (shiftStep + 1) % SHIFTS.size
            val (sx, sy) = SHIFTS[shiftStep]
            content.translationX = sx * shiftPx
            content.translationY = sy * shiftPx
            postDelayed(this, SHIFT_PERIOD_MS)
        }
    }

    init {
        orientation = VERTICAL
        // Opaque panels: the strip uncovered by the burn-in shift stays grass-coloured.
        if (!compact) setBackgroundColor(BG)
        hint.apply {
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(HINT_TEXT)
            setBackgroundColor(HINT_BG)
            val pad = (6 * density).toInt()
            setPadding(pad, pad, pad, pad)
            visibility = GONE
        }
        addView(hint, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        for (p in pages.values) {
            content.addView(p, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        }
        addView(content, LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f))
        show(initial)
    }

    /**
     * Whether this panel needs [t]'s data: every page when it has tabs (so switching tabs shows
     * current data at once; hidden pages keep their state), only its one page otherwise.
     */
    fun wants(t: PanelTab): Boolean = showTabs || tab == t

    /** Switches page (from a tab tap) and reports it. */
    fun select(t: PanelTab) {
        if (t == tab) return
        show(t)
        onTabChanged(t)
    }

    private fun show(t: PanelTab) {
        tab = t
        for ((k, v) in pages) v.visibility = if (k == t) VISIBLE else GONE
    }

    fun setState(s: GameState) {
        for (p in pages.values) p.setState(s)
    }

    fun setFooter(text: String) {
        for (p in pages.values) p.setFooter(text)
    }

    /** A one-line notice above the page (null hides it). */
    fun setHint(text: String?) {
        hint.text = text ?: ""
        hint.visibility = if (text.isNullOrEmpty()) GONE else VISIBLE
    }

    /** Long-press on the page content. */
    fun setOnContentLongClick(listener: () -> Unit) {
        val l = OnLongClickListener { listener(); true }
        for (p in pages.values) p.setOnLongClickListener(l)
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        postDelayed(shifter, SHIFT_PERIOD_MS)
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(shifter)
        super.onDetachedFromWindow()
    }
}
