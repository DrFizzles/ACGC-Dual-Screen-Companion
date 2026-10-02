package com.acdualscreen.companion.core

import java.io.IOException

/** Result of [readStable]: one entry per request (null = invalid or never agreed). */
class StableRead(val values: List<ByteArray?>, val unstable: Int)

private fun same(a: ByteArray?, b: ByteArray?): Boolean =
    if (a == null || b == null) a == null && b == null else a.contentEquals(b)

/**
 * Validity rule 8: reads are not synchronised with the emulated CPU, so every entry is read twice
 * in one batch and kept only when both copies agree. Entries that disagree are read twice more;
 * any that still disagree become null and are counted in [StableRead.unstable].
 */
@Throws(IOException::class)
fun readStable(reader: MemoryReader, reqs: List<ReadReq>): StableRead {
    if (reqs.isEmpty()) return StableRead(emptyList(), 0)
    val n = reqs.size
    val r = reader.read(reqs + reqs)
    val out = MutableList(n) { r[it] }
    val retry = (0 until n).filter { !same(r[it], r[n + it]) }
    if (retry.isEmpty()) return StableRead(out, 0)
    val again = retry.map { reqs[it] }
    val rr = reader.read(again + again)
    var unstable = 0
    retry.forEachIndexed { k, i ->
        if (same(rr[k], rr[retry.size + k])) out[i] = rr[k] else { out[i] = null; unstable++ }
    }
    return StableRead(out, unstable)
}
