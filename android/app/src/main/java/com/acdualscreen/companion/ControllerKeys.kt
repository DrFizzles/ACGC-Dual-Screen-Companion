package com.acdualscreen.companion

import android.view.InputDevice
import android.view.KeyEvent

/**
 * Tells key events from a game controller apart, for [PanelActivity] while its window holds input
 * focus. Android turns an unhandled gamepad B or Y into BACK and A/X into DPAD_CENTER, so a
 * controller press there would close the panel or press a tab; the activity swallows these
 * instead. Keys of the phone itself (volume, power…) are never swallowed.
 *
 * Pure function of key code and source (only Android constants), so it is unit-tested on the JVM.
 */
object ControllerKeys {

    /** Keys a controller may also report that must keep working (system and phone keys). */
    private val PASS_THROUGH = setOf(
        KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN, KeyEvent.KEYCODE_VOLUME_MUTE,
        KeyEvent.KEYCODE_MUTE, KeyEvent.KEYCODE_POWER, KeyEvent.KEYCODE_SLEEP, KeyEvent.KEYCODE_WAKEUP,
        KeyEvent.KEYCODE_HOME, KeyEvent.KEYCODE_APP_SWITCH, KeyEvent.KEYCODE_ASSIST,
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_MEDIA_PLAY, KeyEvent.KEYCODE_MEDIA_PAUSE,
        KeyEvent.KEYCODE_MEDIA_NEXT, KeyEvent.KEYCODE_MEDIA_PREVIOUS, KeyEvent.KEYCODE_MEDIA_STOP,
    )

    /** Same set as KeyEvent.isGamepadButton: BUTTON_A..BUTTON_MODE and BUTTON_1..BUTTON_16. */
    fun isGamepadButton(keyCode: Int): Boolean =
        keyCode in KeyEvent.KEYCODE_BUTTON_A..KeyEvent.KEYCODE_BUTTON_MODE ||
            keyCode in KeyEvent.KEYCODE_BUTTON_1..KeyEvent.KEYCODE_BUTTON_16

    /** Like InputEvent.isFromSource: every bit of [wanted] (class bits included) is set. */
    private fun from(source: Int, wanted: Int) = (source and wanted) == wanted

    fun isFromController(source: Int): Boolean =
        from(source, InputDevice.SOURCE_GAMEPAD) || from(source, InputDevice.SOURCE_JOYSTICK)

    /**
     * True for a key event the panel window must swallow: any gamepad button, and any other key
     * (D-pad, BACK…) sent by a gamepad or joystick, except [PASS_THROUGH] keys.
     */
    fun isControllerKey(keyCode: Int, source: Int): Boolean =
        isGamepadButton(keyCode) || (isFromController(source) && keyCode !in PASS_THROUGH)
}
