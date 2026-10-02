package com.acdualscreen.companion

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.WindowInsets
import android.view.WindowManager

/**
 * AC Duo: the panel as an ordinary, resizeable app window. On the AYN Thor it is opened on the
 * bottom screen; on a phone it goes in split screen next to Dolphin. Its window is
 * FLAG_NOT_FOCUSABLE: taps still reach the panel (tabs work) but a tap never moves input focus,
 * and with it the gamepad, away from Dolphin. It polls only while visible (onStart..onStop),
 * through the process-wide poller ([PollerHub]). The gear opens [SettingsActivity].
 *
 * One exception: while this activity is the top-resumed one (right after it was launched, before
 * the user taps the game) the system treats it as the focused app. A focused app without a
 * focusable window makes the input dispatcher hold key events and, after 5 s, report an ANR, so
 * the window is focusable exactly while the activity is top-resumed. Tapping the game makes Dolphin
 * top-resumed and the flag comes back; from then on taps on the panel leave focus with Dolphin.
 * While the window does hold focus, it shows "tap the game" and swallows every controller key
 * ([ControllerKeys]): unhandled, gamepad B/Y would fall back to BACK and close the panel.
 *
 * It requests nothing that could affect Dolphin: no audio, no wake locks, no orientation lock.
 */
class PanelActivity : Activity() {

    private companion object {
        const val TAB_KEY = "window"
        const val FOCUS_HINT = "The panel has the controller: tap the game to give it back"
    }

    private lateinit var root: PanelRoot
    private var sub: PollerHub.Subscription? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Taps must not take input focus: the controller and keyboard stay with the game
        // (see the class comment for the one exception, onTopResumedActivityChanged).
        window.addFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        root = PanelRoot(
            this,
            initial = Prefs.loadTab(this, TAB_KEY),
            onTabChanged = { tab -> Prefs.saveTab(this, TAB_KEY, tab) },
            onSettings = { startActivity(Intent(this, SettingsActivity::class.java)) },
        )
        root.setOnApplyWindowInsetsListener { v, insets ->
            val b = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            v.setPadding(b.left, b.top, b.right, b.bottom)
            insets
        }
        setContentView(root)
    }

    override fun onStart() {
        super.onStart()
        // Every page is kept current (map and tracker included), so switching tabs is instant.
        sub = PollerHub.subscribe(this, active = true, wantsMap = true, wantsDaily = true, onState = { root.setState(it) })
    }

    override fun onStop() {
        sub?.let(PollerHub::unsubscribe)
        sub = null
        super.onStop()
    }

    override fun onTopResumedActivityChanged(isTopResumedActivity: Boolean) {
        super.onTopResumedActivityChanged(isTopResumedActivity)
        // See the class comment: focusable only while the system already treats us as focused.
        if (isTopResumedActivity) {
            window.clearFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        } else {
            window.addFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        root.setHint(if (hasFocus) FOCUS_HINT else null)
    }

    /**
     * Keys reach this window only while it holds focus. Controller keys are consumed so none of
     * them falls back to BACK (closing the panel) or DPAD_CENTER (pressing a tab).
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (ControllerKeys.isControllerKey(event.keyCode, event.source)) return true
        return super.dispatchKeyEvent(event)
    }

    /** Stick and hat motion too: unhandled, Android turns a hat into D-pad keys. */
    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {
        if (ControllerKeys.isFromController(ev.source)) return true
        return super.dispatchGenericMotionEvent(ev)
    }
}
