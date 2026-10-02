package com.acdualscreen.companion.core

import com.acdualscreen.companion.net.EmuLinkClient
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.awt.BasicStroke
import java.awt.Color
import java.awt.Font
import java.awt.RenderingHints
import java.awt.geom.Ellipse2D
import java.awt.geom.Path2D
import java.awt.geom.RoundRectangle2D
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO

/**
 * Manual live check, skipped unless AC_LIVE_DUMP_DIR is set: reads the running game through
 * dolphin-lnk (read-only), builds the town map with the app's own code and writes into that
 * directory live_map.png (the composed map with the overlay) and live_panel.png (an AWT preview
 * of TownMapView's layout: frame, labels, highlight, marker). The directory must be outside the
 * project: the images contain decoded game textures that must never be committed or packaged.
 *
 *   AC_LIVE_DUMP_DIR=<scratch dir> gradlew testDebugUnitTest --tests "*LiveMapDumpTest*" --rerun
 */
class LiveMapDumpTest {

    @Test
    fun dumpLiveMap() {
        val dirName = System.getenv("AC_LIVE_DUMP_DIR")
        assumeTrue("AC_LIVE_DUMP_DIR not set", !dirName.isNullOrBlank())
        val dir = File(dirName!!).canonicalFile
        val project = File("../..").canonicalFile // android/app -> project root
        check(!dir.path.startsWith(project.path + File.separator) && dir != project) {
            "refusing to write decoded game images into the project ($dir)"
        }
        val specFile = listOf("../../spec/ac_memory_map.json", "../spec/ac_memory_map.json").map(::File).first { it.isFile }
        val map = MemoryMap.parse(specFile.readText())
        val host = System.getenv("AC_LIVE_HOST") ?: "127.0.0.1"
        val client = EmuLinkClient(host, 55355)
        try {
            val hello = client.handshake()
            println("hello: $hello")
            val dec = Decoder(map).also { it.onHello(hello.gameId, hello.gameHash) }
            val state = dec.poll(client)
            println("phase=${state.phase} town=${state.town} scene=${state.scene} player=${state.playerName}")
            val reader = TownMapReader(map, map.townMap!!).also { it.onHello(hello.gameId, hello.gameHash) }
            val t0 = System.nanoTime()
            val s = reader.poll(client)
            val t1 = System.nanoTime()
            val s2 = reader.poll(client)
            val t2 = System.nanoTime()
            println("first poll ${(t1 - t0) / 1_000_000} ms, steady poll ${(t2 - t1) / 1_000_000} ms, layout reused=${s.layout === s2.layout}")
            println("note: '${s.note}'")
            val lay = s.layout
            if (lay != null) {
                println("acre types: " + lay.acres.joinToString(",") { it.type.toString() })
                println("missing textures: " + lay.acres.count { it.pixels == null })
                println("buildings: " + lay.buildings.joinToString { "${it.key}@${lay.acreLabel(it.blockX, it.blockZ)}${if (it.inTexture) "" else "(fallback)"}" })
                println("grid line colour: %08X".format(lay.lineColor))
            }
            println("houses: " + s.houses.joinToString { "slot${it.slot}@${lay?.acreLabel(it.blockX, it.blockZ)} idx${it.idx} tier${it.tier} exact=${it.exact} (${it.cx},${it.cy})" })
            println("house art: " + s.houseArt.joinToString { "tier${it.tier}=${if (it.pixels != null) "ram" else "fallback"}" })
            println("player: ${s2.player} acre=${s2.player?.let { lay?.acreLabel(it.blockX, it.blockZ) }} indoor=${s2.indoorAcre}")
            if (lay == null) return
            dir.mkdirs()
            val scale = 8
            val ras = TownMapRenderer.compose(lay, s2.houseArt, s2.houses, scale)
            TownMapRenderer.drawOverlay(ras, s2, scale)
            ImageIO.write(image(ras), "png", File(dir, "live_map.png"))
            // What TownMapView draws: the acres at one pixel per texel, scaled up, icons on top.
            val acres = image(TownMapRenderer.composeAcres(lay, 1))
            ImageIO.write(panelPreview(s2, lay, acres, 1000, 1100, 2.625f), "png", File(dir, "live_panel.png"))
            ImageIO.write(panelPreview(s2, lay, acres, 648, 745, 2.625f), "png", File(dir, "live_panel_small.png"))
            println("wrote live_map.png, live_panel.png, live_panel_small.png in $dir")
        } finally {
            client.close()
        }
    }

    /** The app's Poller against the live game for a few seconds with the map wanted (read-only). */
    @Test
    fun livePollerPublishesTheMap() {
        assumeTrue("AC_LIVE_DUMP_DIR not set", !System.getenv("AC_LIVE_DUMP_DIR").isNullOrBlank())
        val specFile = listOf("../../spec/ac_memory_map.json", "../spec/ac_memory_map.json").map(::File).first { it.isFile }
        val map = MemoryMap.parse(specFile.readText())
        val states = java.util.concurrent.LinkedBlockingQueue<GameState>()
        val poller = com.acdualscreen.companion.Poller(map, null, System.getenv("AC_LIVE_HOST") ?: "127.0.0.1", 55355, 250) { states += it }
        poller.setMapWanted(true)
        poller.start()
        val seen = ArrayList<GameState>()
        val end = System.currentTimeMillis() + 4000
        while (System.currentTimeMillis() < end) states.poll(100, java.util.concurrent.TimeUnit.MILLISECONDS)?.let { seen += it }
        poller.stop()
        println("published ${seen.size} states: " + seen.joinToString(" | ") { s ->
            "${s.phase} map=${s.map?.layout != null} player=${s.map?.player?.let { "%.1f,%.1f f=%04X".format(it.mapX, it.mapY, it.facing) }}"
        })
        val layouts = seen.mapNotNull { it.map?.layout }.distinct()
        println("distinct layout objects: ${layouts.size}")
    }

    private fun image(ras: Raster) = BufferedImage(ras.width, ras.height, BufferedImage.TYPE_INT_ARGB).also {
        it.setRGB(0, 0, ras.width, ras.height, ras.pixels, 0, ras.width)
    }

    /** AWT mirror of TownMapView.drawMap (same geometry from TownMapGeometry), for a visual check. */
    private fun panelPreview(m: TownMapState, layout: TownLayout, acres: BufferedImage, w: Int, h: Int, density: Float): BufferedImage {
        val img = BufferedImage(w, h, BufferedImage.TYPE_INT_ARGB)
        val g = img.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON)
        g.color = Color.BLACK
        g.fillRect(0, 0, w, h)
        val f = TownMapGeometry.frame(layout, w.toFloat(), h.toFloat(), 12f * density, false)!!
        val u = f.u
        val fu = TownMapGeometry.FRAME_UNITS
        g.color = Color(0xFFE8892E.toInt(), true)
        g.fill(RoundRectangle2D.Float(f.gridX - fu * u, f.gridY - fu * u, f.gridW + (1 + 2 * fu) * u, f.gridH + (1 + 2 * fu) * u, 2 * u, 2 * u))
        g.color = Color(layout.lineColor, true)
        g.fill(java.awt.geom.Rectangle2D.Float(f.gridX, f.gridY, f.gridW + u, f.gridH + u))
        // Nearest neighbour, as TownMapView draws at 2 px per map unit and more.
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION,
            if (u < 2f) RenderingHints.VALUE_INTERPOLATION_BILINEAR else RenderingHints.VALUE_INTERPOLATION_NEAREST_NEIGHBOR)
        fun blit(img: BufferedImage, l: Float, t: Float, r: Float, b: Float) {
            val x0 = Math.round(l)
            val y0 = Math.round(t)
            g.drawImage(img, x0, y0, Math.round(r) - x0, Math.round(b) - y0, null)
        }
        blit(acres, f.gridX, f.gridY, f.gridX + f.gridW, f.gridY + f.gridH)
        val icons = m.houseArt.map { a -> a.pixels?.let { px -> image(Raster(a.w, a.h, px.copyOf())) } }
        for (h in m.houses.sortedBy { it.tier }) {
            val a = TownMapGeometry.artFor(h, m.houseArt) ?: continue
            val r = TownMapGeometry.houseRect(h, a)
            val icon = icons.getOrNull(if (h.tier in m.houseArt.indices) h.tier else 0)
            if (icon != null) {
                blit(icon, f.x(r[0]), f.y(r[1]), f.x(r[2]), f.y(r[3]))
            } else {
                g.color = Color(a.fallbackColor, true)
                g.fill(java.awt.geom.Rectangle2D.Float(f.x(r[0]), f.y(r[1]), (r[2] - r[0]) * u, (r[3] - r[1]) * u))
            }
        }
        g.font = Font(Font.SANS_SERIF, Font.BOLD, (TownMapGeometry.LABEL_UNITS * 0.8f * u).toInt())
        val fm = g.fontMetrics
        g.color = Color(0xFF8EE08A.toInt(), true)
        layout.colLabels.forEachIndexed { i, l ->
            val x = f.gridX + (i + 0.5f) * layout.acreUnits * u - fm.stringWidth(l) / 2f
            g.drawString(l, x, f.top + TownMapGeometry.LABEL_UNITS * 0.5f * u + (fm.ascent - fm.descent) / 2f)
        }
        g.color = Color(0xFF86C8FF.toInt(), true)
        layout.rowLabels.forEachIndexed { i, l ->
            val x = f.left + TownMapGeometry.LABEL_UNITS * 0.5f * u - fm.stringWidth(l) / 2f
            g.drawString(l, x, f.gridY + (i + 0.5f) * layout.acreUnits * u + (fm.ascent - fm.descent) / 2f)
        }
        val p = m.player
        val box = p?.let { it.blockX to it.blockZ } ?: m.indoorAcre
        if (box != null) {
            val r = layout.acreRect(box.first, box.second)
            val sw = maxOf(2f * density, 0.9f * u)
            g.color = Color(m.highlightColor, true)
            g.stroke = BasicStroke(sw)
            g.draw(java.awt.geom.Rectangle2D.Float(f.x(r[0]) + sw / 2, f.y(r[1]) + sw / 2, (r[2] - r[0]) * u - sw, (r[3] - r[1]) * u - sw))
        }
        if (p != null) {
            val cx = f.x(p.mapX)
            val cy = f.y(p.mapY)
            val radius = maxOf(TownMapGeometry.DOT_RADIUS * u, 4f * density)
            val outline = maxOf(1.5f * density, radius * 0.3f)
            val a = TownMapGeometry.arrow(cx, cy, p.dirX, p.dirY, radius)
            val path = Path2D.Float().apply { moveTo(a[0], a[1]); lineTo(a[2], a[3]); lineTo(a[4], a[5]); closePath() }
            val dot = Ellipse2D.Float(cx - radius, cy - radius, 2 * radius, 2 * radius)
            g.color = Color(m.markerOutline, true)
            g.stroke = BasicStroke(outline * 2, BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND)
            g.draw(path)
            g.draw(dot)
            g.color = Color(m.markerColor, true)
            g.fill(path)
            g.fill(dot)
        }
        g.dispose()
        return img
    }
}
