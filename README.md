# ACGC Dual-Screen Companion

A second-screen companion for **Animal Crossing (GameCube, USA)** running in Dolphin. It reads the
game's memory live (read-only) and shows it on a second display, made for the **AYN Thor's bottom
screen** (1240×1080) while the game plays on the top screen. It also runs on an ordinary phone in
split screen, and there is a text dashboard for the PC.

The panel has three pages, styled after the GameCube game's menus:

- **Info**: season, date, time and weather; player name, town and town fruit; Bells in the pocket,
  savings and the house loan (paying / all paid off / no loan yet); held item, birthday and
  today's fortune.
- **Map**: the town map drawn from the game's own acre art (buildings included), villager houses,
  your acre and position, the neighbours living in your acre, and a key.
- **Tracker**: which neighbours you have talked to today, how many of the day's 5 fossils you have
  dug up, and whether you have dug up your glowing spot. It never shows where anything is buried.

> **Status: proof of concept.** It works against a live game on desktop Dolphin and on Android
> (tested on a Pixel phone; not yet on a Thor). Only the unmodified USA release `GAFE01`,
> revision 0, is supported. Mods such as Animal Crossing Deluxe can move things in memory.

## How it works

```
Animal Crossing ──► dolphin-lnk (Dolphin fork) ──UDP 55355 "EmuLink"──► companion app / PC client
                     reads emulated RAM on request                       decodes it with spec/ac_memory_map.json
```

The game runs in **[dolphin-lnk](https://github.com/EmuLnk/dolphin-lnk)**, a Dolphin fork with an
*EmuLink* UDP server that answers memory reads. The companion asks for the bytes it needs a few
times a second and decodes them with a shared memory map, `spec/ac_memory_map.json`. Every address
in that map is derived from the
[Animal Crossing decompilation](https://github.com/ACreTeam/ac-decomp) and, where possible, checked
against a live game. The app never writes to the game's memory.

## Repository layout

| Path | What it is |
|---|---|
| `android/` | The Android app ("AC Panel"): Kotlin, no external libraries. See [android/README.md](android/README.md). |
| `spec/` | The memory map (`ac_memory_map.json`) and the tools that generate and check it. See [spec/README.md](spec/README.md). |
| `tools/pc_client/` | Python 3.12 PC client: text dashboard, town-map PNG, a mock EmuLink server for testing without Dolphin, and helper tools. See [tools/pc_client/README.md](tools/pc_client/README.md). |

Not in this repository: the dolphin-lnk source and APKs (get them from the dolphin-lnk project),
and anything from the game itself apart from the bundled villager-name list (see below).

## What you need

- **Animal Crossing (USA)**, `GAFE01` revision 0, as your own disc image (ISO or RVZ).
- **dolphin-lnk** with the EmuLink server enabled:
  - Android (Thor or phone): the dolphin-lnk APK from its releases page. This project was built
    against `v2603a-lnk3`. Turn on the EmuLink server in Dolphin's settings.
  - Windows: build dolphin-lnk from source. EmuLink is on by default (`[Core] EmuLinkServerEnabled`).
  - The server listens on UDP 55355 only while a game is running.
- To build the app: **JDK 17** and the **Android SDK** (compileSdk 35). Android Studio provides both.
- For the PC client and the spec tools: **Python 3.12** (standard library only).

## Quick start: Android (Thor or phone)

1. Build the app:

   ```bash
   cd android
   ./gradlew assembleDebug
   ```

   On Windows use `gradlew.bat`. If your default Java is older than 17, set `JAVA_HOME` to a JDK 17 first.
   The APK is `android/app/build/outputs/apk/debug/app-debug.apk`.

2. Install it, plus dolphin-lnk if you haven't yet:

   ```bash
   adb install -r android/app/build/outputs/apk/debug/app-debug.apk
   ```

3. Open **AC Panel**. Keep the host as `127.0.0.1` and the port as `55355` when Dolphin runs on the same device.
4. Choose how the panel appears:
   - **AYN Thor (two screens):** grant the overlay permission, keep **Use bottom screen** on and
     tap **Start panel**. The panel fills the bottom screen; tap its tabs to switch pages. Long-press
     the panel to close it.
   - **Phone (one screen):** tap **Open panel window (split-screen)** and put it beside Dolphin.
     Alternatively, turn **Use bottom screen** off for a small floating box. It can't be touched, so it
     shows the one page picked under **Floating panel shows**.
5. Start Animal Crossing in dolphin-lnk and load your town. The panel says "Not in town" on the title
   screen and fills in once you are playing.

More detail, including running the app against Dolphin on a PC over Wi-Fi and replacing the
memory map without rebuilding, is in [android/README.md](android/README.md).

## Quick start: PC

With dolphin-lnk running Animal Crossing on the same PC:

```bash
python tools/pc_client/dashboard.py
```

Useful options: `--once` for a single poll, `--json` for machine-readable output, `--map PATH`
to keep a PNG of the town map up to date (outside the project folder), and `--host` to read a
Dolphin on another machine. See [tools/pc_client/README.md](tools/pc_client/README.md).

### Without Dolphin

The mock server serves a synthetic town with invented names over the same protocol, so the app
and the PC client can be tried without the game:

```bash
python tools/pc_client/mock_server.py --synthetic
```

## Tests

```bash
python -m unittest discover -s tools/pc_client/tests
```

```bash
python spec/tools/layout_check.py
```

```bash
cd android && ./gradlew testDebugUnitTest
```

The Android tests are JVM tests and need no device. A cross-check makes the Python and Kotlin
decoders decode the same synthetic memory images and compares the results.

## Updating generated files

- **Memory map**: `spec/ac_memory_map.json` is generated. Don't hand-edit it. Change the tables in
  `spec/tools/build_memory_map.py` and run:

  ```bash
  python spec/tools/build_memory_map.py --decomp <path-to-ac-decomp>
  ```

  Then refresh the Android cross-check fixtures:

  ```bash
  python tools/pc_client/crosscheck_export.py
  ```

- **Villager names**: `android/app/src/main/assets/villager_names.json` holds all 236 villager
  names. The game keeps these in ARAM, which EmuLink cannot read, so they ship with the app as a
  fallback. Names read from the running game take priority. The list was extracted from the game's own
  `npc_name_str_table.bin`. To rebuild it from your disc:

  ```bash
  DolphinTool extract -i "Animal Crossing (USA).rvz" -o <dir> -s forest_1st.arc
  DolphinTool extract -i "Animal Crossing (USA).rvz" -o <dir> -s forest_2nd.arc
  python tools/pc_client/extract_villager_names.py <dir>/files/forest_1st.arc <dir>/files/forest_2nd.arc
  ```

- **Font (optional)**: the panel uses the system sans-serif unless you add `Fredoka-Medium.ttf` and
  `Fredoka-Bold.ttf` (SIL Open Font License, from Google Fonts) to `android/app/src/main/assets/fonts/`.

## Known limitations

- Only `GAFE01` revision 0. Other regions, revisions and mods are refused or may decode wrongly.
- **Portraits**: the tracker's villager portraits are placeholders (the villager's initial on a colour).
- **Fossils**: the count assumes the usual 5 buried fossils a day ("dug" = 5 minus those still buried).
- **Glowing spot**: if you dig your glowing spot and then save and continue, the game forgets the
  spot, so the tracker shows "None today".
- **Memory addresses**: most addresses come from the decompilation and layout arithmetic, and only some
  have been confirmed on a live game. Each address's evidence is listed in `spec/ac_memory_map.json`.
- **Network**: the EmuLink server answers anyone who can reach UDP 55355, and it accepts memory
  **writes**. Use it only on networks you trust. This app only reads.
- **Hardware**: not yet tested on an AYN Thor.

## Credits

- [dolphin-lnk](https://github.com/EmuLnk/dolphin-lnk) (EmuLink server) and the Dolphin project.
- The Animal Crossing decompilation project, whose CC0 headers, symbols and character map the memory
  map is derived from.
- RetroAchievements code notes for Animal Crossing, used to cross-check addresses such as the
  house upgrade state.

Animal Crossing is a trademark of Nintendo. This is an unofficial fan project and is not affiliated
with Nintendo.
