# PC client (proof of concept)

A small Python client that reads live Animal Crossing (GameCube, USA `GAFE01`
rev 0) state from the **dolphin-lnk** EmuLink UDP server and prints a
refreshing text dashboard. It is the desktop counterpart of the Thor
bottom-screen companion and is meant for checking offsets against a running
game before trying anything on the device.

It uses only the Python 3.12 standard library and only reads memory. The client
cannot build EmuLink write packets.

| File | Purpose |
|---|---|
| `emulink.py` | `EmuLinkClient` (UDP, EMLKV2): `handshake()`, `read()`, `batch_read()`. Also `MemoryImageSource`, which serves the same calls from a `mem1.raw` dump |
| `acmap.py` | Loads the shared spec, decodes fields, applies the validity rules, detects the current player, looks up item names, and returns a plain `GameState` dict |
| `dashboard.py` | Command-line dashboard. `--map PATH` also keeps a PNG of the town map up to date |
| `townmap.py` | Town map. Reads the spec's `map` section, decodes the acre images and the house icon from RAM, finds buildings, villager houses and the player, and composes the map image (see *Town map* below) |
| `gctex.py` | GameCube texture decoder to RGBA (I4, I8, IA4, IA8, RGB565, RGB5A3, RGBA8, and C4/CI4, C8/CI8, C14X2 with IA8/RGB565/RGB5A3 palettes), plus a small PNG writer |
| `texture_vectors_export.py` | Writes `android/app/src/test/resources/crosscheck/texture_vectors.json`: synthetic texture cases decoded by `gctex.py`, which the Android `GcTextureTest` must decode to the same pixels. Rerun after changing `gctex.py` (`tests/test_texture_vectors.py` fails while the file is stale) |
| `mock_server.py` | Read-only EmuLink server for testing without Dolphin. It serves a dump or a synthetic image built from the spec, including a made-up town map |
| `tests/` | `unittest` suite. It uses `tests/fixtures/spec_fixture.json`, plus the real spec when present |

All offsets come from `spec/ac_memory_map.json` at the project root, or from the
file given with `--spec`. The client does not hard-code any game layout, and it
reads every name from emulated RAM at runtime.

## Running against desktop dolphin-lnk

1. Start dolphin-lnk (`dolphin-lnk/Binary/x64/Dolphin.exe` once built). The
   EmuLink server is on by default; the setting is `[Core] EmuLinkServerEnabled`
   in `Dolphin.ini`. On Android it is a toggle in Dolphin's settings.
2. Boot Animal Crossing (`GAFE01`, rev 0). The server listens on **UDP 55355**
   only while a game is running: it starts when the game boots and stops when
   emulation stops.
3. From the project root:

   ```
   python tools/pc_client/dashboard.py
   ```

   You will see these statuses:
   * `disconnected`: no emulator or no game is running yet.
   * `Unsupported game`: the id or revision is not `GAFE01` rev 0.
   * `Not in town (...)`: the title screen, loading, or one of the scenes in
     the spec's `validity.not_in_town_scenes` (title demo, player select,
     intro). It also covers the case where the current player's record is not in use
     (`exists` != 1; status code `no_player`), and the case where `now_private`
     is neither a resident slot nor a valid visitor (validity rule 3c, e.g. while
     the game rebuilds `common_data` for a foreign save).
   * `In town as player N`: town, player, date and time, weather, Bells,
     pockets, turnip prices, and villagers. A villager's name appears once the game
     has looked it up (see *Villager names* below).
   * `Visiting / foreign player`: only town-wide information is shown. This
     needs `player_no == 4`, a MEM1 `now_private` and
     `(town_id & 0xFF00) == 0x3000` (rule 3b).

4. Useful flags:

   ```
   --interval 0.5      refresh period in seconds
   --once              poll once and exit (exit code 0 = connected to the right game)
   --json              GameState as JSON (one object per line when looping)
   --host 192.168.x.y  read a Dolphin on another machine, such as the Thor over Wi-Fi
   --port 55355
   --timeout 0.5       UDP timeout per attempt (each request is tried 3 times)
   --spec PATH         use a different memory map
   --map PATH          write the town map as a PNG (rewritten whenever it changes);
                       PATH must be outside the project (refused with exit code 2)
   --map-scale N       map pixels per map unit; an acre is 22 units (default 4: 499x593 px)
   --no-map            skip the town map (no "map" in --json, no Map line)
   ```

**Network note:** the EmuLink server binds to all interfaces. It answers anyone
who can reach UDP 55355, and it also accepts memory **writes** from them. Use it
only on trusted networks. If Windows Firewall asks about Dolphin, allow private
networks only. Reading from the same PC works through loopback with no firewall
change.

## Without Dolphin

Mock server, with synthetic data built from the spec. All the names in it are
made up:

```
python tools/pc_client/mock_server.py --synthetic                 # port 55355
python tools/pc_client/mock_server.py --synthetic --port 55399 --scenario title
python tools/pc_client/dashboard.py --port 55399
```

Scenarios: `town`, `title`, `title_demo`, `null_game`, `visiting`,
`rebuilding` (NULL `now_private`, `town_id` 0), `no_player`, `wrong_game`.
`--save-image mem1.raw` also writes the synthetic image to disk.

The synthetic image includes a made-up town map: flat-coloured acre images
with an orientation mark, an invented house icon, and a player in acre C-2
facing north. `--no-map` leaves the map out.

From a real memory dump:

1. In Dolphin, enable *Options → Configuration → Interface → Enable Debugging UI*.
2. Open *View → Memory*, then choose *Export → Dump MRAM*. This writes
   `Dump/mem1.raw` in Dolphin's user folder.
3. Run either of these:

```
python tools/pc_client/dashboard.py --from-dump path/to/mem1.raw --once
python tools/pc_client/mock_server.py --dump path/to/mem1.raw     # then point the dashboard at it
```

A dump maps `offset = addr & 0x3FFFFFFF`, which equals `addr & 0x01FFFFFF`
inside MEM1. Ranges outside the file count as invalid, the same as on the server.

## How it reads

* **One request per refresh.** Each refresh normally sends one UDP batch
  request. That request covers the game id and revision, the `gamePT` pointer,
  `GAME.exec` at the pointer seen on the last poll, the item-name check table,
  the villager-name cache, every global, all 4 player records and all 15
  villagers. Fields at most 128 bytes apart are merged into one range and
  sliced locally. With the real spec, about 317 fields become 57 ranges, read
  twice (114 entries, about 5 KB per reply). A second request is sent only to
  follow a new `gamePT`, to fetch item names that are not cached yet, or to
  re-read a range whose two copies differed. `round_trips` in the GameState
  counts UDP request/reply exchanges, including the handshake, which repeats
  every 10 s and after any error.
* **Validity rules.** The rules come from the spec's `validity` section:
  1. The game id and revision must match.
  2. `BE32(gamePT)` must be a MEM1 pointer, and `GAME.exec` must equal
     `play_main`.
  3. (a) `now_private` points to one of the 4 player records: that is the
     current player. (b) Otherwise, if `player_no == 4`, `now_private` is a MEM1
     pointer and `(town_id & 0xFF00) == 0x3000`, the player is visiting and only
     globals and villagers are shown (the `town_id` test is skipped if the spec
     has no `town_id`). (c) Anything else shows `Not in town`.
  4. That player's `exists` field must be exactly 1.
  5. `scene_no` must not be in `validity.not_in_town_scenes`. The title demo,
     player select and intro also run `play_main`, and the demo fills
     `now_private` with a random player.
  8. Every range is read twice in the same request. A field is accepted only
     when both copies agree. Ranges that disagree are read twice more, and a
     field that still disagrees is shown as unknown, with a warning.
     Impossible values are rejected and shown as `?` with a warning:
     `rtc_month` outside 1-12, `rtc_hour` > 23 (and the other clock fields),
     `wallet` > 99999, and any turnip price > 2000. A spec field's own
     `min`/`max` overrides these.

  10. The `players` list (residents) uses `(land_id & 0xFF00) == 0x3000`;
     `travelling` marks a resident whose `exists` is not 1 (shown as "(away)").

  When rules 1, 2, 3c, 4 or 5 fail, the GameState carries a status and empty
  `globals`/`player`/`pockets`/`villagers` instead of garbage, and
  `in_gameplay` is false. `in_gameplay` is true only for `in_town` and
  `visiting`.
* **Item names.** Every field marked `"item_id": true` in the spec is resolved:
  pockets, held tool, shirt, villager shirts and the town fruit. Pocket
  conditions use the field's `slot_enum`. An id is matched to a range in
  `item_names.ranges`. Its entry is read at
  `table_addr + ((id - id_min) >> shift) * entry_len`, decoded with the spec
  charmap, and trailing spaces are trimmed. Before any name is fetched, the 16
  pointers at `item_names.runtime_check.pointer_table_addr` must equal
  `expected`. This check runs on every refresh and costs 64 bytes. If it
  fails, the dashboard shows hex ids and a warning. An entry that is all
  `0x00` or all `0xFF` counts as no name, because `0x00` is a glyph in this
  charset. Names are cached per item id, and only while in town. The cache is
  cleared when the game hash, game id or revision changes. Empty slots
  (`item_names.empty_ids`) show `—`. Ids with no name show as hex.
* **Villager names.** Villager names are not kept in MEM1. The game keeps the
  last name it looked up at `extras.npc_name_cache`. The client keeps that
  name when its `npc_id` belongs to one of this town's villagers, so names
  appear as you talk to villagers. The cache is cleared with the item-name
  cache.
* **Batching limits.** Batches use at most 256 entries per packet. Each reply
  stays at or under the server's 65000-byte buffer, or under `max_reply` if
  that is set lower. Requests over 4096 bytes are split into chunks. If the
  server answers fewer entries than were asked for, the client requests the
  rest again.
* **Late replies.** The protocol has no request ids, so a reply that arrives
  after its timeout could be taken as the answer to the next request with the
  same shape. Within one request, every retry sends the same packet, so any
  reply is valid. After a request that needed a retry, or that failed, the
  next request goes out from a new socket on a new source port, and late
  replies arrive at the closed one. A server that is consistently slower than
  `--timeout` therefore produces errors, not wrong data. If that happens,
  raise `--timeout`.

## Town map

`townmap.py` draws the town the way the game's map screen does, and nothing
else.

**What the map shows**

* **Acre images.** All 30 acres, in 5 columns (labelled 1-5) and 6 rows
  (labelled A-F), in the order `mMP_make_max_no_table` lays them out.
  * Each acre is a 32×32 C4 texture with an RGB5A3 palette. The client reads
    it from RAM through the spec's pointer tables and crops it to the visible
    22×22 texels.
  * Rivers, cliffs, bridges, rails, the beach and every building in the acre
    art come out exactly as the game draws them.
  * The orange frame, the labels and the closing separator lines on the right
    and bottom edges are this client's own drawing.
* **Building indicators.** The game draws the shop, post office, station,
  dump, wishing well, police box, museum, tailor, dock and the four player
  houses inside their acre images, so they appear with the acre art.
  * They are also listed in the status (`buildings`).
  * An acre whose image fails validation is drawn as a plain placeholder. If
    that acre holds a building, an original coloured square with a short label
    from the spec marks it.
* **Villager houses.** One house icon per villager, decoded from the 16×16 IA4
  icon in RAM.
  * Each icon is tinted with the PRIM and ENV colours of its height tier. These
    colours are read from the game's display lists.
  * Each icon sits on the house spot the game uses.
  * If the icon cannot be read, a square in the tier colour is drawn instead.
* **Player.** An original red dot with a white outline at the exact position,
  an arrow for the facing direction, and a magenta box around the current
  acre.
  * The marker is shown only outdoors in town: scene 7, `field_type` 0, and
    blocks 1-5 × 1-6.
  * Indoors the marker is hidden. When the game's indoor rule applies
    (`field_type` != 0 and `next_scene` != 0), only the box is drawn, around
    the acre of the building's exit door. This rule has not been tested live.

**What the map never reads or shows:** the item/fg grid, that is buried items,
fossils, money rocks and ground items. A test checks that every read falls
inside the spec's map tables, the player chain or the villager house heights.

**Decoded images:** all images are decoded in memory at runtime. The PNG
written with `--map` contains art decoded from the game, so it must be written
outside the project: `dashboard.py` refuses (exit code 2) any `--map` path at
or under the project root, after resolving `..`, symlinks and letter case, and
`TownMapReader.write_png` raises `ProjectPathError` for such a path. Never
commit the image and never put it in the APK.

**Reads**

1. **First refresh in town** (three round trips):
   * The static tables, once per game hash: about 6 KB of pointer tables,
     palettes, `data_combi_table`, the house position list and the icon.
   * The town layout: about 1.4 KB.
   * The acre images in use: 512 B each, cached per texture.
2. **Each refresh after that** is one UDP request. It reads only the player
   chain (`gamePT` → GAME → actor), the position, the facing and `field_type`.
3. **Layout refresh.** The layout is read again every 10 s, and whenever the
   town name, the town id or any villager's home changes. If its content is
   unchanged, the base image is not redrawn.
4. **Unstable values.** Every read uses the same read-twice rule as
   `acmap.py`. If the position keeps changing between the two copies, the
   last good marker stays (`"stale": true`).
5. **Acre types:**
   * If the block-type table pointer is not the expected one, the acre types
     come from the save (`combi_table` + `data_combi_table`).
   * If the table and the save disagree (the field is being rebuilt), the
     previous layout is kept.

**Status** (`--json` → `"map"`):

```
{"status": "ok",            // ok | stale (not in gameplay) | no_layout | unavailable | disconnected
 "layout": {"source": "table", "acre_types": [30 ints], "invalid_acres": [], "bridge": [x, z, exists]},
 "buildings": [{"key": "shop", "label": "Shop", "acre": "A-5"}, ...],
 "villager_houses": [{"slot": 0, "npc_id": "0xE095", "acre": "E-4", "spot": 0, "tier": 1}, ...],
 "player": {"visible": true, "acre": "B-3", "block": [3, 2], "tile": [15, 6], "map": [64.6, 30.9],
            "world": [2520.4, 280.0, 1540.3], "facing": 16384, "facing_dir": "E"},
 "highlight": {"acre": "B-3", "block": [3, 2], "source": "player"},   // or "exit_door", or null
 "image": {"width": 499, "height": 593, "scale": 4, "path": "..."}, "warnings": [], "round_trips": 1}
```

When the marker is hidden, `player.visible` is false and `player.reason` says
why:
* the player is indoors (the reason names the scene);
* the player is outside the map, for example on the island;
* an actor check failed;
* the read was unstable.

The text dashboard's `Map` line shows the same information.

The map can also be used as a library:

```python
reader, tmap = acmap.ACReader(source, spec), townmap.TownMapReader(source, spec, scale=4)
status = tmap.update(reader.poll())   # the GameState gates the map (validity rules 1-5)
canvas = tmap.render()                # townmap.Canvas: .width, .height, .rgba (RGBA bytes), .png()
```

## Tests

```
python -m unittest discover -s tools/pc_client/tests -v
```

Run this from the project root. The tests start mock servers on ephemeral
loopback ports and do not need Dolphin. Most tests use
`tests/fixtures/spec_fixture.json`. `tests/test_real_spec.py` runs the same
checks against `spec/ac_memory_map.json` and is skipped if that file is missing.

`tests/test_gctex.py` checks each texture format against hand-built tiles with
known pixels.

`tests/test_townmap.py` tests the town map against the synthetic map from
`mock_server.py`. It covers:
* the placement rules, the reader, the renderer and the dashboard output;
* the fallbacks and the layout caching;
* the one-request steady state;
* the check that every read stays inside the spec's map data.

Both test files use synthetic texture data only.

### Cross-check with the Android decoder

```
python tools/pc_client/crosscheck_export.py [--save-image DIR]
```

Builds every synthetic scenario (plus a `town_edge` image with charmap,
trimming, blank-name and out-of-range cases) from the real spec, decodes each
with `acmap.py`, and writes sparse images, the canonical summaries and a copy of
the spec to `android/app/src/test/resources/crosscheck/`. The Android
`CrossCheckTest` decodes the same bytes with `Decoder.kt` and asserts identical
summaries; it also fails if its spec copy differs from `spec/ac_memory_map.json`.

It also writes `map_cases.json`: four synthetic town-map images (the default
map; moved player with a non-cardinal facing, other house tiers and a
translucent icon; a skipped border acre type; no valid palettes) and what
`townmap.py` made of each: per-acre texel hashes, building acres, villager
houses with their centres, the tinted icon per tier, the player marker (block,
map position, facing, direction), the highlighted acre, and row hashes of the
static composition (acres + house icons, no labels or overlay) at 8 px per map
unit. `CrossCheckTest.townMapLikeThePythonClient` runs the same bytes through
`TownMapReader` and `TownMapRenderer.compose` and expects the same values and
pixels. `tests/test_crosscheck_map.py` fails while `map_cases.json` is stale.

Rerun the export after any spec, decoder or town-map change.
