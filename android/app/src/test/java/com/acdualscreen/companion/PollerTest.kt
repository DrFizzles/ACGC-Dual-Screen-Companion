package com.acdualscreen.companion

import com.acdualscreen.companion.core.DecoderTest
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.MapImage
import com.acdualscreen.companion.core.Phase
import com.acdualscreen.companion.net.FakeEmuLinkServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** End-to-end: Poller -> EmuLinkClient -> UDP -> fake EmuLink server -> synthetic memory. */
class PollerTest {

    private fun LinkedBlockingQueue<GameState>.awaitPhase(phase: Phase, ms: Long = 5000): GameState {
        val deadline = System.currentTimeMillis() + ms
        while (true) {
            val left = deadline - System.currentTimeMillis()
            val s = poll(maxOf(1, left), TimeUnit.MILLISECONDS) ?: throw AssertionError("no $phase within $ms ms")
            if (s.phase == phase) return s
        }
    }

    @Test
    fun publishesTownStateThenWaitsWhenDolphinStops() {
        val mem = DecoderTest.townImage()
        val server = FakeEmuLinkServer(mem)
        val states = LinkedBlockingQueue<GameState>()
        val poller = Poller(DecoderTest.fixture(), null, "127.0.0.1", server.port, 50) { states += it }
        try {
            poller.start()
            val town = states.awaitPhase(Phase.IN_TOWN)
            assertEquals("Doc", town.playerName)
            assertEquals("Fake Chair", town.pockets[0].name)

            mem.u32(DecoderTest.GAME_PT, 0) // back to the title screen
            states.awaitPhase(Phase.NOT_IN_TOWN)

            server.close() // Dolphin quits
            states.awaitPhase(Phase.NO_DOLPHIN)
        } finally {
            poller.stop()
            server.close()
        }
        assertTrue("protocol violations: ${server.violations}", server.violations.isEmpty())
    }

    @Test
    fun pausedPollerSendsNothingAndResumes() {
        val server = FakeEmuLinkServer(DecoderTest.townImage())
        val states = LinkedBlockingQueue<GameState>()
        val poller = Poller(DecoderTest.fixture(), null, "127.0.0.1", server.port, 50) { states += it }
        try {
            poller.setPaused(true)
            poller.start()
            Thread.sleep(300)
            assertTrue("sent while paused: ${server.packets.size}", server.packets.isEmpty())
            assertTrue(states.none { it.phase == Phase.IN_TOWN })

            poller.setPaused(false)
            states.awaitPhase(Phase.IN_TOWN)

            poller.setPaused(true)
            Thread.sleep(200) // let an in-flight poll finish
            val sent = server.packets.size
            Thread.sleep(300)
            assertEquals(sent, server.packets.size)
        } finally {
            poller.stop()
            server.close()
        }
        assertTrue(poller.join(1000))
        assertTrue("protocol violations: ${server.violations}", server.violations.isEmpty())
    }

    @Test
    fun stopUnblocksAPendingHandshakeAndPublishesNothingMore() {
        val server = FakeEmuLinkServer(DecoderTest.townImage(), silent = true)
        val states = LinkedBlockingQueue<GameState>()
        val poller = Poller(DecoderTest.fixture(), null, "127.0.0.1", server.port, 50) { states += it }
        try {
            poller.start()
            val deadline = System.currentTimeMillis() + 2000
            while (server.packets.isEmpty() && System.currentTimeMillis() < deadline) Thread.sleep(10)
            assertEquals(1, server.packets.size) // the handshake, now waiting up to 2.5 s
            val t0 = System.nanoTime()
            poller.stop()
            assertTrue("poller thread still running", poller.join(500))
            assertTrue((System.nanoTime() - t0) / 1_000_000 < 500)
            val published = states.size
            Thread.sleep(200)
            assertEquals(published, states.size)
            assertEquals(1, server.packets.size)
        } finally {
            poller.stop()
            server.close()
        }
    }

    @Test
    fun readsTheTownMapOnlyWhileWanted() {
        val mem = MapImage.build()
        val server = FakeEmuLinkServer(mem)
        val states = LinkedBlockingQueue<GameState>()
        val poller = Poller(MapImage.map, null, "127.0.0.1", server.port, 50) { states += it }
        try {
            poller.start()
            val plain = states.awaitPhase(Phase.IN_TOWN)
            assertEquals(null, plain.map)

            poller.setMapWanted(true)
            var s = states.awaitPhase(Phase.IN_TOWN)
            while (s.map?.player == null) s = states.awaitPhase(Phase.IN_TOWN)
            val m = s.map
            assertEquals("", m.note)
            assertEquals(30, m.layout!!.acres.count { it.pixels != null })
            assertEquals(3 to 2, m.player.blockX to m.player.blockZ)
            assertEquals(3, m.houses.size)

            // The player walks: only the marker changes, the layout object is reused.
            mem.u32(MapImage.ACTOR + MapImage.spec.player.posX, (4 * 640f + 100f).toRawBits().toLong() and 0xFFFFFFFFL)
            var moved = states.awaitPhase(Phase.IN_TOWN)
            while (moved.map?.player?.blockX != 4) moved = states.awaitPhase(Phase.IN_TOWN)
            assertTrue(moved.map.layout === m.layout)
            assertEquals((4 * 640f + 100f) / 640f * 22 - 22, moved.map.player.mapX, 1e-3f)

            poller.setMapWanted(false)
            var off = states.awaitPhase(Phase.IN_TOWN)
            while (off.map != null) off = states.awaitPhase(Phase.IN_TOWN)
        } finally {
            poller.stop()
            server.close()
        }
        assertTrue("protocol violations: ${server.violations}", server.violations.isEmpty())
        assertEquals(0, server.overflowed)
    }

    @Test
    fun reportsMissingSpec() {
        val states = LinkedBlockingQueue<GameState>()
        val poller = Poller(null, "spec missing (test)", "127.0.0.1", 1, 50) { states += it }
        poller.start()
        assertEquals("spec missing (test)", states.awaitPhase(Phase.SPEC_MISSING).status)
        poller.stop()
    }
}
