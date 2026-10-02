package com.acdualscreen.companion

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Display
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast

/** Settings screen: connection settings, permissions, start/stop. Built in code, no XML. */
class MainActivity : Activity() {

    private var loaded = Prefs()
    private lateinit var host: EditText
    private lateinit var port: EditText
    private lateinit var interval: EditText
    private lateinit var bottom: Switch
    private lateinit var floatingMap: RadioButton
    private lateinit var floatingTracker: RadioButton
    private lateinit var info: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = Prefs.load(this)
        loaded = p
        val pad = (16 * resources.displayMetrics.density).toInt()

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        fun label(text: String) = TextView(this).apply { this.text = text; setPadding(0, pad / 2, 0, 0) }
            .also { col.addView(it) }
        fun field(value: String, type: Int) = EditText(this).apply {
            setText(value)
            inputType = type
            isSingleLine = true
        }.also { col.addView(it, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT) }
        fun button(text: String, action: () -> Unit) = Button(this).apply {
            this.text = text
            setOnClickListener { action() }
        }.also { col.addView(it, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT) }

        col.addView(TextView(this).apply {
            text = "AC Panel"
            textSize = 24f
        })
        label("Dolphin host (EmuLink server)")
        host = field(p.host, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        label("Port")
        port = field(p.port.toString(), InputType.TYPE_CLASS_NUMBER)
        label("Poll interval (ms)")
        interval = field(p.intervalMs.toString(), InputType.TYPE_CLASS_NUMBER)
        bottom = Switch(this).apply {
            text = "Use bottom screen (secondary display)"
            isChecked = p.useBottomScreen
            setPadding(0, pad, 0, pad / 2)
        }.also { col.addView(it, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT) }
        label("Floating panel shows (the main-screen box, used when \"Use bottom screen\" is off)")
        val group = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        val floatingStatus = RadioButton(this).apply { text = "Info"; id = View.generateViewId() }
        floatingMap = RadioButton(this).apply { text = "Map"; id = View.generateViewId() }
        floatingTracker = RadioButton(this).apply { text = "Tracker"; id = View.generateViewId() }
        group.addView(floatingStatus)
        group.addView(floatingMap)
        group.addView(floatingTracker)
        group.check(when (p.floatingShows) {
            PanelTab.MAP -> floatingMap.id
            PanelTab.TRACKER -> floatingTracker.id
            PanelTab.STATUS -> floatingStatus.id
        })
        col.addView(group, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

        button("Grant overlay permission") {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        button("Request notification permission") {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            } else {
                toast("Not needed on this Android version")
            }
        }
        button("Start panel") { startPanel() }
        button("Stop panel") {
            stopService(Intent(this, CompanionService::class.java))
            refreshInfo()
        }
        button("Open panel window (split-screen)") { openPanelWindow() }
        label(
            "The panel window is for split screen next to Dolphin (no second display needed). " +
                "Right after it opens it holds the controller (it ignores controller buttons, so " +
                "they cannot close it): tap the game once to give the controller back to Dolphin. " +
                "After that, taps on the panel leave the controller with the game. It is also on " +
                "the launcher as \"AC Panel window\", so it can be dragged from the taskbar into " +
                "split screen."
        ).setTextColor(0xFF9E9E9E.toInt())
        info = label("")
        info.setTextIsSelectable(true)

        setContentView(ScrollView(this).apply { addView(col) })
    }

    override fun onResume() {
        super.onResume()
        refreshInfo()
    }

    override fun onPause() {
        readPrefs()?.save(this)
        super.onPause()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshInfo()
    }

    private fun readPrefs(): Prefs? {
        val portNo = port.text.toString().trim().toIntOrNull()
        val ms = interval.text.toString().trim().toLongOrNull()
        if (portNo == null || portNo !in 1..65535 || ms == null) return null
        return loaded.copy(
            host = host.text.toString().trim().ifEmpty { Prefs.DEFAULT_HOST },
            port = portNo,
            intervalMs = ms.coerceAtLeast(Prefs.MIN_INTERVAL),
            useBottomScreen = bottom.isChecked,
            floatingShows = when {
                floatingMap.isChecked -> PanelTab.MAP
                floatingTracker.isChecked -> PanelTab.TRACKER
                else -> PanelTab.STATUS
            },
        )
    }

    private fun openPanelWindow() {
        val p = readPrefs() ?: return toast("Check the port (1-65535) and poll interval")
        p.save(this)
        // A panel window that is already showing does not subscribe again: apply new settings.
        PollerHub.ensurePrefs(this)
        // Its own task (see the manifest), opened beside this one where the device supports it.
        startActivity(
            Intent(this, PanelActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_LAUNCH_ADJACENT)
        )
    }

    private fun startPanel() {
        val p = readPrefs() ?: return toast("Check the port (1-65535) and poll interval")
        p.save(this)
        if (!Settings.canDrawOverlays(this)) return toast("Grant the overlay permission first")
        startForegroundService(Intent(this, CompanionService::class.java).setAction(CompanionService.ACTION_START))
        toast("Panel started")
        // The notification carries the Stop action, so ask for it here too (Android 13+). The
        // system stops showing this dialog by itself once the user has declined it twice.
        if (!notificationsAllowed()) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        info.postDelayed({ refreshInfo() }, 500)
    }

    private fun notificationsAllowed() = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun refreshInfo() {
        val dm = getSystemService(DisplayManager::class.java)
        val spec = SpecLoader.load(this)
        val notifOk = notificationsAllowed()
        val target = CompanionService.pickDisplay(dm, bottom.isChecked)
        val presentation = dm.getDisplays(DisplayManager.DISPLAY_CATEGORY_PRESENTATION).map { it.displayId }.toSet()
        info.text = buildString {
            append("\nOverlay permission: ${if (Settings.canDrawOverlays(this@MainActivity)) "granted" else "MISSING"}\n")
            append("Notification permission: ${if (notifOk) "granted" else "not granted (long-press the panel to close it)"}\n")
            append("Panel service: ${if (CompanionService.running) "running" else "stopped"}\n")
            append("Town map: ${if (spec.map?.townMap != null) "in the spec" else "not in this spec"}\n")
            append("Spec: ${spec.map?.summary() ?: spec.error} [${spec.source}]\n")
            spec.map?.warnings?.take(5)?.forEach { append("  warning: $it\n") }
            append("\nDisplays:\n")
            for (d in dm.displays) {
                val tags = listOfNotNull(
                    if (d.displayId == Display.DEFAULT_DISPLAY) "default" else null,
                    if (d.displayId in presentation) "presentation" else null,
                    if ((d.flags and Display.FLAG_PRIVATE) != 0) "private" else null,
                ).joinToString(", ")
                val m = d.mode
                append("  #${d.displayId} ${d.name} ${m.physicalWidth}x${m.physicalHeight} [$tags]\n")
            }
            append(
                if (target != null) "Panel would use: #${target.displayId} ${target.name}\n"
                else "Panel would use: nothing (no bottom screen found; turn off \"Use bottom screen\" for a test box)\n"
            )
            append("\nStart the panel before launching the game, or launch it from the bottom screen.")
        }
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
