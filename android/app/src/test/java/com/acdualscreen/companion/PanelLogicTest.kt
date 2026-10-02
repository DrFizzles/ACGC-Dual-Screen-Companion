package com.acdualscreen.companion

import android.view.InputDevice
import android.view.KeyEvent
import com.acdualscreen.companion.core.GameState
import com.acdualscreen.companion.core.Phase
import com.acdualscreen.companion.core.PlayerMarker
import com.acdualscreen.companion.core.TownMapState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The plain-Kotlin decisions behind the panel UI (no Android runtime needed). */
class PanelLogicTest {

    private val gamepad = InputDevice.SOURCE_GAMEPAD or InputDevice.SOURCE_KEYBOARD
    private val joystick = InputDevice.SOURCE_JOYSTICK
    private val keyboard = InputDevice.SOURCE_KEYBOARD
    private val keyboardWithArrows = InputDevice.SOURCE_KEYBOARD or InputDevice.SOURCE_DPAD

    @Test
    fun controllerKeysAreSwallowedPhoneKeysAreNot() {
        // B and Y would fall back to BACK, A to DPAD_CENTER: always swallowed.
        for (k in listOf(KeyEvent.KEYCODE_BUTTON_A, KeyEvent.KEYCODE_BUTTON_B, KeyEvent.KEYCODE_BUTTON_Y,
            KeyEvent.KEYCODE_BUTTON_START, KeyEvent.KEYCODE_BUTTON_MODE, KeyEvent.KEYCODE_BUTTON_L1, KeyEvent.KEYCODE_BUTTON_16)) {
            assertTrue("$k", ControllerKeys.isControllerKey(k, gamepad))
            assertTrue("$k from an unknown source", ControllerKeys.isControllerKey(k, 0))
        }
        // D-pad and BACK sent by a controller.
        assertTrue(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_DPAD_LEFT, gamepad))
        assertTrue(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_DPAD_CENTER, joystick))
        assertTrue(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_BACK, gamepad))
        // Phone and keyboard keys keep working.
        assertFalse(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_VOLUME_UP, gamepad))
        assertFalse(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_POWER, keyboard))
        assertFalse(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_BACK, keyboard))
        assertFalse(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_DPAD_LEFT, keyboardWithArrows))
        assertFalse(ControllerKeys.isControllerKey(KeyEvent.KEYCODE_A, keyboard))
        // Sources share class bits: a keyboard is not a gamepad, a touchscreen not a joystick.
        assertFalse(ControllerKeys.isFromController(keyboardWithArrows))
        assertFalse(ControllerKeys.isFromController(InputDevice.SOURCE_TOUCHSCREEN))
        assertFalse(ControllerKeys.isFromController(InputDevice.SOURCE_MOUSE))
        assertTrue(ControllerKeys.isFromController(joystick or InputDevice.SOURCE_GAMEPAD))
    }

    @Test
    fun onlyConnectionSettingsRestartThePoller() {
        val p = Prefs()
        assertTrue(p.sameConnection(p.copy(useBottomScreen = !p.useBottomScreen, floatingShows = PanelTab.MAP)))
        assertFalse(p.sameConnection(p.copy(host = "192.168.1.20")))
        assertFalse(p.sameConnection(p.copy(port = 55356)))
        assertFalse(p.sameConnection(p.copy(intervalMs = 500)))
        assertEquals("127.0.0.1:55355 · 250 ms", p.connectionLabel())
    }

    @Test
    fun theStatusPageIgnoresTheMap() {
        val base = GameState(Phase.IN_TOWN, "ok", town = "Cheevo")
        val a = base.copy(map = TownMapState(null, player = PlayerMarker(10f, 10f, 1, 1, 0)))
        val b = base.copy(map = TownMapState(null, player = PlayerMarker(11f, 10f, 1, 1, 0)))
        assertNotEquals(a, b) // the map page must redraw
        assertEquals(a.withoutMap(), b.withoutMap()) // the status page must not
        assertNull(a.withoutMap().map)
        assertTrue(base.withoutMap() === base)
        assertNotEquals(a.withoutMap(), base.copy(town = "Other"))
    }
}
