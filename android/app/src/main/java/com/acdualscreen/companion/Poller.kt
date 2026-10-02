package com.acdualscreen.companion

import com.acdualscreen.companion.core.DailyReader
import com.acdualscreen.companion.core.DailyState
import com.acdualscreen.companion.core.Decoder
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.MemoryMap
import com.acdualscreen.companion.core.MemoryReader
import com.acdualscreen.companion.core.Phase
import com.acdualscreen.companion.core.TownMapReader
import com.acdualscreen.companion.core.TownMapState
import com.acdualscreen.companion.net.EmuLinkClient
import java.io.IOException
import java.util.concurrent.locks.LockSupport

/**
 * Background polling loop: handshake, decode, publish. Runs on its own thread; [onState] is
 * called from that thread, only when the published state actually changes. Nothing new is
 * published once [stop] has been called (a call already in progress may still finish).
 * While [setPaused] is true the loop sends nothing but keeps its decoder and name caches.
 * While [setMapWanted] is true, in-town states also carry the town map ([GameState.map]): its
 * textures are read once and cached, the player marker is re-read on every poll.
 * While [setDailyWanted] is true they also carry the daily tracker ([GameState.daily]).
 */
class Poller(
    private val map: MemoryMap?,
    private val specError: String?,
    private val host: String,
    private val port: Int,
    private val intervalMs: Long,
    /** Keeps learned villager names between sessions (per game image); null = forget them. */
    private val names: NameStore? = null,
    /** Bundled villager names (npc id -> name), used for any villager not yet seen live. */
    private val bundledNames: Map<Int, String> = emptyMap(),
    private val onState: (GameState) -> Unit,
) {
    /** Saved villager names, keyed by the game image ("gameId/gameHash"). */
    interface NameStore {
        fun load(gameKey: String): Map<Int, String>
        fun save(gameKey: String, names: Map<Int, String>)
    }

    @Volatile private var running = false
    @Volatile private var paused = false
    @Volatile private var mapWanted = false
    @Volatile private var dailyWanted = false
    @Volatile private var client: EmuLinkClient? = null
    @Volatile private var thread: Thread? = null

    fun start() {
        if (running) return
        running = true
        thread = Thread(::loop, "emulink-poller").apply {
            isDaemon = true
            start()
        }
    }

    fun stop() {
        running = false
        client?.close() // unblocks a pending receive; loop() closes any client it creates later
        thread?.interrupt()
    }

    /** Stops talking to Dolphin while the panel cannot be seen (screen off, no bottom screen). */
    fun setPaused(p: Boolean) {
        paused = p
        if (!p) thread?.let(LockSupport::unpark)
    }

    /** Reads the town map too (some visible panel shows it). */
    fun setMapWanted(w: Boolean) {
        mapWanted = w
    }

    /** Reads the daily tracker too (some visible panel shows it). */
    fun setDailyWanted(w: Boolean) {
        dailyWanted = w
    }

    /** Waits for the polling thread to finish after [stop]. True if it has. */
    fun join(timeoutMs: Long): Boolean {
        val t = thread ?: return true
        t.join(timeoutMs)
        return !t.isAlive
    }

    private fun loop() {
        if (map == null) {
            onState(GameState.of(Phase.SPEC_MISSING, specError ?: "spec missing"))
            return
        }
        val decoder = Decoder(map, bundledNames)
        val townMap = map.townMap?.let { TownMapReader(map, it) }
        val daily = DailyReader(map)
        var published: GameState? = null
        fun publish(s: GameState) {
            if (running && s != published) {
                published = s
                onState(s)
            }
        }
        publish(GameState.of(Phase.CONNECTING, "Connecting to $host:$port…"))

        var helloAt = 0L
        var helloOk = false
        var failures = 0
        var prev: GameState? = null
        var shown: GameState? = null // last state accepted by the stability rule below
        var unstable = 0
        var namesKey: String? = null
        var savedNames = 0

        while (running) {
            if (paused) {
                client?.close()
                client = null
                helloOk = false
                prev = null
                shown = null
                while (paused && running) LockSupport.park(this) // unparked by setPaused / stop
                continue
            }
            try {
                val c = client ?: newClient() ?: break
                val now = System.nanoTime()
                // Re-handshake every 10 s so a different game image (new boot.dol hash) is noticed.
                if (!helloOk || now - helloAt > 10_000_000_000L) {
                    val hello = c.handshake()
                    decoder.onHello(hello.gameId, hello.gameHash)
                    decoder.gameKey?.let { k -> if (k != namesKey) { namesKey = k; names?.load(k)?.let(decoder::preloadNames); savedNames = decoder.learnedNames().size } }
                    townMap?.onHello(hello.gameId, hello.gameHash)
                    daily.reset()
                    helloOk = true
                    helloAt = now
                }
                val s = decoder.poll(c)
                failures = 0
                val learned = decoder.learnedNames()
                if (learned.size != savedNames) {
                    savedNames = learned.size
                    namesKey?.let { k -> names?.save(k, learned) }
                }
                // Reads race the emulated CPU and can tear, so in-town data is shown once two
                // consecutive polls agree (or after 3 polls if something keeps changing).
                if (s.phase != Phase.IN_TOWN || s == prev || ++unstable >= 3) {
                    shown = s
                    unstable = 0
                }
                prev = s
                // The map's player marker moves every poll; it has its own rule-8 double reads.
                shown?.let { publish(withDaily(withMap(it, c, townMap), c, daily)) }
                Thread.sleep(intervalMs)
            } catch (e: InterruptedException) {
                break
            } catch (e: IOException) {
                if (!running) break
                failures++
                helloOk = false
                prev = null
                shown = null
                client?.close()
                client = null
                if (failures >= 2) {
                    publish(GameState.of(Phase.NO_DOLPHIN, "Waiting for Dolphin… ($host:$port)"))
                }
                if (!sleepQuietly(minOf(3000L, 250L shl minOf(failures, 4)))) break
            } catch (e: Exception) {
                // A decoder problem (e.g. an odd spec value) should not kill the panel.
                shown = null
                publish(GameState.of(Phase.NO_DOLPHIN, "Error: ${e.javaClass.simpleName}: ${e.message}"))
                if (!sleepQuietly(1000L)) break
            }
        }
        client?.close()
        client = null
    }

    /** [base] plus the town map when it is wanted and the player is in town. */
    private fun withMap(base: GameState, reader: MemoryReader, townMap: TownMapReader?): GameState {
        if (!mapWanted || base.phase != Phase.IN_TOWN) return base
        if (townMap == null) return base.copy(map = TownMapState(null, note = "this spec has no town map"))
        return try {
            base.copy(map = townMap.poll(reader))
        } catch (e: IOException) {
            throw e
        } catch (e: Exception) {
            // A map problem must not take the status page down with it.
            base.copy(map = TownMapState(null, note = "map error: ${e.javaClass.simpleName}: ${e.message}"))
        }
    }

    /** [base] plus the daily tracker when it is wanted and the player is in town. */
    private fun withDaily(base: GameState, reader: MemoryReader, daily: DailyReader): GameState {
        if (!dailyWanted || base.phase != Phase.IN_TOWN) return base
        return try {
            base.copy(daily = daily.poll(reader, base))
        } catch (e: IOException) {
            throw e
        } catch (e: Exception) {
            base.copy(daily = DailyState(note = "tracker error: ${e.javaClass.simpleName}: ${e.message}"))
        }
    }

    /** A fresh client, or null if [stop] ran meanwhile (then nothing is left open). */
    private fun newClient(): EmuLinkClient? {
        val c = EmuLinkClient(host, port)
        client = c
        // stop() sets running before reading client, so one of the two sides closes c.
        if (!running) {
            c.close()
            client = null
            return null
        }
        return c
    }

    private fun sleepQuietly(ms: Long): Boolean = try {
        Thread.sleep(ms)
        true
    } catch (e: InterruptedException) {
        false
    }
}
