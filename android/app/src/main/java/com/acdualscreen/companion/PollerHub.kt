package com.acdualscreen.companion

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.acdualscreen.companion.core.GameState

/**
 * The one [Poller] of the process, shared by every panel (the overlay service and the split-screen
 * [PanelActivity]), so two panels never poll Dolphin twice. Each panel holds a [Subscription];
 * the poller runs while any subscription exists, pauses while none is [Subscription.active]
 * (nothing visible), and reads the town map while any active one [Subscription.wantsMap] (the
 * daily tracker likewise with [Subscription.wantsDaily]).
 *
 * The poller always uses the saved connection settings: a panel that subscribes, or a call to
 * [ensurePrefs], restarts it when the host, port or interval changed since it started. Each
 * subscription hears the settings in use ([Subscription.onPrefs], for its footer) when it
 * subscribes and after every restart. States and settings arrive on the main thread. All methods
 * must be called on the main thread.
 */
object PollerHub {
    private const val TAG = "ACPanel"

    class Subscription internal constructor(
        internal val onPrefs: (Prefs) -> Unit,
        internal val onState: (GameState) -> Unit,
    ) {
        internal var active = false
        internal var wantsMap = false
        internal var wantsDaily = false
    }

    private val main = Handler(Looper.getMainLooper())
    private val subs = ArrayList<Subscription>()
    private var poller: Poller? = null
    private var generation = 0
    private var prefs: Prefs? = null

    /** Last state delivered (so a new panel can draw at once). */
    var lastState: GameState? = null
        private set

    /** Connection settings of the running poller (null while none runs). */
    val currentPrefs: Prefs? get() = prefs

    fun subscribe(
        ctx: Context,
        active: Boolean,
        wantsMap: Boolean,
        wantsDaily: Boolean = false,
        onPrefs: (Prefs) -> Unit = {},
        onState: (GameState) -> Unit,
    ): Subscription {
        checkMain()
        val s = Subscription(onPrefs, onState).also {
            it.active = active
            it.wantsMap = wantsMap
            it.wantsDaily = wantsDaily
        }
        subs += s
        if (!restartIfChanged(ctx)) {
            if (poller == null) start(ctx.applicationContext) else apply()
            prefs?.let(onPrefs)
        }
        lastState?.let(onState)
        return s
    }

    fun update(
        sub: Subscription,
        active: Boolean = sub.active,
        wantsMap: Boolean = sub.wantsMap,
        wantsDaily: Boolean = sub.wantsDaily,
    ) {
        checkMain()
        sub.active = active
        sub.wantsMap = wantsMap
        sub.wantsDaily = wantsDaily
        apply()
    }

    fun unsubscribe(sub: Subscription) {
        checkMain()
        if (!subs.remove(sub)) return
        if (subs.isEmpty()) {
            stopPoller()
            prefs = null
        } else {
            apply()
        }
    }

    /** Restarts a running poller whose host, port or interval differ from the saved settings. */
    fun ensurePrefs(ctx: Context) {
        checkMain()
        restartIfChanged(ctx)
    }

    /** Re-reads settings and spec and restarts the poller (Start pressed again). */
    fun restart(ctx: Context) {
        checkMain()
        if (subs.isEmpty()) {
            prefs = null
            return
        }
        stopPoller()
        start(ctx.applicationContext)
        notifyPrefs()
    }

    /** True when it restarted the poller (and told every subscription). */
    private fun restartIfChanged(ctx: Context): Boolean {
        val running = prefs ?: return false
        if (poller == null || Prefs.load(ctx).sameConnection(running)) return false
        Log.i(TAG, "connection settings changed: restarting the poller")
        restart(ctx)
        return true
    }

    private fun start(app: Context) {
        val p = Prefs.load(app).also { prefs = it }
        val spec = SpecLoader.load(app)
        Log.i(TAG, "spec from ${spec.source}: ${spec.map?.summary() ?: spec.error}")
        spec.map?.warnings?.forEach { Log.w(TAG, "spec warning: $it") }
        // A replaced poller may still deliver one last state; the generation check drops it.
        val gen = ++generation
        poller = Poller(spec.map, spec.error, p.host, p.port, p.intervalMs, VillagerNameStore(app), VillagerNameStore.bundled(app)) { s ->
            main.post { if (gen == generation) deliver(s) }
        }.also {
            applyTo(it)
            it.start()
        }
    }

    private fun stopPoller() {
        poller?.stop()
        poller = null
        generation++
        lastState = null
    }

    private fun notifyPrefs() {
        val p = prefs ?: return
        for (sub in subs.toList()) sub.onPrefs(p)
    }

    private fun deliver(s: GameState) {
        lastState = s
        for (sub in subs.toList()) sub.onState(s)
    }

    private fun apply() {
        poller?.let(::applyTo)
    }

    private fun applyTo(p: Poller) {
        val active = subs.filter { it.active }
        p.setMapWanted(active.any { it.wantsMap })
        p.setDailyWanted(active.any { it.wantsDaily })
        p.setPaused(active.isEmpty())
    }

    private fun checkMain() {
        check(Looper.myLooper() == Looper.getMainLooper()) { "PollerHub is main-thread only" }
    }
}
