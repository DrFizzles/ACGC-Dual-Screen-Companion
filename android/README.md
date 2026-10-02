# AC Panel (Android companion, proof of concept)

AC Panel shows live Animal Crossing data on the AYN Thor's bottom screen while the game runs in Dolphin on the top screen. It supports only the USA GameCube release (GAFE01, revision 0).

It reads emulated memory from the **dolphin-lnk** Dolphin fork over its EmuLink UDP server (port 55355) and draws the data in an overlay window on the secondary display. The data shown is the town, player, date and time, weather, Bells, pockets, turnip prices and villagers. A second page shows the **town map** the way the game's map screen draws it: the 5x6 acre images with their buildings, the villager houses, and the player's position. The app only reads memory and never writes it.

On a phone or foldable without a second display, the panel can also run as an ordinary window in **split screen** next to Dolphin (see *Panel window*).

Every address and offset comes from `../spec/ac_memory_map.json`, the shared memory map. That file is packaged into the APK at build time and is the only asset. No item names, villager names, images or other game data ship with the app. Item names are read from the game's own tables in emulated RAM. Villager names are picked up from the game's name cache after you talk to a villager. The map's acre images and house icon are read from RAM and decoded at runtime (`GcTexture.kt`); they are never stored.

## Build

You need the Android SDK (platform 35) and JDK 17 or newer. This project was built with JDK 25, Gradle 9.5.1 (wrapper), AGP 9.3.3 and Kotlin 2.3.21. Kotlin is compiled by AGP's built-in Kotlin support. During the first build, AGP downloads build-tools 36.0.0 by itself.

```bat
cd android
set JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-25.0.1.8-hotspot
gradlew.bat assembleDebug testDebugUnitTest
```

From Git Bash, run `export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-25.0.1.8-hotspot"` and then `./gradlew.bat assembleDebug testDebugUnitTest`.

Output: `app/build/outputs/apk/debug/app-debug.apk`

`local.properties` points at `C:/Users/itsdr/AppData/Local/Android/Sdk`. Change it on another machine.

If `spec/ac_memory_map.json` is missing, the build still succeeds and the app shows **"Spec missing"**. Only that one file is copied out of `spec/`. Any tooling in that folder stays out of the APK.

## Install

Enable USB debugging on the Thor (Settings → About → tap Build number 7 times, then Developer options → USB debugging), then:

```bat
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

## First run

1. Open **AC Panel**.
2. Tap **Grant overlay permission** and allow "Display over other apps" for AC Panel. Then go back.
3. Tap **Request notification permission** (Android 13+) and allow it. **Start panel** also asks for it if it is missing. The panel works without it, but the notification holds the **Stop** button.
4. Check the **Displays** list at the bottom of the screen. The bottom screen should appear as a second display (`#N ... [presentation]` or just a non-default id). **Panel would use** shows which one the panel will take.
5. Tap **Start panel** *before* launching the game. Alternatively, launch AC Panel from the bottom screen.
6. Start dolphin-lnk on the top screen and boot Animal Crossing. The panel shows *Waiting for Dolphin…* until the EmuLink server answers, then *Not in town* on the title screen and menus, then the live data once you are walking around.
7. To stop, use **Stop** in the notification, **Stop panel** in the app, or long-press the panel on the bottom screen.

Settings are saved: host (default `127.0.0.1`), port (`55355`), poll interval (`250` ms), **Use bottom screen** and **Floating panel shows** (Info, Map or Tracker). Turn **Use bottom screen** off to get a small translucent box on the main screen, for example on an ordinary phone. The box lets touches through to the game, so it has no tabs: it shows the page chosen under **Floating panel shows**. With **Use bottom screen** on and no second display, nothing is drawn. The notification says "Bottom screen not found" and the panel appears when the display does.

On the bottom screen the panel has **Info | Map | Tracker** tabs at the top. The chosen tab is remembered.

### Town map

The Map page follows the spec's `map` section (spec/README.md section 13):

- The 30 acre images, decoded from RAM (GX C4 with an RGB5A3 palette), cropped to their 22x22 visible texels and placed in the game's order. Buildings (shop, post office, station, museum, tailor, police box, well, dump, dock and the four player houses) are part of those images, as in the game.
- Villager houses as separate icons: the game's 16x16 IA4 icon from RAM, tinted per height tier with the colours from the game's display lists.
- Column labels 1-5, row labels A-F and an outer frame, drawn by the app as plain text and shapes.
- The player's acre outlined in magenta, and a red dot with a facing arrow at the player's exact position (original shapes, never a character sprite). Indoors, where the spec allows it, only the acre of the building's door is outlined.

Nothing else is shown. The app never reads the item/fg grid, so there are no buried items, fossils, money rocks or ground items. If an acre image fails validation, a plain green acre is drawn instead, with the spec's original fallback marker (coloured square and short label) for a building in it. A house icon that cannot be read becomes a square in its tier colour.

The images and tables are read once per game image and town and then cached. Tables that fail validation are re-read every 5 s, and the layout is rebuilt only if what it uses actually changed. Only the player marker is re-read on every poll (three small extra batch reads), and the villager houses every 4 s. A house whose reads tear keeps its last icon and is read again 0.5 s later. Map reads happen only while a visible panel shows the Map page.

### Panel window (split screen)

**Open panel window (split-screen)** in the settings, or the separate launcher entry **AC Panel window**, opens the same panel (with tabs) as a normal resizeable activity in its own task. Put it next to Dolphin in split screen, for example by dragging **AC Panel window** from the taskbar.

- Its window is `FLAG_NOT_FOCUSABLE`. Taps on it still work (tabs), but a tap never moves input focus, and with it the gamepad, away from Dolphin.
- Right after the window opens, tap the game once so Dolphin has focus. Until then the system treats the panel as the focused app. A focused app without a focusable window makes Android hold key events and report an ANR after 5 s, so the window drops `FLAG_NOT_FOCUSABLE` only while it is the top-resumed activity and sets it again as soon as Dolphin is.
- While the window holds focus it shows "tap the game to give it back" and swallows every controller key and stick movement (`ControllerKeys`). Otherwise Android would turn an unhandled gamepad B or Y into BACK, which closes the panel, and A into a tab press. Phone keys such as volume still work.
- It requests no audio focus, wake lock or orientation, so it does not pause or change Dolphin. In split screen both apps stay resumed.
- It shares the process's single poller with the overlay service (`PollerHub`), so running both does not poll Dolphin twice. It polls only while it is visible.
- Changed host, port or interval apply when a panel starts (the shared poller restarts if they differ from what it uses), when **Open panel window** is pressed, and when **Start panel** is pressed. Each panel's footer shows the settings actually in use.

### Testing on a phone against desktop Dolphin

The EmuLink server listens on all interfaces. That means you can run dolphin-lnk on a PC and set **host** in the app to the PC's LAN IP. Windows Firewall may need to allow inbound UDP 55355 for Dolphin.

### Changing offsets without rebuilding

If a file named `ac_memory_map.json` exists in the app's external files dir, the app uses it instead of the packaged copy:

```bat
adb push ..\spec\ac_memory_map.json /sdcard/Android/data/com.acdualscreen.companion/files/ac_memory_map.json
```

Then tap **Start panel** again. The settings screen shows which copy was loaded and lists any parse warnings. Logs: `adb logcat -s ACPanel`.

## How it works

| File | Role |
|---|---|
| `core/MemoryMap.kt` | Parses the spec (schema 1) with `org.json`. It accepts hex strings and is lenient per field, and collects warnings. |
| `core/Decoder.kt` | Pure Kotlin. Builds one read plan, decodes big-endian values and charmap strings, applies the spec's validity rules and returns a `GameState`. |
| `net/EmuLinkClient.kt` | UDP client. Handles the `EMLKV2` handshake, `EL` batch reads with little-endian headers, 300 ms timeout with one retry, and skips stale replies. |
| `core/MapSpec.kt` | Parses the spec's `map` section. A broken section becomes a warning and the status page keeps working. |
| `core/GcTexture.kt` | Pure Kotlin GameCube texture decoder (I4, I8, IA4, IA8, RGB565, RGB5A3, RGBA8, C4, C8, C14X2; IA8/RGB565/RGB5A3 palettes) to ARGB. |
| `core/TownMap.kt` | `TownMapReader`: reads acre types, textures, building acres, villager houses and the player actor as the spec describes, with rule-8 double reads, and caches what is static. |
| `core/TownMapRenderer.kt` | Composes the acres (one pixel per texel for the view, any scale for tools and tests) and, for tools and tests, the house icons into an ARGB raster. Also holds the geometry (frame, house-icon rectangles, marker arrow) the view shares. |
| `core/StableRead.kt` | Validity rule 8 (read twice, retry once), shared by the decoder and the map reader. |
| `core/DailyReader.kt` | The tracker's reads, only while a visible panel shows the Tracker: each villager's memory of the current player (`last_speak_time` == today), the buried-fossil count from the fg grid and deposit bits (re-read every 2 s), and the player's glowing spot (`shine_pos`). |
| `VillagerNameStore.kt` | Villager names: `assets/villager_names.json` (every villager, built from the disc's `npc_name_str_table.bin` with `tools/pc_client/extract_villager_names.py`) as the fallback, plus names learned live from the game's name cache (checked against its lookup buffer), saved per game image. |
| `Poller.kt` | Background thread. Handshakes again every 10 s and backs off while Dolphin is absent. It publishes in-town data only after two polls agree, because reads can tear. While the map is wanted it adds the map to in-town states, and the marker updates on every poll. It pauses (sends nothing, keeps its caches) while no panel can be seen. |
| `PollerHub.kt` | The process's one poller, shared by the overlay and the panel window. It runs while any panel is subscribed, pauses while none is visible, and reads the map while a visible panel shows it. It restarts when the saved connection settings differ from the ones it uses, and tells each panel which settings are in use. |
| `CompanionService.kt` | `specialUse` foreground service. Picks the display (presentation display, then any other non-private display; the default display only when **Use bottom screen** is off), owns the overlay, follows display changes and pauses polling while the panel's display is off or missing. |
| `PanelRoot.kt` | A panel: the Info, Map and Tracker pages. Each page draws the Info \| Map \| Tracker tabs when the panel is touchable. Moves the content a few px every 60 s against OLED burn-in. |
| `AcStyle.kt`, `AcPage.kt` | The game-menu look (grass, red-framed lined paper, cream bubbles, original icons) and the page base class. Pages are drawn in 1240x1080 design pixels (the Thor's bottom screen) and scaled to fit; tab taps are hit-tested in design space. Uses Fredoka from `assets/fonts/Fredoka-Medium.ttf` / `Fredoka-Bold.ttf` when present, else the system sans. |
| `InfoView.kt` | The Info page: season, date, time, weather; player bubble with the town fruit; pocket, savings and loan (paying / all paid off / no loan); held item, birthday, today's fortune. Redraws only when those change. |
| `TrackerView.kt` | The Tracker page: neighbours talked to today (green ring + check / red ring), fossils dug today (of 5) and the glowing spot. Never shows where anything is buried. Portraits are placeholders (initials). |
| `TownMapView.kt` | The Map page: acre bubble (your acre and the neighbours living there), key, and the map with outlined column numbers and row letters. The acre art is one 110x132 bitmap (one pixel per texel, rebuilt only when the layout changes) plus the 16x16 house icons, scaled without filtering, with the acre highlight and player marker. |
| `PanelActivity.kt` | The panel as a split-screen window (`FLAG_NOT_FOCUSABLE`). While it holds focus it swallows controller keys. |
| `ControllerKeys.kt` | Decides which key events come from a game controller (pure function, unit-tested). |
| `MainActivity.kt` | Settings, permissions, start/stop, panel window, display diagnostics. |

The overlay is a `TYPE_APPLICATION_OVERLAY` window with `FLAG_NOT_FOCUSABLE`, so it never takes key focus and the gamepad stays with Dolphin. The panel window sets the same flag on its activity window.

A normal poll makes two small batch reads: about 85 fixed addresses, each read twice in the same batch (validity rule 8), then a re-check of the GAME pointer. When new item ids show up in the pockets, a third read fetches their name entries. Before trusting the item-name tables, the decoder checks the spec's `runtime_check` pointer table. If that check fails, ids are shown in hex.

Only two packet shapes are ever sent: the 6-byte handshake, and batches with 1–256 entries of 1–4096 bytes. The server treats any other packet longer than 8 bytes as a memory *write*, so `encodeBatch` refuses to build anything else.

The server builds each batch reply in a 65000-byte buffer. An entry that does not fit comes back as len 0, which looks like an invalid address, and a full buffer cuts the reply short. `chunk` keeps every batch's full reply under 60000 bytes, so neither can happen, and a short reply is rejected as stale.

### Validity rules implemented

1. The game id at `0x80000000` must be `GAFE01` and the revision byte must be 0. Otherwise the panel shows **Wrong game**.
2. `gamePT` must point at a GAME whose `exec` equals `play_main`. The pointer is read twice. Otherwise the panel shows **Not in town**.
3. `scene_no` must not be in `validity.not_in_town_scenes`. The title demo also runs `play_main`.
4. `now_private` is checked against the players array. If it falls inside it and is aligned to a record, it gives the player index. Otherwise the player is visiting only if `player_no == 4`, `now_private` is a MEM1 pointer and `(town_id & 0xFF00) == 0x3000`; the panel then shows town-wide data only. Anything else (a NULL or torn pointer, or the game rebuilding `common_data` for a foreign save) shows **Not in town**.
5. The local player's `exists` must equal 1.
6. A villager slot is shown only if `(npc_id & 0xF000) == 0xE000`.
7. Every fixed address is read twice in one batch and kept only when both copies agree; entries that disagree are read twice more and shown as unknown if they still disagree. Impossible values are dropped and shown as `?`: month outside 1–12, hour > 23, weekday outside 0–6, wallet > 99,999, turnip price > 2,000.
8. Strings decode every byte through the charmap and trim trailing spaces only. An item-name or villager-name entry that is all `0x00` or all `0xFF` counts as no name.

## Tests

Run `gradlew.bat testDebugUnitTest`. All tests are JVM tests and need no device.

- **`DecoderTest`** (25 tests) runs against a synthetic 24 MB big-endian memory image, using `src/test/resources/spec_fixture.json`. That fixture follows the schema and uses the decomp's CC0 charmap. Item names in the image are made-up strings. Coverage:
  - every validity rule
  - item-name lookup with `shift`, the cache that clears when the game hash changes, and the runtime table check
  - villager-name harvesting
  - torn and impossible values
  - charmap strings (0x00 is a glyph; trailing spaces are trimmed)
  - all scalar types
- **`EmuLinkClientTest`** (13 tests) runs against `FakeEmuLinkServer`, an in-process UDP copy of `EmuLinkServer.cpp`, including its 65000-byte reply buffer. Coverage:
  - little-endian headers
  - invalid ranges
  - splitting into chunks above 256 entries or 4096 bytes
  - large reads that would overflow the server's reply buffer without chunking
  - skipping stale replies
  - timeouts, and a closed client failing at once
  - a full decode over UDP

  The fake server fails the test if the client ever sends something the real server would treat as a write.
- **`PollerTest`** (5 tests) checks the full sequence (in town → title screen → Dolphin quits), the missing-spec state, pause and resume, and that `stop()` ends a pending handshake at once with nothing published afterwards. It also checks that the map is read over UDP only while it is wanted, and that the layout is reused while the player moves.
- **`GcTextureTest`** (13 tests) checks colour conversions, block order, nibble order, padding, offsets and palettes on hand-made tiles. It also decodes every case in `src/test/resources/crosscheck/texture_vectors.json` and expects the same pixels. That file holds hand tiles plus pseudo-random data for every format and palette format, decoded by the Python `tools/pc_client/gctex.py`. Regenerate it with `python tools/pc_client/texture_vectors_export.py`.
- **`TownMapTest`** (32 tests) runs `TownMapReader` and the renderer on a synthetic map image (`MapImage.kt`: made-up textures, palettes, icon and colours at the spec's addresses). Coverage:
  - acre order and cropping, skipped border types and padding
  - acre types from the save when the table pointer is wrong, and the extra-bridge rule
  - texture and palette validation, and every fallback
  - building acres, villager-house slots and tiers, and the tint
  - the player chain and every condition that hides the marker, the indoor acre, the torn-read grace period, and a torn layout read that must not rebuild the layout
  - caching of static tables and layout, and the size of a steady poll
  - re-reads of incomplete tables that keep the layout and house art unless what they use changed
  - torn villager-house reads that keep the last icon or tier and retry soon
  - that `common_data` is read only where the map spec points (never the item grid)
  - composed pixels and the overlay marker, and that the view's one-pixel-per-texel acre image matches the full composition
- **`LiveMapDumpTest`** (2 tests) is skipped unless `AC_LIVE_DUMP_DIR` is set. Then it reads the running game (read-only), writes the composed map and an AWT preview of the map page into that directory (which must be outside the project), and runs the app's `Poller` against the game for 4 s with the map wanted.
- **`PanelLogicTest`** (3 tests) checks which key events count as controller keys, that only host, port and interval restart the poller, and that the status page ignores map-only changes.
- **`RealSpecTest`** (4 tests) checks the shared `../spec/ac_memory_map.json` if it is present. The spec must parse with no warnings, define every key the panel needs, fit in one batch, and decode the same synthetic town.
- **`CrossCheckTest`** (3 tests) decodes the sparse synthetic images in `src/test/resources/crosscheck/cases.json` (all mock scenarios plus a charmap/trimming/blank-name/out-of-range edge image) and asserts the same canonical summaries the Python client produced. It also fails if `crosscheck/ac_memory_map.json` differs from `../spec/ac_memory_map.json`. `townMapLikeThePythonClient` runs the four synthetic town-map images in `crosscheck/map_cases.json` through `TownMapReader` and expects what `tools/pc_client/townmap.py` produced: the same acre texels (hashed), building acres, villager houses and their centres, tinted icon per tier, player marker (block, map position, facing, direction) and highlighted acre, and `TownMapRenderer.compose` at 8 px per unit must match the Python composition row for row. Regenerate these files with `python tools/pc_client/crosscheck_export.py` after any spec, decoder or town-map change.

The shared spec is declared as an input of the test task, so editing it re-runs the tests.

## Known limitations

- **Not run on hardware yet.** No device or emulator was used. Display selection, overlay placement and focus behaviour on the Thor are untested. The Displays list in the app is there to diagnose this.
- **Panel window focus.** Opening the panel window makes its task the focused one, so tap the game once afterwards to send the controller back to Dolphin. Until then controller presses are swallowed by the panel, and they do not reach the game. Later taps on the panel do not move focus. This is not yet tried on the foldable.
- **Map paths not tested live.** The extra-bridge rule, `field_type` indoors and the indoor acre are `header_derived` in the spec (Cheevo has no extra bridge). The map and its marker are verified live on desktop dolphin-lnk only.
- **Opaque bottom screen.** The panel covers the bottom screen completely and swallows touches there. Stop it from the notification or with a long-press on the panel.
- **Item names.** They depend on the spec's computed table addresses, guarded by the runtime pointer check. If the check fails, the panel shows hex ids and says so in the status line.
- **Villager names.** They appear only after the game has looked them up, for example after talking to a villager. They are forgotten when the panel restarts.
- **Torn reads.** Reads are not synchronised with the emulated CPU. The two-poll agreement and range checks hide most tearing, at the cost of 1–2 poll intervals of latency.
- **Turnip labels.** The weekday labels assume index 0 = Sunday, as the spec says. This is not verified live.
- **Screen and battery.** The panel no longer keeps the screen on by itself; Dolphin does that while it emulates. Polling pauses when the panel's display reports off or doze, which is untested on the Thor.
- **No access control on the server.** dolphin-lnk's EmuLink server binds all interfaces and accepts memory writes from any host that can reach UDP 55355. This app never writes, but use Dolphin only on networks you trust.
- **One release only.** GAFE01 rev 0 only. Achievements and RetroAchievements are out of scope.
