package com.acdualscreen.companion.net

import com.acdualscreen.companion.core.MemoryReader
import com.acdualscreen.companion.core.ReadReq
import java.io.Closeable
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Read-only client for dolphin-lnk's EmuLink UDP server (EmuLinkServer.cpp, port 55355).
 *
 * Only two packet shapes are ever sent:
 *  - the 6-byte handshake "EMLKV2", and
 *  - batch reads: 'E','L', u16 count, count x {u32 addr, u32 size}, all little-endian,
 *    with 1 <= count <= 256 and 1 <= size <= 4096.
 *
 * The server treats any other packet of more than 8 bytes as a memory WRITE, including a batch
 * header it rejects (count 0 or > 256, or a short packet). [encodeBatch] enforces the batch
 * invariants so a malformed batch can never reach the socket. No write path exists here.
 *
 * Not thread-safe: use from one thread. [close] may be called from another thread to unblock it;
 * once closed, the client stays closed and every call throws IOException at once.
 */
class EmuLinkClient(
    private val host: String,
    private val port: Int,
    private val timeoutMs: Int = 300,
    private val helloTimeoutMs: Int = 2500,
) : MemoryReader, Closeable {

    data class Hello(val emulator: String, val gameId: String, val gameHash: String, val platform: String)

    companion object {
        const val MAX_ENTRY_SIZE = 4096
        const val MAX_ENTRIES = 256
        /**
         * EmuLinkServer.cpp builds each batch reply in a 65000-byte buffer. An entry that does not
         * fit comes back as len 0, which looks exactly like an invalid address, and once not even
         * the 2-byte len fits the server stops and sends fewer entries than asked. [chunk] keeps
         * every batch's full reply under [MAX_REPLY_BYTES], so neither case can occur.
         */
        const val SERVER_REPLY_BUFFER = 65_000
        const val MAX_REPLY_BYTES = 60_000
        private val HELLO = "EMLKV2".toByteArray(Charsets.US_ASCII)

        /** Builds a batch-read packet. Throws IllegalArgumentException rather than send a bad one. */
        fun encodeBatch(reqs: List<ReadReq>): ByteArray {
            require(reqs.size in 1..MAX_ENTRIES) { "batch must have 1..$MAX_ENTRIES entries" }
            val bb = ByteBuffer.allocate(4 + 8 * reqs.size).order(ByteOrder.LITTLE_ENDIAN)
            bb.put('E'.code.toByte()).put('L'.code.toByte()).putShort(reqs.size.toShort())
            for (r in reqs) {
                require(r.size in 1..MAX_ENTRY_SIZE) { "entry size ${r.size} out of range" }
                require(r.addr in 0L..0xFFFFFFFFL) { "address out of range" }
                bb.putInt(r.addr.toInt()).putInt(r.size)
            }
            return bb.array()
        }

        /** Size of the server's reply to [reqs] when every entry is valid. */
        fun replySize(reqs: List<ReadReq>): Int = 4 + reqs.sumOf { 2 + it.size }

        /**
         * Parses a batch reply. Returns null when the packet does not match [reqs] exactly (e.g. a
         * late reply to an earlier request); entries the server marked invalid (len 0) are null.
         * A short reply (actual < count) is also rejected: it can only come from an overflowing
         * server buffer, which [chunk] rules out, or from a stale packet.
         */
        fun decodeBatch(buf: ByteArray, len: Int, reqs: List<ReadReq>): List<ByteArray?>? {
            if (len < 4 || buf[0] != 'E'.code.toByte() || buf[1] != 'L'.code.toByte()) return null
            val actual = u16le(buf, 2)
            if (actual != reqs.size) return null
            var off = 4
            val out = ArrayList<ByteArray?>(reqs.size)
            for (r in reqs) {
                if (off + 2 > len) return null
                val l = u16le(buf, off)
                off += 2
                if (l == 0) {
                    out += null
                    continue
                }
                if (l != r.size || off + l > len) return null
                out += buf.copyOfRange(off, off + l)
                off += l
            }
            return if (off == len) out else null
        }

        /** Splits requests into batches the server will answer in full. */
        fun chunk(reqs: List<ReadReq>): List<List<ReadReq>> {
            val out = ArrayList<List<ReadReq>>()
            var cur = ArrayList<ReadReq>()
            var bytes = 4 // == replySize(cur)
            for (r in reqs) {
                val cost = 2 + r.size
                if (cur.isNotEmpty() && (cur.size == MAX_ENTRIES || bytes + cost > MAX_REPLY_BYTES)) {
                    out += cur
                    cur = ArrayList()
                    bytes = 4
                }
                cur += r
                bytes += cost
            }
            if (cur.isNotEmpty()) out += cur
            return out
        }

        private val JSON_FIELD = Regex("\"(\\w+)\"\\s*:\\s*\"([^\"]*)\"")

        fun parseHello(text: String): Hello? {
            if (!text.trimStart().startsWith("{")) return null
            val m = JSON_FIELD.findAll(text).associate { it.groupValues[1] to it.groupValues[2] }
            val emulator = m["emulator"] ?: return null
            return Hello(emulator, m["game_id"] ?: "", m["game_hash"] ?: "", m["platform"] ?: "")
        }

        private fun u16le(b: ByteArray, off: Int) = (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8)
    }

    @Volatile private var socket: DatagramSocket? = null
    @Volatile private var closed = false
    private val rx = ByteArray(65536)

    private fun sock(): DatagramSocket {
        socket?.let { return it }
        if (closed) throw IOException("client closed")
        val addr = InetSocketAddress(host, port)
        if (addr.isUnresolved) throw IOException("cannot resolve $host")
        val s = DatagramSocket()
        try {
            s.connect(addr) // only accept datagrams from the server
        } catch (e: Exception) {
            s.close()
            throw IOException("cannot connect to $host:$port", e)
        }
        socket = s
        // close() on another thread may have run meanwhile and missed this socket.
        if (closed) {
            s.close()
            socket = null
            throw IOException("client closed")
        }
        return s
    }

    /**
     * Sends [out] and waits up to [timeout] ms for a datagram that [accept] recognises; anything
     * else (stale replies) is skipped. Returns the accepted result.
     */
    private fun <T> exchange(out: ByteArray, timeout: Int, accept: (Int) -> T?): T {
        val s = sock()
        drain(s)
        s.send(DatagramPacket(out, out.size))
        val deadline = System.nanoTime() + timeout * 1_000_000L
        while (true) {
            val left = ((deadline - System.nanoTime()) / 1_000_000L).toInt()
            if (left <= 0) throw SocketTimeoutException("no reply from $host:$port")
            s.soTimeout = left
            val p = DatagramPacket(rx, rx.size)
            s.receive(p)
            accept(p.length)?.let { return it }
        }
    }

    /** Discards datagrams that arrived after an earlier timeout. */
    private fun drain(s: DatagramSocket) {
        s.soTimeout = 1
        try {
            while (true) s.receive(DatagramPacket(rx, rx.size))
        } catch (_: SocketTimeoutException) {
        }
    }

    @Throws(IOException::class)
    fun handshake(): Hello =
        exchange(HELLO, helloTimeoutMs) { n -> parseHello(String(rx, 0, n, Charsets.UTF_8)) }

    @Throws(IOException::class)
    override fun read(reqs: List<ReadReq>): List<ByteArray?> {
        // Split anything larger than one server entry into <= 4096-byte pieces.
        val pieces = ArrayList<ReadReq>()
        val pieceCount = IntArray(reqs.size)
        reqs.forEachIndexed { i, r ->
            var a = r.addr
            var left = r.size
            while (left > 0) {
                val n = minOf(left, MAX_ENTRY_SIZE)
                pieces += ReadReq(a, n)
                pieceCount[i]++
                a += n
                left -= n
            }
        }

        val results = ArrayList<ByteArray?>(pieces.size)
        for (batch in chunk(pieces)) results += readBatch(batch)

        var k = 0
        return reqs.mapIndexed { i, r ->
            if (r.size <= 0) return@mapIndexed ByteArray(0)
            val parts = results.subList(k, k + pieceCount[i])
            k += pieceCount[i]
            if (parts.any { it == null }) null
            else if (parts.size == 1) parts[0]
            else ByteArray(r.size).also { dst ->
                var o = 0
                for (part in parts) { part!!.copyInto(dst, o); o += part.size }
            }
        }
    }

    private fun readBatch(batch: List<ReadReq>): List<ByteArray?> {
        check(replySize(batch) <= MAX_REPLY_BYTES) { "batch reply would overflow the server buffer" }
        val packet = encodeBatch(batch)
        return try {
            exchange(packet, timeoutMs) { n -> decodeBatch(rx, n, batch) }
        } catch (e: SocketTimeoutException) {
            // One retry absorbs a single lost datagram; a second timeout means Dolphin is gone.
            exchange(packet, timeoutMs) { n -> decodeBatch(rx, n, batch) }
        }
    }

    override fun close() {
        closed = true
        socket?.close()
        socket = null
    }
}
