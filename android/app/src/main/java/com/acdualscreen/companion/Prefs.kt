package com.acdualscreen.companion

import android.content.Context
import com.acdualscreen.companion.core.MemoryMap
import java.io.File

/** What a panel shows: the info page, the town map or the daily tracker. */
enum class PanelTab {
    STATUS, MAP, TRACKER;

    companion object {
        fun parse(s: String?): PanelTab = entries.firstOrNull { it.name == s } ?: STATUS
    }
}

/** Settings persisted in SharedPreferences. */
data class Prefs(
    val host: String = DEFAULT_HOST,
    val port: Int = DEFAULT_PORT,
    val intervalMs: Long = DEFAULT_INTERVAL,
    val useBottomScreen: Boolean = true,
    /** Content of the non-touchable floating box on the main screen (no tabs there). */
    val floatingShows: PanelTab = PanelTab.STATUS,
) {
    /** Same poller settings (host, port, interval); the display options do not affect polling. */
    fun sameConnection(o: Prefs): Boolean = host == o.host && port == o.port && intervalMs == o.intervalMs

    /** "host:port · interval ms", the start of every panel footer. */
    fun connectionLabel(): String = "$host:$port · $intervalMs ms"

    fun save(ctx: Context) {
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit()
            .putString("host", host)
            .putInt("port", port)
            .putLong("interval_ms", intervalMs)
            .putBoolean("use_bottom_screen", useBottomScreen)
            .putString("floating_shows", floatingShows.name)
            .apply()
    }

    companion object {
        private const val NAME = "panel"
        const val DEFAULT_HOST = "127.0.0.1"
        const val DEFAULT_PORT = 55355
        const val DEFAULT_INTERVAL = 250L
        const val MIN_INTERVAL = 50L

        fun load(ctx: Context): Prefs {
            val sp = ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)
            return Prefs(
                host = sp.getString("host", DEFAULT_HOST)!!.ifBlank { DEFAULT_HOST },
                port = sp.getInt("port", DEFAULT_PORT),
                intervalMs = sp.getLong("interval_ms", DEFAULT_INTERVAL).coerceAtLeast(MIN_INTERVAL),
                useBottomScreen = sp.getBoolean("use_bottom_screen", true),
                floatingShows = PanelTab.parse(sp.getString("floating_shows", null)),
            )
        }

        /** Last tab chosen on a touchable panel ([key] tells the bottom screen and the window apart). */
        fun loadTab(ctx: Context, key: String): PanelTab =
            PanelTab.parse(ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).getString("tab_$key", null))

        fun saveTab(ctx: Context, key: String, tab: PanelTab) {
            ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit().putString("tab_$key", tab.name).apply()
        }
    }
}

/**
 * Loads the memory map: an override file in the app's external files dir (handy for trying new
 * offsets without rebuilding: adb push ac_memory_map.json
 * /sdcard/Android/data/com.acdualscreen.companion/files/), else the copy packaged in assets.
 */
object SpecLoader {
    const val FILE_NAME = "ac_memory_map.json"

    data class Result(val map: MemoryMap?, val source: String, val error: String?)

    fun load(ctx: Context): Result {
        val override = ctx.getExternalFilesDir(null)?.let { File(it, FILE_NAME) }
        if (override != null && override.isFile) {
            return parse(runCatching { override.readText() }, "override ${override.path}")
        }
        val text = runCatching { ctx.assets.open(FILE_NAME).use { it.readBytes().toString(Charsets.UTF_8) } }
        if (text.isFailure) return Result(null, "assets", "spec missing ($FILE_NAME not in APK)")
        return parse(text, "assets/$FILE_NAME")
    }

    private fun parse(text: kotlin.Result<String>, source: String): Result {
        val t = text.getOrElse { return Result(null, source, "cannot read spec: ${it.message}") }
        return try {
            Result(MemoryMap.parse(t), source, null)
        } catch (e: Exception) {
            Result(null, source, "bad spec: ${e.message}")
        }
    }
}
