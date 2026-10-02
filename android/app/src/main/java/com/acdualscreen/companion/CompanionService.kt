package com.acdualscreen.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Display
import android.view.Gravity
import android.view.WindowManager
import com.acdualscreen.companion.core.GameState

/**
 * Foreground service that owns the overlay window and a subscription to the shared poller
 * ([PollerHub]; the split-screen [PanelActivity] uses the same one, so Dolphin is polled once).
 *
 * The overlay is a TYPE_APPLICATION_OVERLAY window that is never focusable, so the gamepad and
 * keyboard stay with Dolphin. On a secondary display it fills the screen, opaque black, with
 * Status | Map tabs; a long-press on the page closes the panel. With "use bottom screen" off it is
 * a small translucent box on the main screen that lets touches through and shows the page chosen
 * in the settings. With "use bottom screen" on and no secondary display, nothing is drawn until
 * one appears. Polling pauses while no panel can be seen; the map is read only while shown.
 */
class CompanionService : Service() {

    companion object {
        private const val TAG = "ACPanel"
        private const val CHANNEL_ID = "panel"
        private const val NOTIFICATION_ID = 1
        const val ACTION_START = "com.acdualscreen.companion.START"
        const val ACTION_STOP = "com.acdualscreen.companion.STOP"
        private const val TAB_KEY = "bottom"

        @Volatile var running = false
            private set

        /** Display states in which the panel cannot be seen, so polling pauses. */
        private val DARK_STATES = setOf(
            Display.STATE_OFF, Display.STATE_DOZE, Display.STATE_DOZE_SUSPEND, Display.STATE_ON_SUSPEND,
        )

        /**
         * Where the panel goes: the default display when [useBottomScreen] is off; otherwise a
         * presentation display, then any other non-private display, else null (no bottom screen:
         * draw nothing rather than cover the game).
         */
        fun pickDisplay(dm: DisplayManager, useBottomScreen: Boolean): Display? {
            if (!useBottomScreen) return dm.getDisplay(Display.DEFAULT_DISPLAY)
            fun usable(d: Display) = d.displayId != Display.DEFAULT_DISPLAY && (d.flags and Display.FLAG_PRIVATE) == 0
            return dm.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION).firstOrNull(::usable)
                ?: dm.displays.firstOrNull(::usable)
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var dm: DisplayManager
    private var prefs = Prefs()
    private var sub: PollerHub.Subscription? = null
    private var notificationText = ""

    private var panel: PanelRoot? = null
    private var panelWm: WindowManager? = null
    private var panelDisplayId = -1

    // One WindowContext per display, reused so each Start press does not register a new token.
    private var windowContext: Context? = null
    private var windowContextDisplayId = -1

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = refreshOverlay()
        override fun onDisplayRemoved(displayId: Int) {
            if (displayId == windowContextDisplayId) dropWindowContext()
            refreshOverlay()
        }
        override fun onDisplayChanged(displayId: Int) = refreshOverlay() // also fires on screen on/off
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        dm = getSystemService(DisplayManager::class.java)
        createChannel()
        startInForeground("Starting…")
        dm.registerDisplayListener(displayListener, main)
        running = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        // (Re)start with the current settings. A first subscribe restarts a poller the panel
        // window already runs if the connection settings changed; a later Start restarts it anyway
        // (re-reading the spec too).
        prefs = Prefs.load(this)
        removeOverlay()
        if (sub == null) sub = PollerHub.subscribe(this, active = false, wantsMap = false, onPrefs = ::onPrefs, onState = ::onState)
        else PollerHub.restart(this)
        refreshOverlay()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        sub?.let(PollerHub::unsubscribe)
        sub = null
        dm.unregisterDisplayListener(displayListener)
        removeOverlay()
        dropWindowContext()
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    // ---------------------------------------------------------------- polling

    private fun onState(s: GameState) {
        if (!running) return
        panel?.setState(s)
    }

    /** The shared poller (re)started with [p]: the footer shows what is actually polled. */
    private fun onPrefs(p: Prefs) {
        if (!running) return
        panel?.setFooter(footerText(p, panelDisplayId != Display.DEFAULT_DISPLAY))
    }

    /** True while the panel exists and its display is lit. */
    private fun panelVisible(): Boolean {
        if (panel == null) return false
        val d = dm.getDisplay(panelDisplayId) ?: return false
        return d.state !in DARK_STATES
    }

    private fun updatePolling() {
        val s = sub ?: return
        PollerHub.update(
            s, active = panelVisible(),
            wantsMap = panelVisible() && panel?.tab == PanelTab.MAP,
            wantsDaily = panelVisible() && panel?.tab == PanelTab.TRACKER,
        )
    }

    private fun footerText(p: Prefs, secondary: Boolean) =
        p.connectionLabel() + if (secondary) " · long-press to close" else ""

    // ---------------------------------------------------------------- overlay

    /** Places the overlay on the preferred display (or removes it), then pauses/resumes polling. */
    private fun refreshOverlay() {
        if (!running) return
        placeOverlay()
        updatePolling()
    }

    private fun placeOverlay() {
        if (!Settings.canDrawOverlays(this)) {
            updateNotification("Overlay permission missing — open AC Panel to grant it")
            return
        }
        val target = pickDisplay(dm, prefs.useBottomScreen)
        if (target == null) {
            removeOverlay()
            updateNotification("Bottom screen not found — the panel will appear when it does")
            return
        }
        if (panel != null && target.displayId == panelDisplayId) return
        removeOverlay()
        try {
            addOverlay(target)
        } catch (e: Exception) {
            Log.e(TAG, "cannot add overlay on display ${target.displayId}", e)
            removeOverlay()
            dropWindowContext()
            updateNotification("Cannot show panel: ${e.message}")
        }
    }

    private fun windowContextFor(display: Display): Context {
        windowContext?.let { if (windowContextDisplayId == display.displayId) return it }
        return createDisplayContext(display)
            .createWindowContext(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, null)
            .also {
                windowContext = it
                windowContextDisplayId = display.displayId
            }
    }

    private fun dropWindowContext() {
        windowContext = null
        windowContextDisplayId = -1
    }

    private fun addOverlay(display: Display) {
        val secondary = display.displayId != Display.DEFAULT_DISPLAY
        val ctx = windowContextFor(display)
        val wm = ctx.getSystemService(WindowManager::class.java)
        val view = if (secondary) {
            PanelRoot(ctx, compact = false, showTabs = true, initial = Prefs.loadTab(this, TAB_KEY)) { tab ->
                Prefs.saveTab(this, TAB_KEY, tab)
                updatePolling()
            }
        } else {
            PanelRoot(ctx, compact = true, showTabs = false, initial = prefs.floatingShows) {}
        }
        view.setFooter(footerText(PollerHub.currentPrefs ?: prefs, secondary))
        PollerHub.lastState?.let(view::setState)

        val lp: WindowManager.LayoutParams
        if (secondary) {
            lp = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                // No FLAG_KEEP_SCREEN_ON: Dolphin keeps the screen on while it emulates.
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.OPAQUE,
            )
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            lp.fitInsetsTypes = 0
            // The bottom-screen panel takes every touch anyway; a long-press on the page is the
            // way out when the notification's Stop action is not available.
            view.setOnContentLongClick {
                Log.i(TAG, "panel closed by long-press")
                stopSelf()
            }
        } else {
            val bounds = wm.currentWindowMetrics.bounds
            val w = (minOf(bounds.width(), bounds.height()) * 0.6f).toInt()
            lp = WindowManager.LayoutParams(
                w,
                (w * 1.15f).toInt(), // roughly the Thor bottom screen's aspect ratio
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                // Touches pass through to the game. Android 12+ only lets touches through another
                // app's overlay when that window's alpha is at most 0.8.
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT,
            )
            lp.alpha = 0.8f
            lp.gravity = Gravity.TOP or Gravity.END
            lp.x = 16
            lp.y = 96
        }
        lp.title = "AC Panel"
        wm.addView(view, lp)
        panel = view
        panelWm = wm
        panelDisplayId = display.displayId
        val where = if (secondary) "display ${display.displayId} (${display.name})" else "main screen (test box)"
        Log.i(TAG, "panel on $where")
        updateNotification("Panel on $where")
    }

    private fun removeOverlay() {
        val v = panel ?: return
        try {
            panelWm?.removeViewImmediate(v)
        } catch (e: Exception) {
            Log.w(TAG, "removeView failed", e) // display already gone
        }
        panel = null
        panelWm = null
        panelDisplayId = -1
    }

    // ---------------------------------------------------------------- notification

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Panel", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while the AC Panel overlay is running"
            }
        )
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, CompanionService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_panel)
            .setContentTitle("AC Panel")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build())
            .build()
    }

    private fun startInForeground(text: String) {
        notificationText = text
        val n = buildNotification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, n)
        }
    }

    private fun updateNotification(text: String) {
        if (text == notificationText) return // display events can repeat the same status often
        notificationText = text
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(text))
    }
}
