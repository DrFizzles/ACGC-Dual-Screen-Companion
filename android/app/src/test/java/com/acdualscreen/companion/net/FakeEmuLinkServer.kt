package com.acdualscreen.companion.net

import com.acdualscreen.companion.core.FakeMemory
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Collections

/**
 * In-process UDP server that mimics dolphin-lnk's EmuLinkServer.cpp byte for byte (little-endian
 * headers memcpy'd into host u32/u16, 'EL' batch replies, len-0 entries for invalid ranges). It
 * records every packet and flags anything the real server would treat as a single read or a WRITE.
 */
class FakeEmuLinkServer(val mem: FakeMemory, val staleFirst: Boolean = false, val silent: Boolean = false) {
    val socket = DatagramSocket(InetSocketAddress(InetAddress.getLoopbackAddress(), 0))
    val port: Int get() = socket.localPort
    val packets: MutableList<ByteArray> = Collections.synchronizedList(ArrayList())
    val violations: MutableList<String> = Collections.synchronizedList(ArrayList())
    /** Valid entries sent as len 0 for lack of room, plus 1 if the reply was cut short. */
    @Volatile var overflowed = 0
    private val thread = Thread(::loop).apply { isDaemon = true; start() }

    private fun loop() {
        val buf = ByteArray(4096 + 8) // same receive buffer size as the real server
        while (!socket.isClosed) {
            val p = DatagramPacket(buf, buf.size)
            try { socket.receive(p) } catch (e: IOException) { return }
            val data = buf.copyOf(p.length)
            packets += data
            if (silent) continue
            val reply = handle(data) ?: continue
            if (staleFirst) {
                // A bogus, mismatched batch reply first (like a late answer to an old request).
                val bogus = byteArrayOf('E'.code.toByte(), 'L'.code.toByte(), 2, 0, 0, 0, 0, 0)
                socket.send(DatagramPacket(bogus, bogus.size, p.socketAddress))
            }
            socket.send(DatagramPacket(reply, reply.size, p.socketAddress))
        }
    }

    private fun handle(d: ByteArray): ByteArray? {
        if (d.size == 6 && String(d, Charsets.US_ASCII) == "EMLKV2") {
            return """{"emulator":"dolphin","game_id":"GAFE01","game_hash":"0123abcd","platform":"GCN"}"""
                .toByteArray()
        }
        val le = ByteBuffer.wrap(d).order(ByteOrder.LITTLE_ENDIAN)
        if (d.size >= 4 && d[0] == 'E'.code.toByte() && d[1] == 'L'.code.toByte()) {
            val count = le.getShort(2).toInt() and 0xFFFF
            if (count in 1..256 && d.size >= 4 + count * 8) {
                // Same 65000-byte reply buffer as the server: a valid entry that does not fit is
                // sent as len 0, and once not even a len fits the loop stops (actual < count).
                val out = ByteBuffer.allocate(65000).order(ByteOrder.LITTLE_ENDIAN)
                out.put('E'.code.toByte()).put('L'.code.toByte()).putShort(0)
                var actual = 0
                for (i in 0 until count) {
                    val addr = le.getInt(4 + i * 8).toLong() and 0xFFFFFFFFL
                    val size = minOf(le.getInt(8 + i * 8), 4096)
                    val bytes = mem.slice(addr, size)
                    if (bytes != null && out.position() + 2 + size <= out.capacity()) {
                        out.putShort(size.toShort()); out.put(bytes)
                    } else if (out.position() + 2 <= out.capacity()) {
                        if (bytes != null) overflowed++
                        out.putShort(0)
                    } else {
                        overflowed++
                        break
                    }
                    actual++
                }
                out.putShort(2, actual.toShort())
                return out.array().copyOf(out.position())
            }
        }
        if (d.size > 8) violations += "packet of ${d.size} bytes would be treated as a WRITE"
        else if (d.size == 8) violations += "single read sent (client should only batch)"
        return null
    }

    fun close() {
        socket.close()
        thread.join(1000)
    }
}
