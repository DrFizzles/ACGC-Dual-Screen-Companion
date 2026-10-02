package com.acdualscreen.companion

import android.app.Activity
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Settings, opened from the gear on the panel: where Dolphin's EmuLink server is and how often to
 * poll it. Built in code, no XML. Saving applies at once (the shared poller restarts when the
 * connection settings change).
 */
class SettingsActivity : Activity() {

    private lateinit var host: EditText
    private lateinit var port: EditText
    private lateinit var interval: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = Prefs.load(this)
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
            text = "AC Duo settings"
            textSize = 24f
        })
        label("Dolphin host (where dolphin-lnk runs; 127.0.0.1 = this device)")
        host = field(p.host, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        label("Port (EmuLink server)")
        port = field(p.port.toString(), InputType.TYPE_CLASS_NUMBER)
        label("Poll interval (ms)")
        interval = field(p.intervalMs.toString(), InputType.TYPE_CLASS_NUMBER)
        button("Done") { if (save()) finish() }
        button("Reset to defaults") {
            host.setText(Prefs.DEFAULT_HOST)
            port.setText(Prefs.DEFAULT_PORT.toString())
            interval.setText(Prefs.DEFAULT_INTERVAL.toString())
        }

        val spec = SpecLoader.load(this)
        label(
            "\nGame data: ${spec.map?.summary() ?: spec.error} [${spec.source}]" +
                (spec.map?.warnings?.take(5)?.joinToString("") { "\n  warning: $it" } ?: "")
        ).apply {
            setTextColor(0xFF9E9E9E.toInt())
            setTextIsSelectable(true)
        }

        setContentView(ScrollView(this).apply { addView(col) })
    }

    override fun onPause() {
        save()
        super.onPause()
    }

    /** Saves valid settings and applies them; false (with a message) when a field is invalid. */
    private fun save(): Boolean {
        val portNo = port.text.toString().trim().toIntOrNull()
        val ms = interval.text.toString().trim().toLongOrNull()
        if (portNo == null || portNo !in 1..65535 || ms == null) {
            Toast.makeText(this, "Check the port (1-65535) and poll interval", Toast.LENGTH_SHORT).show()
            return false
        }
        Prefs(
            host = host.text.toString().trim().ifEmpty { Prefs.DEFAULT_HOST },
            port = portNo,
            intervalMs = ms.coerceAtLeast(Prefs.MIN_INTERVAL),
        ).save(this)
        PollerHub.ensurePrefs(this)
        return true
    }
}
