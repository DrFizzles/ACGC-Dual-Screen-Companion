package com.acdualscreen.companion

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import com.acdualscreen.companion.core.Fruit

/**
 * The panel's look, after the GameCube game's menus: yellow lined paper in a thick red rounded
 * frame on grass, cream speech bubbles with orange borders, brown rounded text. Every page is
 * drawn in design pixels (1240x1080, the Thor's bottom screen) and scaled to the real view.
 *
 * The icons are original drawings (simple paths), not game art.
 */
object AcStyle {
    const val W = 1240f
    const val H_TABS = 1080f
    const val H_NO_TABS = 964f

    const val GRASS = 0xFF47A646.toInt()
    const val GRASS_DARK = 0xFF3B9139.toInt()
    const val GRASS_LIGHT = 0xFF53B451.toInt()
    const val PAPER = 0xFFFFF27A.toInt()
    const val PAPER_LINE = 0xFFF3CB45.toInt()
    const val RED = 0xFFE5261F.toInt()
    const val RED_DARK = 0xFFA3150F.toInt()
    const val BROWN = 0xFF5B3714.toInt()
    const val BROWN_SOFT = 0xFF8A5A22.toInt()
    const val PINK = 0xFFC8189A.toInt()
    const val CREAM = 0xFFFFF9D9.toInt()
    const val ORANGE = 0xFFF59A1B.toInt()
    const val ROW_BG = 0xFFFFFBE6.toInt()
    const val ROW_BORDER = 0xFFE9B53A.toInt()
    const val GREEN = 0xFF2E9E3E.toInt()
    const val GREEN_DARK = 0xFF1F6E25.toInt()
    const val GREEN_DARKER = 0xFF145018.toInt()
    const val GREEN_PALE = 0xFFD9F2B4.toInt()
    const val ALERT = 0xFFD93025.toInt()
    const val LOAN = 0xFFB3261E.toInt()
    const val UNKNOWN = 0xFFA89A7A.toInt()
    const val DIM_FILL = 0xFFF1E6C8.toInt()
    const val MAP_FRAME = 0xFFEF7A1A.toInt()
    const val MAP_SHADOW = 0xFFB9560E.toInt()
    const val COL_FILL = 0xFF6EDB4E.toInt()
    const val COL_EDGE = 0xFF0F5A1A.toInt()
    const val ROW_FILL = 0xFF5CC8FF.toInt()
    const val ROW_EDGE = 0xFF0B4A8A.toInt()
    const val VILLAGER_NAME = 0xFFC2410C.toInt()
    const val WHITE = 0xFFFFFFFF.toInt()

    /** Portrait backgrounds, picked per villager id. */
    val PASTELS = intArrayOf(
        0xFFF9C6D3.toInt(), 0xFFD9C8F2.toInt(), 0xFFF2D7A6.toInt(), 0xFFC9D3DC.toInt(), 0xFFF4C08E.toInt(),
        0xFFFFD29A.toInt(), 0xFFF2DDB8.toInt(), 0xFFCFE3F2.toInt(), 0xFFBFD8F7.toInt(), 0xFFE8DDF5.toInt(),
        0xFFE9CBA8.toInt(), 0xFFCFE8C9.toInt(),
    )

    private var regular: Typeface? = null
    private var bold: Typeface? = null

    /**
     * Fredoka (a rounded face close to the game's lettering) when assets/fonts/Fredoka-*.ttf are
     * bundled; otherwise the system sans.
     */
    fun typeface(ctx: Context, isBold: Boolean): Typeface {
        if (regular == null) {
            regular = load(ctx, "fonts/Fredoka-Medium.ttf") ?: Typeface.create("sans-serif-medium", Typeface.NORMAL)
            bold = load(ctx, "fonts/Fredoka-Bold.ttf") ?: Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
        }
        return if (isBold) bold!! else regular!!
    }

    private fun load(ctx: Context, asset: String): Typeface? =
        try { Typeface.createFromAsset(ctx.assets, asset) } catch (e: Exception) { null }
}

/** Drawing helpers shared by the pages; all sizes are design pixels. */
class AcPainter(private val ctx: Context) {
    val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    val text = Paint(Paint.ANTI_ALIAS_FLAG)
    private val r = RectF()
    private val p = Path()

    fun roundBox(c: Canvas, l: Float, t: Float, rt: Float, b: Float, radius: Float, bg: Int, border: Int, bw: Float) {
        r.set(l, t, rt, b)
        fill.color = bg
        c.drawRoundRect(r, radius, radius, fill)
        if (bw > 0) {
            r.inset(bw / 2, bw / 2)
            stroke.color = border
            stroke.strokeWidth = bw
            c.drawRoundRect(r, radius - bw / 2, radius - bw / 2, stroke)
        }
    }

    /** Like [roundBox] with a hard drop shadow below (the game's chunky buttons). */
    fun shadowBox(c: Canvas, l: Float, t: Float, rt: Float, b: Float, radius: Float, bg: Int, border: Int, bw: Float, shadow: Int, dy: Float) {
        r.set(l, t + dy, rt, b + dy)
        fill.color = shadow
        c.drawRoundRect(r, radius, radius, fill)
        roundBox(c, l, t, rt, b, radius, bg, border, bw)
    }

    /** Yellow lined paper inside a rounded rectangle. */
    fun paper(c: Canvas, l: Float, t: Float, rt: Float, b: Float, radius: Float) {
        r.set(l, t, rt, b)
        fill.color = AcStyle.PAPER
        c.drawRoundRect(r, radius, radius, fill)
        c.save()
        p.reset()
        p.addRoundRect(r, radius, radius, Path.Direction.CW)
        c.clipPath(p)
        fill.color = AcStyle.PAPER_LINE
        var y = t + 60f
        while (y < b) {
            c.drawRect(l, y, rt, y + 4f, fill)
            y += 64f
        }
        c.restore()
    }

    /** Grass: green with two offset dot grids. */
    fun grass(c: Canvas, w: Float, h: Float) {
        c.drawColor(AcStyle.GRASS)
        fill.color = AcStyle.GRASS_DARK
        var y = 28f
        while (y < h + 56) {
            var x = 28f
            while (x < w + 56) {
                c.drawCircle(x - 28f, y - 28f, 12f, fill)
                x += 56f
            }
            y += 56f
        }
        fill.color = AcStyle.GRASS_LIGHT
        y = 28f
        while (y < h + 56) {
            var x = 28f
            while (x < w + 56) {
                c.drawCircle(x, y, 10f, fill)
                x += 56f
            }
            y += 56f
        }
    }

    fun font(size: Float, bold: Boolean, color: Int, align: Paint.Align = Paint.Align.LEFT) {
        text.typeface = AcStyle.typeface(ctx, bold)
        text.textSize = size
        text.color = color
        text.textAlign = align
    }

    /**
     * Draws [s] with its vertical centre at [cy], shrinking it (down to 70%) and then ellipsizing
     * so it fits [maxW]. Returns the width drawn.
     */
    fun line(c: Canvas, s: String, x: Float, cy: Float, maxW: Float = Float.MAX_VALUE): Float {
        var t = s
        val w0 = text.measureText(t)
        if (w0 > maxW) {
            text.textSize *= maxOf(0.7f, maxW / w0)
            if (text.measureText(t) > maxW) {
                val n = text.breakText(t, true, maxW - text.measureText("…"), null)
                t = t.take(maxOf(0, n)) + "…"
            }
        }
        val fm = text.fontMetrics
        c.drawText(t, x, cy - (fm.ascent + fm.descent) / 2, text)
        return text.measureText(t)
    }

    /** Text with a thick outline (the map's row letters and column numbers). */
    fun outlined(c: Canvas, s: String, cx: Float, cy: Float, size: Float, fillColor: Int, edge: Int, edgeW: Float) {
        font(size, true, edge, Paint.Align.CENTER)
        text.style = Paint.Style.STROKE
        text.strokeWidth = edgeW
        text.strokeJoin = Paint.Join.ROUND
        val fm = text.fontMetrics
        val base = cy - (fm.ascent + fm.descent) / 2
        c.drawText(s, cx, base, text)
        text.style = Paint.Style.FILL
        text.color = fillColor
        c.drawText(s, cx, base, text)
    }

    /** Green circle with a white check, centred on (cx, cy). */
    fun checkBadge(c: Canvas, cx: Float, cy: Float, ring: Int = AcStyle.CREAM) {
        fill.color = ring
        c.drawCircle(cx, cy, 20f, fill)
        fill.color = AcStyle.GREEN
        c.drawCircle(cx, cy, 16f, fill)
        stroke.color = AcStyle.WHITE
        stroke.strokeWidth = 4f
        p.reset()
        p.moveTo(cx - 7f, cy + 0.5f)
        p.lineTo(cx - 2f, cy + 5f)
        p.lineTo(cx + 7f, cy - 5f)
        c.drawPath(p, stroke)
    }

    /** A status ring: a circle of [bg] with a [ring]-coloured border. */
    fun ringCircle(c: Canvas, cx: Float, cy: Float, radius: Float, bg: Int, ring: Int, rw: Float = 8f) {
        fill.color = bg
        c.drawCircle(cx, cy, radius, fill)
        stroke.color = ring
        stroke.strokeWidth = rw
        c.drawCircle(cx, cy, radius - rw / 2, stroke)
    }

    // ---------------------------------------------------------------- icons (svg-like units)

    /** Runs [block] with the canvas mapped so a [vb]-unit square fills [size] at (x, y). */
    private inline fun icon(c: Canvas, x: Float, y: Float, size: Float, vb: Float, block: () -> Unit) {
        c.save()
        c.translate(x, y)
        c.scale(size / vb, size / vb)
        block()
        c.restore()
    }

    private fun path(vararg pts: Float, close: Boolean = true): Path {
        p.reset()
        p.moveTo(pts[0], pts[1])
        var i = 2
        while (i < pts.size) {
            p.lineTo(pts[i], pts[i + 1])
            i += 2
        }
        if (close) p.close()
        return p
    }

    private fun fillStroke(c: Canvas, path: Path, f: Int, s: Int, sw: Float) {
        fill.color = f
        c.drawPath(path, fill)
        stroke.color = s
        stroke.strokeWidth = sw
        c.drawPath(path, stroke)
    }

    fun sun(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 64f) {
        stroke.color = 0xFFE8851A.toInt()
        stroke.strokeWidth = 5f
        val rays = floatArrayOf(32f, 4f, 32f, 12f, 32f, 52f, 32f, 60f, 4f, 32f, 12f, 32f, 52f, 32f, 60f, 32f,
            12f, 12f, 18f, 18f, 46f, 46f, 52f, 52f, 12f, 52f, 18f, 46f, 46f, 18f, 52f, 12f)
        c.drawLines(rays, stroke)
        fill.color = 0xFFFFB52E.toInt()
        c.drawCircle(32f, 32f, 13f, fill)
        stroke.strokeWidth = 4f
        c.drawCircle(32f, 32f, 13f, stroke)
    }

    fun cloud(c: Canvas, x: Float, y: Float, size: Float, drops: Int, flakes: Boolean) = icon(c, x, y, size, 64f) {
        p.reset()
        p.addCircle(22f, 30f, 12f, Path.Direction.CW)
        p.addCircle(36f, 24f, 15f, Path.Direction.CW)
        p.addCircle(47f, 32f, 10f, Path.Direction.CW)
        p.addRect(14f, 30f, 50f, 42f, Path.Direction.CW)
        fill.color = 0xFFDCE6EE.toInt()
        c.drawPath(p, fill)
        stroke.color = 0xFF6F8796.toInt()
        stroke.strokeWidth = 3f
        for (i in 0 until drops) {
            val dx = 20f + i * 12f
            if (flakes) {
                fill.color = 0xFF6F8796.toInt()
                c.drawCircle(dx, 54f, 3.5f, fill)
            } else {
                c.drawLine(dx, 48f, dx - 3f, 58f, stroke)
            }
        }
    }

    fun blossom(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 64f) {
        fill.color = 0xFFFFB7CF.toInt()
        for (k in 0 until 5) {
            val a = Math.toRadians(-90.0 + k * 72.0)
            c.drawCircle(32f + 13f * Math.cos(a).toFloat(), 32f + 13f * Math.sin(a).toFloat(), 11f, fill)
        }
        fill.color = 0xFFF7D046.toInt()
        c.drawCircle(32f, 32f, 7f, fill)
    }

    fun leaf(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 96f) {
        p.reset()
        p.moveTo(48f, 10f)
        p.cubicTo(24f, 26f, 18f, 52f, 30f, 74f)
        p.cubicTo(38f, 86f, 58f, 86f, 66f, 74f)
        p.cubicTo(78f, 52f, 72f, 26f, 48f, 10f)
        p.close()
        fillStroke(c, p, 0xFF5ED35A.toInt(), AcStyle.GREEN_DARK, 5f)
        c.drawLine(48f, 22f, 48f, 88f, stroke)
        stroke.strokeWidth = 4f
        c.drawLine(48f, 46f, 36f, 38f, stroke)
        c.drawLine(48f, 60f, 60f, 52f, stroke)
    }

    fun bellBag(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 52f) {
        stroke.color = AcStyle.BROWN_SOFT
        stroke.strokeWidth = 4f
        c.drawPath(path(18f, 12f, 26f, 18f, 34f, 12f, close = false), stroke)
        p.reset()
        p.moveTo(26f, 18f)
        p.cubicTo(10f, 22f, 6f, 42f, 14f, 46f)
        p.cubicTo(20f, 49f, 32f, 49f, 38f, 46f)
        p.cubicTo(46f, 42f, 42f, 22f, 26f, 18f)
        p.close()
        fillStroke(c, p, 0xFFF2D27A.toInt(), AcStyle.BROWN_SOFT, 4f)
        stroke.strokeWidth = 3.5f
        c.drawLine(26f, 28f, 26f, 40f, stroke)
        c.drawLine(21f, 32f, 31f, 32f, stroke)
    }

    fun bank(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 52f) {
        fillStroke(c, path(6f, 20f, 26f, 8f, 46f, 20f), 0xFFF2D27A.toInt(), AcStyle.BROWN_SOFT, 4f)
        for (cx in floatArrayOf(12f, 22f, 30f, 40f)) c.drawLine(cx, 24f, cx, 40f, stroke)
        c.drawLine(6f, 44f, 46f, 44f, stroke)
    }

    fun house(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 52f) {
        fillStroke(c, path(8f, 24f, 26f, 9f, 44f, 24f, 44f, 44f, 8f, 44f), 0xFFF2D27A.toInt(), AcStyle.BROWN_SOFT, 4f)
        fill.color = AcStyle.BROWN_SOFT
        c.drawRect(21f, 30f, 31f, 44f, fill)
    }

    /** Small solid house (map key / villager homes). */
    fun homeIcon(c: Canvas, x: Float, y: Float, size: Float, color: Int) = icon(c, x, y, size, 22f) {
        fillStroke(c, path(1f, 10f, 11f, 2f, 21f, 10f, 21f, 20f, 1f, 20f), color, AcStyle.WHITE, 1.5f)
        fill.color = AcStyle.WHITE
        c.drawRect(9f, 13f, 13f, 20f, fill)
    }

    /** A gear (settings) of outer radius [r] centred on (cx, cy). */
    fun gear(c: Canvas, cx: Float, cy: Float, r: Float, color: Int) {
        p.reset()
        val teeth = 8
        for (k in 0 until teeth * 2) {
            val a0 = Math.PI * 2 * k / (teeth * 2) - Math.PI / (teeth * 2)
            val a1 = a0 + Math.PI * 2 / (teeth * 2)
            val rr = if (k % 2 == 0) r else r * 0.74f
            val x0 = cx + rr * Math.cos(a0).toFloat()
            val y0 = cy + rr * Math.sin(a0).toFloat()
            val x1 = cx + rr * Math.cos(a1).toFloat()
            val y1 = cy + rr * Math.sin(a1).toFloat()
            if (k == 0) p.moveTo(x0, y0) else p.lineTo(x0, y0)
            p.lineTo(x1, y1)
        }
        p.close()
        p.addCircle(cx, cy, r * 0.32f, Path.Direction.CCW)
        p.fillType = Path.FillType.EVEN_ODD
        fill.color = color
        c.drawPath(p, fill)
        p.fillType = Path.FillType.WINDING
    }

    /** The red "you" figure. */
    fun you(c: Canvas, x: Float, y: Float, size: Float) = icon(c, x, y, size, 44f) {
        fill.color = AcStyle.RED
        p.reset()
        p.moveTo(10f, 40f)
        p.quadTo(10f, 24f, 22f, 24f)
        p.quadTo(34f, 24f, 34f, 40f)
        p.close()
        c.drawPath(p, fill)
        c.drawCircle(22f, 13f, 9f, fill)
    }

    fun check(c: Canvas, x: Float, y: Float, size: Float, color: Int) = icon(c, x, y, size, 30f) {
        stroke.color = color
        stroke.strokeWidth = 5f
        c.drawPath(path(6f, 16f, 12f, 22f, 24f, 8f, close = false), stroke)
    }

    fun fossil(c: Canvas, x: Float, y: Float, size: Float, alpha: Int = 255) = icon(c, x, y, size, 48f) {
        fill.alpha = alpha
        p.reset()
        p.moveTo(8f, 30f)
        p.cubicTo(4f, 18f, 14f, 6f, 26f, 7f)
        p.cubicTo(38f, 8f, 45f, 18f, 42f, 29f)
        p.cubicTo(39f, 40f, 26f, 44f, 17f, 41f)
        p.cubicTo(12f, 39f, 9f, 35f, 8f, 30f)
        p.close()
        fill.color = (0xD6B48A or (alpha shl 24))
        c.drawPath(p, fill)
        stroke.color = (0x6B4A2A or (alpha shl 24))
        stroke.strokeWidth = 3f
        c.drawPath(p, stroke)
        p.reset()
        p.moveTo(25f, 25f)
        p.cubicTo(25f, 22f, 29f, 22f, 29f, 25f)
        p.cubicTo(29f, 30f, 21f, 30f, 21f, 25f)
        p.cubicTo(21f, 18f, 33f, 18f, 33f, 25f)
        p.cubicTo(33f, 33f, 17f, 34f, 17f, 25f)
        stroke.strokeWidth = 2.5f
        c.drawPath(p, stroke)
        fill.alpha = 255
    }

    fun sparkle(c: Canvas, x: Float, y: Float, size: Float, alpha: Int = 255) = icon(c, x, y, size, 48f) {
        fill.color = (0xB88A5A or (alpha shl 24))
        c.drawOval(6f, 26f, 42f, 42f, fill)
        val star = path(24f, 4f, 27f, 18f, 40f, 14f, 30f, 24f, 40f, 34f, 27f, 30f, 24f, 44f, 21f, 30f, 8f, 34f, 18f, 24f, 8f, 14f, 21f, 18f)
        fill.color = (0xFFD23F or (alpha shl 24))
        c.drawPath(star, fill)
        stroke.color = (0xC98A00 or (alpha shl 24))
        stroke.strokeWidth = 2.5f
        c.drawPath(star, stroke)
    }

    fun fruit(c: Canvas, f: Fruit, x: Float, y: Float, size: Float) = icon(c, x, y, size, 48f) {
        val leafPath = { lx: Float, ly: Float ->
            p.reset()
            p.moveTo(lx, ly)
            p.cubicTo(lx + 4f, ly - 6f, lx + 11f, ly - 6f, lx + 14f, ly - 3f)
            p.cubicTo(lx + 10f, ly + 2f, lx + 4f, ly + 2f, lx, ly)
            p.close()
            fillStroke(c, p, 0xFF5ED35A.toInt(), AcStyle.GREEN_DARK, 2.5f)
        }
        when (f) {
            Fruit.PEACH -> {
                p.reset()
                p.moveTo(24f, 12f)
                p.cubicTo(10f, 10f, 4f, 22f, 8f, 32f)
                p.cubicTo(12f, 42f, 24f, 44f, 24f, 44f)
                p.cubicTo(24f, 44f, 36f, 42f, 40f, 32f)
                p.cubicTo(44f, 22f, 38f, 10f, 24f, 12f)
                p.close()
                fillStroke(c, p, 0xFFFFB08F.toInt(), 0xFFB9562E.toInt(), 3f)
                p.reset()
                p.moveTo(24f, 14f)
                p.cubicTo(20f, 22f, 20f, 34f, 24f, 43f)
                stroke.color = 0xFFE07A5A.toInt()
                stroke.strokeWidth = 2.5f
                c.drawPath(p, stroke)
                leafPath(24f, 12f)
            }
            Fruit.APPLE -> {
                p.reset()
                p.moveTo(24f, 15f)
                p.cubicTo(14f, 9f, 5f, 16f, 7f, 28f)
                p.cubicTo(9f, 40f, 18f, 45f, 24f, 42f)
                p.cubicTo(30f, 45f, 39f, 40f, 41f, 28f)
                p.cubicTo(43f, 16f, 34f, 9f, 24f, 15f)
                p.close()
                fillStroke(c, p, 0xFFE53935.toInt(), 0xFF8E1B18.toInt(), 3f)
                stroke.color = 0xFF6B3E1A.toInt()
                c.drawLine(24f, 15f, 24f, 6f, stroke)
                leafPath(25f, 10f)
            }
            Fruit.ORANGE -> {
                fill.color = 0xFFFF9A1F.toInt()
                c.drawCircle(24f, 27f, 17f, fill)
                stroke.color = 0xFFB35A00.toInt()
                stroke.strokeWidth = 3f
                c.drawCircle(24f, 27f, 17f, stroke)
                fill.color = 0xFFD97706.toInt()
                c.drawCircle(18f, 24f, 1.6f, fill)
                c.drawCircle(29f, 31f, 1.6f, fill)
                c.drawCircle(22f, 34f, 1.6f, fill)
                leafPath(24f, 10f)
            }
            Fruit.PEAR -> {
                p.reset()
                p.moveTo(24f, 9f)
                p.cubicTo(18f, 9f, 17f, 16f, 17f, 20f)
                p.cubicTo(17f, 24f, 9f, 27f, 9f, 35f)
                p.cubicTo(9f, 42f, 16f, 45f, 24f, 45f)
                p.cubicTo(32f, 45f, 39f, 42f, 39f, 35f)
                p.cubicTo(39f, 27f, 31f, 24f, 31f, 20f)
                p.cubicTo(31f, 16f, 30f, 9f, 24f, 9f)
                p.close()
                fillStroke(c, p, 0xFFCBDB4A.toInt(), 0xFF6B7A12.toInt(), 3f)
                stroke.color = 0xFF6B3E1A.toInt()
                c.drawLine(24f, 9f, 24f, 3f, stroke)
                leafPath(25f, 6f)
            }
            Fruit.CHERRY -> {
                p.reset()
                p.moveTo(15f, 32f)
                p.cubicTo(18f, 20f, 24f, 10f, 32f, 5f)
                p.moveTo(33f, 32f)
                p.cubicTo(32f, 20f, 32f, 12f, 32f, 5f)
                stroke.color = 0xFF3E7D1E.toInt()
                stroke.strokeWidth = 3f
                c.drawPath(p, stroke)
                for ((cx, cy) in listOf(14f to 35f, 33f to 36f)) {
                    fill.color = 0xFFD81B3C.toInt()
                    c.drawCircle(cx, cy, 9f, fill)
                    stroke.color = 0xFF7A0E22.toInt()
                    c.drawCircle(cx, cy, 9f, stroke)
                }
            }
        }
    }
}
