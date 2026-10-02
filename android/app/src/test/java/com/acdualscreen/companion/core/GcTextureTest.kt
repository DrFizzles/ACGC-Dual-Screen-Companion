package com.acdualscreen.companion.core

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * GcTexture on synthetic data only. texture_vectors.json holds hand-made tiles and pseudo-random
 * data decoded by the Python decoder (tools/pc_client/gctex.py, exported by
 * tools/pc_client/texture_vectors_export.py); every case must decode to the same pixels here.
 */
class GcTextureTest {

    private fun px(vararg v: Long) = IntArray(v.size) { v[it].toInt() }

    @Test
    fun rgb5a3() {
        assertEquals(0xFFFFFFFF.toInt(), GcTexture.rgb5a3(0xFFFF))
        assertEquals(0xFF000000.toInt(), GcTexture.rgb5a3(0x8000))
        assertEquals(0xFFFF0000.toInt(), GcTexture.rgb5a3(0xFC00))
        assertEquals(0xFF080808.toInt(), GcTexture.rgb5a3(0x8421))
        assertEquals(0, GcTexture.rgb5a3(0x0000))
        assertEquals(0xFFFFFFFF.toInt(), GcTexture.rgb5a3(0x7FFF))
        assertEquals(0x6DAA55FF, GcTexture.rgb5a3(0x3A5F))
    }

    @Test
    fun rgb565AndIa8() {
        assertEquals(0xFFFF0000.toInt(), GcTexture.rgb565(0xF800))
        assertEquals(0xFF00FF00.toInt(), GcTexture.rgb565(0x07E0))
        assertEquals(0xFF0000FF.toInt(), GcTexture.rgb565(0x001F))
        assertEquals(0xFF848284.toInt(), GcTexture.rgb565(0x8410))
        assertEquals(0x80404040.toInt(), GcTexture.ia8(0x80, 0x40))
    }

    @Test
    fun palettes() {
        val raw = byteArrayOf(0x00, 0x00, 0xFC.toByte(), 0x00, 0x80.toByte(), 0x1F)
        assertArrayEquals(px(0, 0xFFFF0000, 0xFF0000FF), GcTexture.decodePalette(raw, 3, TlutFormat.RGB5A3))
        assertArrayEquals(px(0xFC000000), GcTexture.decodePalette(raw, 1, TlutFormat.IA8, offset = 2))
        assertArrayEquals(px(0xFFFF0000), GcTexture.decodePalette(byteArrayOf(0xF8.toByte(), 0), 1, TlutFormat.RGB565))
        assertThrows { GcTexture.decodePalette(raw, 4, TlutFormat.RGB5A3) }
    }

    @Test
    fun byteSizes() {
        assertEquals(512, GcFormat.C4.byteSize(32, 32))
        assertEquals(256, GcFormat.IA4.byteSize(16, 16))
        assertEquals(64, GcFormat.RGB5A3.byteSize(5, 3))
        assertEquals(64, GcFormat.RGBA8.byteSize(4, 4))
        assertEquals(64, GcFormat.I4.byteSize(9, 1))
        assertEquals(GcFormat.C4, GcFormat.parse("CI4"))
        assertEquals(GcFormat.C14X2, GcFormat.parse("CI14X2"))
        assertEquals(GcFormat.IA4, GcFormat.parse("ia4"))
        assertEquals(null, GcFormat.parse("CMPR"))
    }

    @Test
    fun i4Tiles() {
        val data = ByteArray(64)
        data[0] = 0x1F; data[4] = 0x20; data[32] = 0xF0.toByte()
        val p = GcTexture.decode(data, 16, 8, GcFormat.I4)
        assertEquals(0x11111111, p[0])
        assertEquals(0xFFFFFFFF.toInt(), p[1])
        assertEquals(0x22222222, p[16])
        assertEquals(0xFFFFFFFF.toInt(), p[8])
        assertEquals(0, p[9])
    }

    @Test
    fun ia4AndI8() {
        val data = ByteArray(32)
        data[0] = 0xF0.toByte(); data[1] = 0x0F; data[8] = 0x5A
        val p = GcTexture.decode(data, 8, 4, GcFormat.IA4)
        assertEquals(0xFF000000.toInt(), p[0])
        assertEquals(0x00FFFFFF, p[1])
        assertEquals(0x55AAAAAA, p[8])
        assertEquals(0x5A5A5A5A, GcTexture.decode(data, 8, 4, GcFormat.I8)[8])
    }

    @Test
    fun c4BlocksAndPalette() {
        val pal = px(0, 0xFF112233, 0xFF445566) + IntArray(13) { -1 }
        val data = ByteArray(128)
        data[0] = 0x12; data[32] = 0x21; data[64] = 0x20
        val p = GcTexture.decode(data, 16, 16, GcFormat.C4, pal)
        assertEquals(0xFF112233.toInt(), p[0])
        assertEquals(0xFF445566.toInt(), p[1])
        assertEquals(0xFF445566.toInt(), p[8])
        assertEquals(0xFF112233.toInt(), p[9])
        assertEquals(0xFF445566.toInt(), p[8 * 16])
        assertEquals(0, p[2])
    }

    @Test
    fun c8ShortPaletteReadsZero() {
        val data = ByteArray(32)
        data[1] = 1; data[2] = 200.toByte()
        val p = GcTexture.decode(data, 8, 4, GcFormat.C8, px(0x11, 0x22))
        assertArrayEquals(px(0x11, 0x22, 0), p.copyOfRange(0, 3))
    }

    @Test
    fun rgba8Block() {
        val data = ByteArray(64)
        data[0] = 0x80.toByte(); data[1] = 0x11; data[32] = 0x22; data[33] = 0x33
        data[30] = 1; data[31] = 2; data[62] = 3; data[63] = 4
        val p = GcTexture.decode(data, 4, 4, GcFormat.RGBA8)
        assertEquals(0x80112233.toInt(), p[0])
        assertEquals(0x01020304, p[15])
    }

    @Test
    fun paddingAndOffset() {
        val data = ByteArray(65) { if (it == 0) 9 else if (it % 2 == 1) 0x80.toByte() else 0x1F }
        val p = GcTexture.decode(data, 5, 3, GcFormat.RGB5A3, offset = 1)
        assertEquals(15, p.size)
        assertTrue(p.all { it == 0xFF0000FF.toInt() })
    }

    @Test
    fun errors() {
        assertThrows { GcTexture.decode(ByteArray(10), 8, 8, GcFormat.I4) }
        assertThrows { GcTexture.decode(ByteArray(512), 32, 32, GcFormat.C4) }
        assertThrows { GcTexture.decode(ByteArray(512), 0, 32, GcFormat.I8) }
        assertThrows { GcTexture.decode(ByteArray(512), 32, 32, GcFormat.C4, IntArray(16), offset = 1) }
    }

    @Test
    fun crop() {
        val img = IntArray(16) { it }
        assertArrayEquals(intArrayOf(5, 6, 9, 10), GcTexture.crop(img, 4, 1, 1, 2, 2))
        assertThrows { GcTexture.crop(img, 4, 3, 3, 2, 2) }
    }

    @Test
    fun matchesPythonVectors() {
        val text = GcTextureTest::class.java.classLoader!!.getResource("crosscheck/texture_vectors.json")!!.readText()
        val cases = JSONObject(text).getJSONArray("cases")
        assertTrue(cases.length() >= 60)
        val seen = HashSet<GcFormat>()
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val fmt = GcFormat.parse(c.getString("format"))!!
            seen += fmt
            val data = hex(c.getString("data"))
            var pal: IntArray? = null
            if (c.has("tlut")) {
                val tf = TlutFormat.parse(c.getString("tlut_format"))!!
                pal = GcTexture.decodePalette(hex(c.getString("tlut")), c.getInt("tlut_count"), tf)
                assertArrayEquals("$name palette", argbList(c.getJSONArray("palette")), pal)
            }
            val got = GcTexture.decode(data, c.getInt("width"), c.getInt("height"), fmt, pal, c.getInt("offset"))
            assertArrayEquals(name, argbList(c.getJSONArray("expected")), got)
        }
        assertEquals(GcFormat.entries.toSet(), seen)
    }

    private fun argbList(a: org.json.JSONArray) = IntArray(a.length()) { a.getString(it).toLong(16).toInt() }

    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }

    private fun assertThrows(block: () -> Unit) {
        try {
            block()
        } catch (e: IllegalArgumentException) {
            return
        }
        throw AssertionError("expected IllegalArgumentException")
    }
}
