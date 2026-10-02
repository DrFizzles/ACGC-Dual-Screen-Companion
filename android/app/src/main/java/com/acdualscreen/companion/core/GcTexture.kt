package com.acdualscreen.companion.core

/** GameCube GX texture formats: block size in pixels and bits per pixel. */
enum class GcFormat(val blockW: Int, val blockH: Int, val bitsPerPixel: Int) {
    I4(8, 8, 4),
    I8(8, 4, 8),
    IA4(8, 4, 8),
    IA8(4, 4, 16),
    RGB565(4, 4, 16),
    RGB5A3(4, 4, 16),
    RGBA8(4, 4, 32),
    C4(8, 8, 4),
    C8(8, 4, 8),
    C14X2(4, 4, 16);

    val paletted: Boolean get() = this == C4 || this == C8 || this == C14X2

    /** Bytes of a [width] x [height] texture; the image is padded to whole blocks. */
    fun byteSize(width: Int, height: Int): Int {
        val blocks = ((width + blockW - 1) / blockW) * ((height + blockH - 1) / blockH)
        return blocks * blockW * blockH * bitsPerPixel / 8
    }

    companion object {
        fun parse(s: String): GcFormat? = when (s.trim().uppercase()) {
            "CI4" -> C4
            "CI8" -> C8
            "CI14X2" -> C14X2
            else -> entries.firstOrNull { it.name == s.trim().uppercase() }
        }
    }
}

/** Palette (TLUT) entry formats. */
enum class TlutFormat {
    IA8, RGB565, RGB5A3;

    companion object {
        fun parse(s: String): TlutFormat? = entries.firstOrNull { it.name == s.trim().uppercase() }
    }
}

/**
 * GameCube texture decoder: tiled GX texture bytes (big-endian, as in emulated RAM) to ARGB
 * pixels (0xAARRGGBB, row-major), the layout android.graphics.Bitmap takes. Pure Kotlin, so the
 * JVM tests cover it; tools/pc_client/gctex.py is the Python twin, and both decode the shared
 * vectors in src/test/resources/crosscheck/texture_vectors.json identically
 * (tools/pc_client/texture_vectors_export.py writes them).
 *
 * Used only on bytes read from RAM at runtime: the app ships no images.
 */
object GcTexture {

    private fun c3(v: Int) = (v shl 5) or (v shl 2) or (v shr 1)
    private fun c4(v: Int) = v * 17
    private fun c5(v: Int) = (v shl 3) or (v shr 2)
    private fun c6(v: Int) = (v shl 2) or (v shr 4)

    fun argb(a: Int, r: Int, g: Int, b: Int): Int = (a shl 24) or (r shl 16) or (g shl 8) or b

    /** BE16 RGB5A3: bit 15 set = opaque RGB555, clear = A3RGB444. */
    fun rgb5a3(v: Int): Int =
        if (v and 0x8000 != 0) argb(255, c5((v shr 10) and 31), c5((v shr 5) and 31), c5(v and 31))
        else argb(c3((v shr 12) and 7), c4((v shr 8) and 15), c4((v shr 4) and 15), c4(v and 15))

    fun rgb565(v: Int): Int = argb(255, c5((v shr 11) and 31), c6((v shr 5) and 63), c5(v and 31))

    /** GX IA8 texel or TLUT entry: first byte alpha, second byte intensity. */
    fun ia8(hi: Int, lo: Int): Int = argb(hi, lo, lo, lo)

    private fun u8(b: ByteArray, i: Int) = b[i].toInt() and 0xFF

    /** [count] BE16 TLUT entries at [offset] to ARGB. */
    fun decodePalette(data: ByteArray, count: Int, format: TlutFormat, offset: Int = 0): IntArray {
        require(offset >= 0 && count >= 0 && offset + 2 * count <= data.size) { "palette data too short" }
        return IntArray(count) { i ->
            val hi = u8(data, offset + 2 * i)
            val lo = u8(data, offset + 2 * i + 1)
            when (format) {
                TlutFormat.RGB5A3 -> rgb5a3((hi shl 8) or lo)
                TlutFormat.RGB565 -> rgb565((hi shl 8) or lo)
                TlutFormat.IA8 -> ia8(hi, lo)
            }
        }
    }

    /**
     * Decodes a [width] x [height] texture starting at [offset]. C4/C8/C14X2 need a decoded [palette];
     * an index past its end decodes as 0 (transparent black). Throws IllegalArgumentException
     * when the data is too short or a palette is missing.
     */
    fun decode(
        data: ByteArray, width: Int, height: Int, format: GcFormat, palette: IntArray? = null, offset: Int = 0,
    ): IntArray {
        require(width > 0 && height > 0) { "bad size ${width}x$height" }
        require(!format.paletted || palette != null) { "$format needs a palette" }
        require(offset >= 0 && offset.toLong() + format.byteSize(width, height) <= data.size) { "texture data too short" }
        val out = IntArray(width * height)
        val bw = format.blockW
        val bh = format.blockH
        val blockBytes = bw * bh * format.bitsPerPixel / 8
        fun pal(i: Int) = if (i < palette!!.size) palette[i] else 0
        var p = offset
        var by = 0
        while (by < height) {
            var bx = 0
            while (bx < width) {
                for (y in 0 until bh) {
                    val py = by + y
                    for (x in 0 until bw) {
                        val px = bx + x
                        if (px >= width || py >= height) continue
                        val k = y * bw + x
                        out[py * width + px] = when (format) {
                            GcFormat.I4, GcFormat.C4 -> {
                                val v = u8(data, p + k / 2)
                                val n = if (x % 2 == 0) v shr 4 else v and 15
                                if (format == GcFormat.I4) c4(n).let { argb(it, it, it, it) } else pal(n)
                            }
                            GcFormat.I8 -> u8(data, p + k).let { argb(it, it, it, it) }
                            GcFormat.IA4 -> {
                                val v = u8(data, p + k)
                                val i = c4(v and 15)
                                argb(c4(v shr 4), i, i, i)
                            }
                            GcFormat.C8 -> pal(u8(data, p + k))
                            GcFormat.C14X2 -> pal(((u8(data, p + 2 * k) shl 8) or u8(data, p + 2 * k + 1)) and 0x3FFF)
                            GcFormat.IA8 -> ia8(u8(data, p + 2 * k), u8(data, p + 2 * k + 1))
                            GcFormat.RGB565 -> rgb565((u8(data, p + 2 * k) shl 8) or u8(data, p + 2 * k + 1))
                            GcFormat.RGB5A3 -> rgb5a3((u8(data, p + 2 * k) shl 8) or u8(data, p + 2 * k + 1))
                            // 64-byte block: 16 AR pairs, then 16 GB pairs.
                            GcFormat.RGBA8 -> argb(
                                u8(data, p + 2 * k), u8(data, p + 2 * k + 1),
                                u8(data, p + 32 + 2 * k), u8(data, p + 33 + 2 * k),
                            )
                        }
                    }
                }
                p += blockBytes
                bx += bw
            }
            by += bh
        }
        return out
    }

    /** The [w] x [h] sub-image at ([x], [y]) of a row-major image [width] pixels wide. */
    fun crop(pixels: IntArray, width: Int, x: Int, y: Int, w: Int, h: Int): IntArray {
        require(x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= width && (y + h) * width <= pixels.size) { "crop out of range" }
        return IntArray(w * h) { i -> pixels[(y + i / w) * width + x + i % w] }
    }
}
