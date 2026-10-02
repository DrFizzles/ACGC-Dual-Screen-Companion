package com.acdualscreen.companion.net

import com.acdualscreen.companion.core.Decoder
import com.acdualscreen.companion.core.DecoderTest
import com.acdualscreen.companion.core.FakeMemory
import com.acdualscreen.companion.core.Phase
import com.acdualscreen.companion.core.ReadReq
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Client tests against [FakeEmuLinkServer], which mimics dolphin-lnk's EmuLinkServer.cpp. */
class EmuLinkClientTest {

    private val servers = ArrayList<FakeEmuLinkServer>()
    private val clients = ArrayList<EmuLinkClient>()

    private fun server(mem: FakeMemory = FakeMemory(), staleFirst: Boolean = false, silent: Boolean = false) =
        FakeEmuLinkServer(mem, staleFirst, silent).also { servers += it }

    private fun client(s: FakeEmuLinkServer, timeout: Int = 300) =
        EmuLinkClient("127.0.0.1", s.port, timeout, helloTimeoutMs = 1000).also { clients += it }

    @After
    fun tearDown() {
        clients.forEach { it.close() }
        servers.forEach {
            it.close()
            assertTrue("protocol violations: ${it.violations}", it.violations.isEmpty())
        }
    }

    @Test
    fun handshakeParsesJson() {
        val s = server()
        val hello = client(s).handshake()
        assertEquals(EmuLinkClient.Hello("dolphin", "GAFE01", "0123abcd", "GCN"), hello)
        assertArrayEquals("EMLKV2".toByteArray(), s.packets.single())
    }

    @Test
    fun batchHeaderIsLittleEndian() {
        val pkt = EmuLinkClient.encodeBatch(listOf(ReadReq(0x81266400L, 8), ReadReq(0x80000000L, 4096)))
        assertEquals(4 + 2 * 8, pkt.size)
        assertArrayEquals(
            byteArrayOf(0x45, 0x4C, 2, 0, 0x00, 0x64, 0x26, 0x81.toByte(), 8, 0, 0, 0,
                0, 0, 0, 0x80.toByte(), 0, 0x10, 0, 0),
            pkt,
        )
    }

    @Test
    fun encodeBatchRefusesShapesTheServerWouldTreatAsWrites() {
        val bad = listOf(
            emptyList(),
            List(257) { ReadReq(0x80000000L, 4) },
            listOf(ReadReq(0x80000000L, 0)),
            listOf(ReadReq(0x80000000L, 4097)),
            listOf(ReadReq(-1L, 4)),
        )
        for (reqs in bad) {
            try {
                EmuLinkClient.encodeBatch(reqs)
                fail("encoded a bad batch of ${reqs.size}")
            } catch (e: IllegalArgumentException) {
                // expected
            }
        }
    }

    @Test
    fun decodeBatchRejectsMismatchedReplies() {
        val reqs = listOf(ReadReq(0x80000000L, 2))
        val good = byteArrayOf(0x45, 0x4C, 1, 0, 2, 0, 7, 9)
        assertArrayEquals(byteArrayOf(7, 9), EmuLinkClient.decodeBatch(good, good.size, reqs)!![0])
        val invalid = byteArrayOf(0x45, 0x4C, 1, 0, 0, 0)
        assertNull(EmuLinkClient.decodeBatch(invalid, invalid.size, reqs)!![0])
        assertNull(EmuLinkClient.decodeBatch(good, good.size, reqs + reqs)) // wrong count
        assertNull(EmuLinkClient.decodeBatch(good, good.size - 1, reqs)) // truncated
        val wrongLen = byteArrayOf(0x45, 0x4C, 1, 0, 1, 0, 7)
        assertNull(EmuLinkClient.decodeBatch(wrongLen, wrongLen.size, reqs))
        assertNull(EmuLinkClient.decodeBatch("{}".toByteArray(), 2, reqs))
    }

    @Test
    fun readsValuesAndInvalidRanges() {
        val mem = FakeMemory().u32(0x81266400L + 0x8C, 77122).text(0x80000000L, "GAFE01", 6)
        val s = server(mem)
        val out = client(s).read(listOf(
            ReadReq(0x81266400L + 0x8C, 4),
            ReadReq(0xC0000000L, 6), // uncached mirror, same physical memory
            ReadReq(0x81800000L, 4), // past the end of MEM1
        ))
        assertArrayEquals(byteArrayOf(0, 1, 0x2D, 0x42), out[0])
        assertEquals("GAFE01", String(out[1]!!, Charsets.US_ASCII))
        assertNull(out[2])
    }

    @Test
    fun splitsLargeRequestsAndManyEntries() {
        val mem = FakeMemory()
        for (i in 0 until 10000) mem.u8(0x80100000L + i, i % 251)
        val s = server(mem)
        val c = client(s)
        val big = c.read(listOf(ReadReq(0x80100000L, 10000)))[0]!!
        assertEquals(10000, big.size)
        assertTrue((0 until 10000).all { (big[it].toInt() and 0xFF) == it % 251 })

        val many = c.read(List(300) { ReadReq(0x80100000L + it, 1) })
        assertEquals(300, many.size)
        assertTrue((0 until 300).all { (many[it]!![0].toInt() and 0xFF) == it % 251 })

        val batches = s.packets.map { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN).getShort(2).toInt() }
        assertTrue(batches.all { it in 1..256 })
    }

    @Test
    fun chunkingKeepsRepliesUnderServerBuffer() {
        val reqs = List(40) { ReadReq(0x80000000L, 4096) }
        val chunks = EmuLinkClient.chunk(reqs)
        assertTrue(chunks.all { b -> 4 + b.sumOf { it.size + 2 } <= EmuLinkClient.MAX_REPLY_BYTES })
        assertEquals(40, chunks.sumOf { it.size })
    }

    @Test
    fun largeReadsNeverOverflowTheServerReplyBuffer() {
        val mem = FakeMemory()
        val base = 0x80200000L
        for (i in 0 until 30 * 4096 step 7) mem.u8(base + i, i / 7)
        val s = server(mem)
        val out = client(s).read(List(30) { ReadReq(base + it * 4096L, 4096) })
        assertEquals(0, s.overflowed)
        for (k in 0 until 30) {
            val b = out[k]!!
            assertTrue((0 until 4096 step 7).all { j -> b[j] == mem.ram[mem.phys(base + k * 4096L + j)] })
        }
        assertTrue(s.packets.all { pkt ->
            val bb = ByteBuffer.wrap(pkt).order(ByteOrder.LITTLE_ENDIAN)
            4 + (0 until bb.getShort(2).toInt()).sumOf { 2 + bb.getInt(8 + it * 8) } <= EmuLinkClient.MAX_REPLY_BYTES
        })
    }

    @Test
    fun fakeServerOverflowsLikeTheRealOneAndShortRepliesAreRejected() {
        // Bypass chunk(): 15 x 4096 + 3522 bytes fills the reply to 64998 bytes, the next entry
        // gets len 0 (65000 bytes) and the server stops there, answering 17 of 19 entries.
        val reqs = List(15) { ReadReq(0x80000000L, 4096) } + ReadReq(0x80000000L, 3522) + List(3) { ReadReq(0x80000000L, 1) }
        val s = server()
        java.net.DatagramSocket().use { sock ->
            sock.soTimeout = 1000
            val pkt = EmuLinkClient.encodeBatch(reqs)
            sock.send(java.net.DatagramPacket(pkt, pkt.size, java.net.InetAddress.getLoopbackAddress(), s.port))
            val buf = ByteArray(65536)
            val reply = java.net.DatagramPacket(buf, buf.size)
            sock.receive(reply)
            assertEquals(65000, reply.length)
            assertEquals(17, ByteBuffer.wrap(buf).order(ByteOrder.LITTLE_ENDIAN).getShort(2).toInt())
            assertEquals(2, s.overflowed) // the len-0 entry and the one that hit the break
            assertNull(EmuLinkClient.decodeBatch(buf, reply.length, reqs))
        }
        assertTrue(EmuLinkClient.replySize(reqs) > EmuLinkClient.MAX_REPLY_BYTES)
        assertTrue(EmuLinkClient.chunk(reqs).all { EmuLinkClient.replySize(it) <= EmuLinkClient.MAX_REPLY_BYTES })
    }

    @Test
    fun closedClientFailsFast() {
        val s = server(silent = true)
        val c = client(s)
        c.close()
        val t0 = System.nanoTime()
        try {
            c.handshake()
            fail("expected IOException")
        } catch (e: IOException) {
            // expected
        }
        assertTrue((System.nanoTime() - t0) / 1_000_000 < 200)
        assertTrue(s.packets.isEmpty())
    }

    @Test
    fun skipsStaleReplies() {
        val mem = FakeMemory().u16(0x80000010L, 0x1234)
        val s = server(mem, staleFirst = true)
        val c = client(s)
        assertEquals("GAFE01", c.handshake().gameId)
        assertArrayEquals(byteArrayOf(0x12, 0x34), c.read(listOf(ReadReq(0x80000010L, 2)))[0])
    }

    @Test
    fun timesOutWhenNobodyAnswers() {
        val s = server(silent = true)
        val c = client(s, timeout = 100)
        val t0 = System.nanoTime()
        try {
            c.read(listOf(ReadReq(0x80000000L, 4)))
            fail("expected a timeout")
        } catch (e: IOException) {
            // expected
        }
        assertTrue((System.nanoTime() - t0) / 1_000_000 < 2000)
        assertEquals(2, s.packets.size) // one retry
    }

    @Test
    fun decoderPollsThroughUdp() {
        val s = server(DecoderTest.townImage())
        val c = client(s)
        val d = Decoder(DecoderTest.fixture())
        val hello = c.handshake()
        d.onHello(hello.gameId, hello.gameHash)
        val st = d.poll(c)
        assertEquals(Phase.IN_TOWN, st.phase)
        assertEquals("Cheevo", st.town)
        assertEquals("Doc", st.playerName)
        assertEquals(77122L, st.wallet)
        assertEquals("Fake Chair", st.pockets[0].name)
        assertNotNull(st.clock)
    }
}
