package com.acdualscreen.companion.core

/**
 * A synthetic big-endian GameCube MEM1 image (24 MB) that answers reads the way the EmuLink
 * server does: address & 0x3FFFFFFF, out-of-range -> invalid (null).
 */
class FakeMemory : MemoryReader {
    val ram = ByteArray(0x01800000)
    val batches = ArrayList<List<ReadReq>>()

    fun phys(addr: Long): Int = (addr and 0x3FFFFFFFL).toInt()

    override fun read(reqs: List<ReadReq>): List<ByteArray?> {
        batches += reqs
        return reqs.map { slice(it.addr, it.size) }
    }

    fun slice(addr: Long, size: Int): ByteArray? {
        val p = phys(addr)
        return if (size > 0 && p.toLong() + size <= ram.size) ram.copyOfRange(p, p + size) else null
    }

    fun u8(addr: Long, v: Int) = apply { ram[phys(addr)] = v.toByte() }

    fun u16(addr: Long, v: Int) = apply {
        val p = phys(addr)
        ram[p] = (v shr 8).toByte()
        ram[p + 1] = v.toByte()
    }

    fun u32(addr: Long, v: Long) = apply {
        val p = phys(addr)
        for (i in 0 until 4) ram[p + i] = (v shr (24 - 8 * i)).toByte()
    }

    fun raw(addr: Long, b: ByteArray) = apply { b.copyInto(ram, phys(addr)) }

    /** Writes ASCII text space-padded to [len] (A-Z, a-z, 0-9 and space match the game charset). */
    fun text(addr: Long, s: String, len: Int) = apply {
        val b = ByteArray(len) { 0x20 }
        s.toByteArray(Charsets.US_ASCII).copyInto(b)
        raw(addr, b)
    }
}
