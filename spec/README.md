# Animal Crossing (GAFE01 rev 0) memory map spec

`ac_memory_map.json` is the only source of addresses and offsets for both companion clients (PC test client and the Android app). It describes live game state in emulated GameCube RAM as exposed by dolphin-lnk's EmuLink server.

Scope: the unmodified USA retail game, `GAFE01`, revision 0. Mods such as "Animal Crossing Deluxe" can move things and are not covered.

| File | What it is |
|---|---|
| `ac_memory_map.json` | The spec. Generated; don't hand-edit. |
| `tools/build_memory_map.py` | Builds and validates the JSON. The hand-checked field tables live at the top of this file. The charmap, item-name ranges and scene enum are derived from the decomp. |
| `tools/layout_check.py` | Recomputes struct member offsets independently with Python `ctypes` (100 checks). |

```
python spec/tools/layout_check.py                 # struct offsets (exit 0 = pass)
python spec/tools/build_memory_map.py             # regenerate the JSON (validates first)
python spec/tools/build_memory_map.py --check     # confirm the JSON matches the generator
python spec/tools/build_memory_map.py --decomp <path-to-ac-decomp>
```

The JSON file is pure ASCII. Non-ASCII charmap glyphs are `\uXXXX` escapes, and astral-plane glyphs are surrogate pairs. It parses correctly with any JSON library, even when the file is opened with a non-UTF-8 default encoding, such as `open()` on Windows.

---

## 1. Reading the spec (for client authors)

- **Hex strings.** Every address, offset, stride and item id is a string matching `^0x[0-9A-F]+$`. Plain JSON numbers are used only for counts, lengths, `revision`, `shift`, `entry_len` and enum keys.
- **Endianness.** All game data is big-endian. EmuLink request headers are little-endian; that's the protocol, not this spec.
- **Addressing.**
  - Global: `bases[g.base] + g.offset`. Every global uses `base: "common_data"`.
  - Player i: `common_data + players.offset + i*players.stride + field.offset`.
  - Villager j: `common_data + villagers.offset + j*villagers.stride + field.offset`.
  - Villager j, memory k: `<villager j> + villagers.memories.offset + k*villagers.memories.stride + field.offset`.
- **Types.**
  - `u8 s8 u16 s16 u32 s32 f32` are as named.
  - `ptr` is a BE32 GameCube pointer. It's valid only in 0x80000000-0x817FFFFF.
  - `str` is `len` bytes decoded through `charmap`: look up every byte and concatenate the entries. Never stop at 0x00, because 0x00-0x1F are real glyphs. An entry can be empty (control codes) or two characters long (0xDC, 0xDD), so don't assume one character per byte. Then trim trailing `' '` characters; 0x20, 0xD2 and 0xD3 all decode to `' '`.
  - `u16[]` is `count` consecutive BE16 values.
- **Extension keys** (all optional; clients may ignore them):
  - `item_id: true`: the value is an item id; name it with `item_names`.
  - `slot_enum`: an enum that applies per 2-bit slot (`item_conditions`), not to the raw u32.
  - `villagers.memories`: a nested array, used for friendship.
  - `extras.npc_name_cache`: see §8. `extras.climate`: see §3.4.
  - `charmap_notes`.
  - `item_names.unknown_ranges`, `item_names.runtime_check` and `item_names.lookup`.
  - `validity.not_in_town_scenes`.
  - `map`: the town map (acre images, building indicators, villager houses, player marker). See §13.
- **Status values.** Every `status` in the file, extension blocks included, is one of the four values below; the build rejects anything else. Treat `status` as information, not as a gate. A client that meets an unknown value in a later schema should keep the field rather than reject the file.

| status | meaning |
|---|---|
| `verified_live` | Read on the user's retail game with Dolphin Memory Engine (DME). |
| `ar_code` | Matches a working Action Replay code. |
| `computed` | Derived by layout arithmetic inside a struct whose position is anchored by `verified_live` or `ar_code` members, with anchors on both sides or a short delta from one. Re-checked by `layout_check.py`. |
| `header_derived` | From decomp struct definitions, or from `symbols.txt` plus the REL section bases (item-name tables, the npc name cache, the climate flag). The arithmetic is checked by `layout_check.py` or by the build, but no live or AR anchor brackets it. |

The decomp builds byte-matching binaries. A struct layout that the matched code uses is therefore effectively confirmed. The main risk for `header_derived` fields is an arithmetic mistake, and `layout_check.py` guards against that. The `/* 0x... */` comments in the headers were **not** trusted blindly. Two were wrong:

- `Private_c.museum_record` is at 0x18, not 0x17.
- `common_data.unused_mail_26522` is at 0x26524.

Neither changes any field in the spec.

## 2. Anchors

| Anchor | Value | Status |
|---|---|---|
| `"GAFE01"` @0x80000000, revision 0 @0x80000007 | | verified_live |
| `common_data` (CD) = 0x81266400 | `symbols.txt` common_data = .bss+0xBC40; .bss base 0x8125A7C0 | verified_live |
| CD+0x9120 town name "Cheevo"; CD+0x20 player 1 name "Doc" | | verified_live |
| CD+0x20+0x8C wallet = 77122 | | verified_live |
| CD+0x26120 rtc_time: hour @+2 = 13, year @+6 = 2026 | | verified_live |
| Private stride 0x2440; bank +0x122C; loan +0x90; gender +0x14; face +0x15 | | ar_code |
| animals CD+0x17438, stride 0x988; npc_id +0; cloth +0x8E4 | | ar_code |
| kabu CD+0x20480 | | ar_code |
| `gamePT` 0x812F31B8 = .bss+0x989F8; `play_main` 0x8062B370 = .text+0x2BB0C8 | foresta `symbols.txt` | header_derived (the build asserts both) |
| sizeof(`common_data_t`) = 0x2DC00, align 32 | foresta `symbols.txt` | header_derived (the build asserts it; anchors §3.4's end-of-struct chain) |

The foresta.rel section bases are .text 0x803702A8, .rodata 0x80641260, .data 0x8064D500 and .bss 0x8125A7C0 (`docs/decomp_basics.md:37-40`).

- .bss is confirmed by `common_data`.
- .data was checked for consistency from `foresta/splits.txt`. .rodata ends at 0x8064D4EC, and 32-byte alignment gives 0x8064D500.
- The build also asserts that `common_data`, `gamePT` and `play_main` agree with symbols.txt plus these bases.

The build asserts that the spec reproduces every `verified_live` absolute address.

## 3. Derivations

Sizes: `lbRTC_time_c` (OSRTCTime) = 8 bytes, align 2. Its layout is sec, min, hour, day, weekday, month (u8 each), then year u16 at +6. `PersonalID_c` = 0x14. `AnmPersonalID_c` = 0xE. `Mail_c` = 0x12A. `mQst_base_c` = 0xC, align 4. All are checked by `layout_check.py`.

### 3.1 `Save_t` head (CD+0x0000)

| Member | Offset | Size | Notes |
|---|---|---|---|
| save_check (`mFRm_chk_t`: int version, u32 code, u16 land_id, time @0xA, u16 checksum @0x12) | 0x00 | 0x14 | |
| **scene_no** (int) | **0x14** | 4 | Current scene. `m_play.c:205-206` copies `next_scene_no` into it. |
| **now_npc_max** (u8) | **0x18** | 1 | |
| remove_animal_idx u8, copy_protect u16, pad_1C[4] | 0x19 / 0x1A / 0x1C | | |
| private_data[4] (`Private_c`, 32-byte aligned) | 0x20 | 4×0x2440 | Name verified at 0x20. 0x20 + 4×0x2440 = 0x9120 = land_info (verified). |
| land_info (`mLd_land_info_c`: name[8], s8 exists @8, **u16 id @0xA**) | 0x9120 | 0xC | town_id = CD+0x912A (computed) |
| animals[15] (`Animal_c`) | 0x17438 | 15×0x988 | AR |
| kabu_price_schedule (`Kabu_price_c`) | 0x20480 | 0x18 | AR; see §4 |
| **fruit** (u16 item id) | **0x20688** | 2 | header_derived; see below |

**Town fruit (0x20688).**

- Working backward from police_box at 0x20ED0: a known cheat code matches that offset, but it is not in the anchor list.
  - `PostOffice_c` is 0x83C bytes, because 8 + 7×0x12A rounds to 0x830, plus a 4-byte union and an 8-byte time. So post_office starts at 0x20694.
  - `all_grow_renew_time` (8 bytes) is at 0x2068C.
  - num_statues is at 0x2068B and house_arrangement at 0x2068A.
  - That puts fruit at 0x20688.
- The only other possible layout would end `event_save_common` on a non-4-aligned address, which is impossible because that struct contains ints.
- Forward, the header says `event_save_common` is at 0x20554 (size 0x134), which agrees.

### 3.2 `Private_c` (record base = CD+0x20 + i×0x2440)

| Member | Offset | Size | Status of fields used |
|---|---|---|---|
| player_ID.player_name[8] | 0x0000 | 8 | **name** verified_live |
| player_ID.land_name[8] | 0x0008 | 8 | **town_name** computed |
| player_ID.player_id u16 / land_id u16 | 0x0010 / 0x0012 | 2+2 | computed |
| gender s8 / face s8 | 0x0014 / 0x0015 | 1+1 | ar_code |
| reset_count u8 | 0x0016 | 1 | |
| museum_record (`mMsm_record_c`, align 2: u8 bits + `mMsm_remail_info_c` 0x4C) | **0x0018** (comment says 0x17) | 0x4E | ends 0x66 |
| inventory (anonymous struct, align 4): pockets u16[15] @0x68, lotto u8 @0x86 / u8 @0x87, item_conditions u32 @0x88, **wallet u32 @0x8C**, **loan u32 @0x90** | 0x0068 | 0x2C | wallet verified_live → pockets / item_conditions computed; loan AR |
| deliveries `mQst_delivery_c`[15] | 0x0094 | 15×0x28 | |
| errands `mQst_errand_c`[5] | 0x02EC | 5×0x58 | |
| **equipment** u16 | 0x04A4 | 2 | computed |
| saved_mail_header `Mail_hs_c` | 0x04A6 | 0x3A | |
| mail `Mail_c`[10] | 0x04E0 | 10×0x12A | |
| backgound_texture u16 | 0x1084 | 2 | |
| **exists** u8 / hint_count u8 | 0x1086 / 0x1087 | 1+1 | computed; see below |
| cloth {u16 idx @0x1088, **u16 item @0x108A**} | 0x1088 | 4 | **shirt** computed |
| stored_anm_id `AnmPersonalID_c` | 0x108C | 0xE | |
| destiny {received_time @0x109A (day @0x109D, month @0x109F, year @0x10A0), **type u8 @0x10A2**} | 0x109A | 0xA | fortune_* computed |
| birthday {u16 year, **u8 month @0x10A6**, **u8 day @0x10A7**} | 0x10A4 | 4 | computed |
| catalog_orders[5] / unk[24] / aircheck u32[2] | 0x10A8 / 0x10BC / 0x10D4 | 0x14 / 0x18 / 8 | |
| remail `Anmremail_c` (0x15 → 0x16) / reset_code u32 | 0x10DC / 0x10F4 | | |
| animal_memory (0xA) / complete flags u8 / celebrated_birthday_year u16 | 0x10F8 / 0x1102 / 0x1104 | | |
| furniture / wall / carpet / paper / music collected bits | 0x1108 / 0x11B4 / 0x11C0 / 0x11CC / 0x11D4 | 0xAC / 0xC / 0xC / 8 / 8 | |
| maps `mPr_map_info_c`[8] | 0x11DC | 0x50 | |
| **bank_account** u32 | **0x122C** | 4 | ar_code (anchors the whole chain above) |
| my_org[8] (`mNW_original_design_c`, **32-byte aligned**) … unused_2412[46] | 0x1240 … 0x2412 | | ends 0x2440 = stride (AR) |

The chain from wallet (verified, 0x8C) through loan (AR, 0x90) to bank (AR, 0x122C) is reproduced exactly by `layout_check.py`. Every Private field between those anchors is therefore marked `computed`.

**Pocket conditions.** `cond(slot) = (item_conditions >> (2*slot)) & 3`. This is the `mPr_GET_ITEM_COND` macro: 0 = normal, 1 = wrapped present, 2 = quest item.

**`exists` is not a slot-in-use flag.** It means "this player's character is currently in this town file":

- The travel save clears it when a resident leaves town (`m_card.c:6279, 6343, 6668`: `priv->exists = FALSE` when `player_no` < 4). The decoy-flag path clears it too (`m_card.c:4813`).
- Loading a resident whose `exists` is 0 sets it back to 1, with the pockets-and-wallet penalty (`m_start_data_init.c:426-449`).
- The game's own slot-in-use test is `mPr_CheckPrivate` (`m_private.c:274-281`): `(player_ID.land_id & 0xFF00) == 0x3000`. A new player gets `land_id = land_info.id` (`m_private.c:235`), and `mLd_MakeLandId` always returns 0x30xx.

Rule 4 (active player must have `exists` == 1) is still right, because a loaded resident always has it set. A client that *lists* residents should use rule 10 instead: occupied iff the `land_id` test passes, with `exists` == 0 meaning "out travelling".

### 3.3 `Animal_c` (record base = CD+0x17438 + j×0x988; alignment 8 because of a u64 in `Anmmem_c`)

| Member | Offset | Size | Notes |
|---|---|---|---|
| id.npc_id u16 | 0x000 | 2 | AR. Valid iff `(id & 0xF000) == 0xE000`. |
| id.land_id u16 / id.land_name[8] / id.name_id u8 / **id.looks u8** | 0x002 / 0x004 / 0x00C / **0x00D** | | computed. looks = personality. |
| memories `Anmmem_c`[7] | 0x010 | 7×0x138 | see below |
| home_info {type u8, **block_x @0x899**, **block_z @0x89A**, **ut_x @0x89B**, **ut_z @0x89C**} | 0x898 | 5 | computed |
| **catchphrase**[10] | 0x89D | 10 | computed (save data) |
| contest_quest `mQst_contest_c` (align 4) | 0x8A8 | 0x28 | |
| parent_name[8] / anmuni (8) / previous_land_id u16 / mood u8 / mood_time u8 | 0x8D0 / 0x8D8 / 0x8E0 / 0x8E2 / 0x8E3 | | |
| **cloth** u16 | 0x8E4 | 2 | AR |
| remove_info u16 / **is_home** u8 / **moved_in** u8 / **removing** u8 | 0x8E6 / 0x8E8 / 0x8E9 / 0x8EA | | computed |
| cloth_original_id, umbrella_id, unk, present_cloth u16, animal_relations[15], hp_mail[4] (0x1C each), unused[24] | 0x8EB … 0x970 | | ends 0x988 = stride (AR) |

`Anmmem_c` is 0x138 bytes:

- memory_player_id (`PersonalID_c`) at 0x00. Its player_id is at +0x10 and land_id at +0x12.
- last_speak_time at 0x14.
- `memuni_u` union at 0x1C. It is 0xC bytes with align 4.
- saved_town_tune u64 at 0x28, align 8.
- **friendship s8 at 0x30**.
- letter_info u8 at 0x31.
- `Anmplmail_c` letter at 0x32. It is really 0x102 bytes, although its comment says 0x104. Either way the struct rounds up to 0x138.

**Friendship with the current player.** The game's `mNpc_GetAnimalMemoryIdx` compares the 20-byte `PersonalID_c`. Clients do the same: find the memory whose first 0x14 bytes equal the current Private's first 0x14 bytes.

**Home acre.** Town acres are block_x 1..5 and block_z 1..6 (`FG_BLOCK_X_NUM`, `FG_BLOCK_Z_NUM`, `FGIDX_2_BLOCK_X/Z` in `m_field_make.h`). Each acre has 16×16 units. The in-game map label is probably `chr('A' + block_z - 1) + block_x`, but this hasn't been checked.

### 3.4 `common_data_t` tail

- `Save` is a union padded to `ALIGN_NEXT(sizeof(Save_t)=0x242A0, 0x2000)`, which is 0x26000.
- After it come game_started @0x26000, field_type @0x26001, field_draw_type @0x26002 and **player_no @0x26003** (header_derived; 4 = foreigner).

`Time_c` is at 0x26110. Its rtc_time (+0x10 = 0x26120) is verified live:

| Time_c member | Offset |
|---|---|
| **season** u32 | +0x00 → CD+0x26110 (`time_season`, computed) |
| term_idx u32 / bgitem_profile s16 / bgitem_bank s16 / now_sec int | +0x04 / +0x08 / +0x0A / +0x0C |
| **rtc_time** (sec, min, hour, day, weekday, month, u16 year) | +0x10 → CD+0x26120..0x26127 |
| rad_min s16 / rad_hour s16 / time_signal, under_sec, disp, rtc_crashed (u8) | +0x18 / +0x1A / +0x1C..0x1F |
| rtc_enabled / add_sec / add_idx (int) | +0x20 / +0x24 / +0x28; sizeof = 0x2C |

What follows `Time_c` (the header comments agree, and `layout_check.py` recomputes it):

1. **now_private** (ptr) at 0x26110 + 0x2C = **0x2613C** (computed).
2. now_home at 0x26140. Four u8 at 0x26144, pad at 0x26148, transition at 0x2614C, two s16 at 0x26150, and a pad up to 0x26164.
3. npclist[16] at 0x26164, × 0x38. island_npclist[1] at 0x264E4.
4. Two u16 at 0x2651C and four u8 at 0x26520.
5. `Mail_c` at **0x26524**. The comment says 0x26522; the real end is 0x2664E.
6. Two u16, then `Mail_nm_c` (0x16) at 0x26652, npc_chg_cloth at 0x26668 and a pad at 0x2666A.
7. **weather s16 at 0x2666C** and **weather_intensity s16 at 0x2666E** (header_derived). This is the live weather that `ac_weather.c:83-84` writes. Don't confuse it with the saved byte `Save.weather` at CD+0x20F19.

**`weather` is never 4 (falling leaves).** `aWeather_weatherinfo_CommonSet` (`ac_weather.c:77-85`) stores any type >= `mEnv_WEATHER_LEAVES` as 0. K.K. Slider's falling-leaves effect (`ac_npc_totakeke_think.c_inc:61-67`) therefore reads as Clear. The enum keeps label 4 only because it mirrors `enum weather` in full.

**Island weather.** On the island the weather actor ignores `weather` and uses `island_weather` / `island_weather_intensity` (`ac_weather.c:101-103, 399-402`). `mEnv_DecideWeather_NormalGameStart` sets them at game start to Clear/None or Rain/Heavy (`m_kankyo_weather.c:525-532`). The offsets come from the end of `common_data_t`. `layout_check.py` models the tail from `auto_nwrite_set` to the end, which is 0xC0 bytes:

- That tail follows `Island_agb_c`, which contains a 32-byte-aligned `mNW_original_design_c`, so it starts on a 32-byte boundary.
- `common_data_t` is 0x2DC00 bytes with align 32 (`symbols.txt`).
- So the tail can only start at 0x2DC00 - 0xC0 = 0x2DB40. That matches the header comment, and the comments for `event_flags` and `pad` agree too.
- `mCD_persistent_data_c` = `mLd_land_info_c` + 4 × `PersonalID_c` = 0x5C, so **island_weather is at 0x2DBA2** and **island_weather_intensity at 0x2DBA4** (header_derived).

To tell whether the player is on the island, `extras.climate` points at `l_mFI_climate` (0x81295BC8 = .bss+0x3B408, s32, header_derived). The island weather applies iff it is 1 (`mFI_CLIMATE_ISLAND`; the boat demo sets it, `ac_boat_demo_move.c_inc:94-101`). `mFI_GetClimate` treats 0 and 2-5 as the town.

## 4. Stalk Market (`Kabu_price_c` @ CD+0x20480, `m_kabu_manager.h/.c`)

| Member | Offset | Meaning |
|---|---|---|
| daily_price u16[7] | +0x00 | Indexed by lbRTC weekday (0 = Sunday). **[0] is the Sunday buy price**, always 70..129 Bells (`Kabu_decide_price_sunday`). **[1..6] are the Monday..Saturday sell prices.** |
| trade_market u16 | +0x0E | This week's trend. 0 = A (B-style random walk, plus one day Mon-Fri set to 8 × the Sunday price). 1 = B (random). 2 = C (falling, each day × [0.80, 0.95)). |
| update_time `lbRTC_time_c` | +0x10 | Sunday 00:00 of the week the schedule belongs to. `Kabu_set_schedule_day` takes the generation date and steps back to Sunday, so after a gap of more than a week it is the most recent Sunday, not the day the schedule was generated. day @+0x13, month @+0x15, year @+0x16. |

There is **one price per day**, with no morning/afternoon split in this game. `Kabu_get_price()` returns `daily_price[rtc_time.weekday]`, so today's price is `kabu_prices[rtc_weekday]`.

**When the values are final.** `Kabu_manager` runs at game start (`m_start_data_init.c:556`) and on every date change (`m_time.c:434, 510`):

- If today's date equals `update_time`, it calls `Kabu_decide_price_schedule_without_sunday`. That re-rolls the trend and the Mon-Sat prices and keeps the Sunday price.
- Otherwise, once more than a week has passed, it re-rolls everything (`Kabu_decide_price_schedule`).

So **on the update Sunday the trend and the Mon-Sat prices are provisional.** Loading the game again that Sunday replaces them. Treat them as final only when the rtc date differs from the `kabu_update_*` date, which in practice means from Monday on. The Sunday price `[0]` is fixed once set. From Monday, the rest of the week's prices sit in RAM. A UI that shows future days reveals hidden information, so whether to show them is a design decision.

The trend and update fields are `computed`: they are in the same 0x18-byte struct as the AR-anchored prices.

## 5. Enums

- **weather**: `enum weather` in `m_kankyo.h`: 0 clear, 1 rain, 2 snow, 3 cherry blossoms, 4 falling leaves. Global `weather` never holds 4 (§3.4). **weather_intensity**: 0 none, 1 light, 2 normal, 3 heavy. `island_weather` / `island_weather_intensity` use the same two enums.
- **weekday**: `enum WEEKDAYS` in `lb_rtc.h`. 0 = Sunday … 6 = Saturday. **month**: 1 = January … 12.
- **season**: `m_time.h`: 0 spring, 1 summer, 2 autumn, 3 winter.
- **gender**: `mPr_SEX_*`: 0 male, 1 female.
- **looks** (personality): `m_npc_personal_id.h`: 0 normal, 1 peppy, 2 lazy, 3 jock, 4 cranky, 5 snooty, 6 unset.
- **item_condition**: `mPr_ITEM_COND_*`. **kabu_trend**: `Kabu_TRADE_MARKET_TYPE_*`. **destiny** (fortune): `mPr_DESTINY_*`.
- **scene_no**: `enum scene_table` in `m_scene_table.h`, parsed in order at build time. Examples: 7 = outdoors, 9 = store, 33 = title demo, 35-39 = museum. Debug scenes are labelled "Test/debug scene …" and never occur in retail.
  - `validity.not_in_town_scenes` = [15, 16, 19, 27, 28, 33, 34, 49]: the start demos, player selects, title demo and the player-select save screen. Those scenes also run `play_main`.

The enum labels were written for this spec. They are not copied from game text.

## 6. Charmap

The charmap starts from `CHAR_MAP` in `tools/msg_tool.py` (CC0), all 256 entries, loaded with `ast`. A-Z, a-z, 0-9 and space are ASCII-compatible, and the build asserts this.

Where `msg_tool.py` disagrees with the decomp's own glyph names in `include/m_font.h` (also CC0), the m_font.h name wins. `msg_tool.py`'s entries there are placeholders: the Latin-1 character equal to the byte value, or U+FFFD. The build asserts every m_font.h name it relies on, so a renumbered header fails the build.

**Blanked control codes** (`charmap_notes.blanked_control_entries`) → `""`:

- 0x7F `CHAR_CONTROL_CODE` and 0x80 `CHAR_MESSAGE_TAG`. Both are message control codes, and `mFont_char_save_data_check` (`m_font.c:135`) rejects both in save data.
- 0xCD `CHAR_NEW_LINE` (`"\n"`).

**Corrected from m_font.h** (`charmap_notes.m_font_overrides`):

| Byte | m_font.h name | msg_tool.py had | Spec now |
|---|---|---|---|
| 0x8D | `CHAR_oe` | ⁰ | ø. `mFont_small_to_capital` (`m_font.c:189`) pairs it with 0x18 Ø. |
| 0x99 / 0x9A | `CHAR_FEMININE_ORDINAL` / `CHAR_MASCULINE_ORDINAL` | ḏ / ṉ | ª / º |
| 0xD2 / 0xD3 | `CHAR_SPACE_2` (short) / `CHAR_SPACE_3` (wide) | Ò / Ó | `' '` / `' '`. The name editor uses 0xD3 as its blank key (`m_editor_ovl.c:416`). Both are trimmed like 0x20. |
| 0xD5 / 0xD6 | `CHAR_LEFT_QUOTATION` / `CHAR_RIGHT_QUOTATION` | Õ / Ö | “ / ” |
| 0xD7 / 0xD8 | `CHAR_LEFT_APOSTROPHE` / `CHAR_RIGHT_APOSTROPHE` | × / Ø | ‘ / ’ |
| 0xD9 / 0xDA | `CHAR_ETHEL` / `CHAR_LOWER_ETHEL` | Ù / Ú | Œ / œ |
| 0xDB / 0xDC / 0xDD | `CHAR_ORDINAL_e` / `_er` / `_re` | Û / Ü / Ỳ | ᵉ / ᵉʳ / ʳᵉ. These are two-character entries. |
| 0xDE | `CHAR_BACKSLASH` | ꟓ | `\` |

**Kept but uncertain** (`charmap_notes.uncertain_entries`): 0xC7 and 0xCA. m_font.h names them `CHAR_SYMBOL_SQUIRREL` and `CHAR_SYMBOL_OCTOPUS`, but its own comments say "might be dog?" and "could also be bird...?". `msg_tool.py` has dog and bird, so neither source is reliable and the tool's glyphs stay.

**Still placeholders**: 0xDF-0xFF. m_font.h has no names for them (`CHAR_223`…`CHAR_255`), so they keep `msg_tool.py`'s Latin-1 stand-ins.

0x85 (`CHAR_INTERPUNCT`, shown as •) and 0x90 (`CHAR_HYPHEN`, shown as ー) are close enough in meaning to leave alone.

## 7. Item names (`item_names`, status `header_derived`)

**What it emulates.** `mIN_copy_name_str` (`src/game/m_item_name.c:476`) does the following:

1. Converts furniture-form ids with `mRmTp_FtrItemNo2Item1ItemNo` (`m_room_type.c:1953`).
2. Returns "Unknown" if `mNT_check_unknown` says so (`m_name_table.c:403`). That happens for an item1 index ≥ the category count, or a furniture index ≥ `FTR_NUM` (1266).
3. Otherwise copies 16 bytes from one table:
   - item1 ids `0x2xxx`: `itemName_table[(id>>8)&0xF] + (id&0xFF)*16`.
   - FTR0 ids `0x1xxx`: `ftrName_table + ((id/4)&0x3FF)*16`.
   - FTR1 ids `0x3xxx`: `ftrName2_table + ((id/4)&0x3FF)*16`.
   - Signboards 0x0900-0x0920: `itemName_etc[30]`.
   - Id 0 gives 16 spaces.
   - Any other id: no name.

**How the ranges were built.** `build_memory_map.py` runs an exact Python copy of these three functions for all 65536 ids. It compresses the result into 33 non-overlapping ranges of the form `index = (id - id_min) >> shift`, then **re-checks every id** against the emulation.

**Inputs.**

- Furniture ids come from the `X()` enums in `m_name_table.h` (FTR0 from 0x1000, FTR1 from 0x3000).
- Furniture indices come from `m_ftr_def.h`. All 1266 names were cross-checked between the two lists.
- Every table's symbol size equals its `*_NUM` × 16. For example, `ftrName2_table` is 0xF20 bytes = 242 entries, and 1024 + 242 = `FTR_NUM`.

Table addresses = foresta `.data` base 0x8064D500 + the symbol offset. Two examples: `itemName_paper` = .data+0x272160 = 0x808BF660, and `ftrName_table` = .data+0x276410 = 0x808C3910.

| id_min | id_max | table_addr | shift | source |
|---|---|---|---|---|
| 0x0900 | 0x0920 | 0x808C20B0 | 6 | signboards → itemName_etc[30] (shift 6 keeps index 0) |
| 0x1000 | 0x17AB | 0x808C3910 | 2 | ftrName_table+0x0 (FTR0, 4 facings per item) |
| 0x17AC | 0x1BA7 | 0x808C0EE0 | 2 | mannequins FMANEKIN000-254 → itemName_cloth |
| 0x1BA8 | 0x1BC7 | 0x808C67B0 | 2 | ftrName_table+0x2EA0 (my-design mannequins) |
| 0x1BC8 | 0x1C67 | 0x808C35E0 | 2 | insect models → itemName_insect |
| 0x1C68 | 0x1D07 | 0x808C0C60 | 2 | fish models → itemName_fish |
| 0x1D08 | 0x1D87 | 0x808C06E0 | 2 | umbrellas FUMBRELLA00-31 → itemName_tool+0x40 (tool 4..35) |
| 0x1D88 | 0x1FEF | 0x808C6F30 | 2 | ftrName_table+0x3620 |
| 0x1FF0 | 0x1FFF | 0x808C0AE0 | 2 | balloons 0-3 → itemName_tool+0x440 (tool 68..71) |
| 0x2000 | 0x20FF | 0x808BF660 | 0 | item1 paper |
| 0x2100 | 0x2103 | 0x808C0660 | 0 | item1 money |
| 0x2200 | 0x225B | 0x808C06A0 | 0 | item1 tool |
| 0x2300 | 0x2327 | 0x808C0C60 | 0 | item1 fish |
| 0x2400 | 0x24FE | 0x808C0EE0 | 0 | item1 cloth (0x24FF = Unknown) |
| 0x2500 | 0x2530 | 0x808C1ED0 | 0 | item1 etc |
| 0x2600 | 0x2642 | 0x808C21E0 | 0 | item1 carpet |
| 0x2700 | 0x2742 | 0x808C2610 | 0 | item1 wall |
| 0x2800 | 0x2807 | 0x808C2A40 | 0 | item1 fruit |
| 0x2900 | 0x290A | 0x808C2AC0 | 0 | item1 plant |
| 0x2A00 | 0x2A36 | 0x808C2B70 | 0 | item1 minidisk (music) |
| 0x2B00 | 0x2B0F | 0x808C2EE0 | 0 | item1 diary (`itemName_dummy`) |
| 0x2C00 | 0x2C5F | 0x808C2FE0 | 0 | item1 ticket |
| 0x2D00 | 0x2D2C | 0x808C35E0 | 0 | item1 insect |
| 0x2E00 | 0x2E01 | 0x808C38B0 | 0 | item1 hukubukuro (grab bag) |
| 0x2F00 | 0x2F03 | 0x808C38D0 | 0 | item1 kabu (turnips) |
| 0x3000 | 0x300F | 0x808C0B20 | 2 | balloons 4-7 → itemName_tool+0x480 (tool 72..75) |
| 0x3010 | 0x30FB | 0x808C7950 | 2 | ftrName2_table+0x40 |
| 0x30FC | 0x313B | 0x808C2EE0 | 2 | diary models → itemName_dummy |
| 0x313C | 0x314B | 0x808C0A30 | 2 | golden tools → itemName_tool+0x390 |
| 0x314C | 0x316B | 0x808C0BE0 | 2 | fans → itemName_tool+0x540 |
| 0x316C | 0x318B | 0x808C0B60 | 2 | pinwheels → itemName_tool+0x4C0 |
| 0x318C | 0x319B | 0x808C06A0 | 2 | tools (net/axe/shovel/rod) → itemName_tool+0x0 |
| 0x319C | 0x33C7 | 0x808C7F80 | 2 | ftrName2_table+0x670 (last furniture index 1265) |

**Other lookups.**

- `empty_ids` = `["0x0000"]` (`EMPTY_NO`). Show an em dash.
- `unknown_ranges` lists the ids the game itself names "Unknown": item1 gaps and 0x33C8-0x3FFF.
- Any other id has no name, for example scenery 0x0xxx or NPC ids. Show its hex value.
- Names are read from RAM at runtime. The spec contains no game text.

**Runtime self-check.** `item_names.runtime_check` gives `itemName_table$398` at **0x80655CB4**: 16 BE32 pointers that the code uses.

- If they equal `expected`, the .data base, and with it every `table_addr` in this table, is confirmed on the running game.
- Clients should do this once per boot and switch item names off if it fails.

## 8. Villager names (not in MEM1)

`mNpc_LoadNpcNameString` (`m_npc.c:3765`) copies a villager's name **from ARAM** (`RESOURCE_NPC_NAME_STR_TABLE`) into a temporary buffer each time it's needed. EmuLink exposes only MEM1, so there is no name table to read. The spec must not bundle names either.

`extras.npc_name_cache` points to `l_npc_name_cache` at 0x8129A3E8 (.bss+0x3FC28; `mNpc_NameCache_c` = {u16 npc_id, u8 name[8], u8 npc_type}, size 0xC). The game stores the last looked-up name there. A client can harvest `npc_id → name` pairs while the player talks to villagers, and remember them per town (by town_id). Until it knows a name, it should show the personality plus `#(npc_id & 0xFF)`.

## 9. Validity rules

These are the rules in the JSON, with the reasons behind them:

1. Check the game id and revision.
2. `BE32(gamePT)` is in MEM1 and `GAME.exec` (+0x04, `game.h:19`) == `play_main`.
3. Three cases:
   - **(a) Resident.** `now_private` is inside the 4-record players array and aligned to the stride. This gives the player index.
   - **(b) Visiting / foreign.** `player_no` == 4 (`mPr_FOREIGNER`), `now_private` is a MEM1 pointer, and `(town_id & 0xFF00) == 0x3000` (a valid `mLd_MakeLandId` value). Only globals are shown.
   - **(c) Anything else** shows "Not in town".

   Why the visiting case needs all three conditions: the foreign-save load (`m_card.c:7200-7228`) runs `bzero(&common_data, sizeof(common_data_t))` before restoring the save. Only afterwards does it set `now_private = &l_mcd_foreigner_file.file.priv` and `player_no = 4`. In between, `now_private` is NULL, `player_no` is 0 and the town is blank. The old rule ("anything outside the array is visiting") would have shown that state as a valid visited town. Every real foreign path sets `player_no` = 4 together with a MEM1 pointer: `m_card.c:7227-7228`, and `m_private.c:810-811` → `g_foreigner_private`.

   Optional: in case (b), `now_private` points at a full `Private_c` with the same layout, so a client may read the visitor's player fields relative to it. That's header_derived and untested.
4. In case 3(a), the player's `exists` == 1. A loaded resident always has it set (§3.2).
5. **Added:** `scene_no` is not in `not_in_town_scenes`. The title demo runs `play_main` with a random demo player (`mPr_RandomSetPlayerData_title_demo`), so rules 2-4 alone would show garbage on the title screen.
6. A villager slot is valid if its id is in the 0xE000 range.
7. Friendship matching, as in §3.3.
8. Tear handling: read twice and do range checks.
9. String decoding rules (§1, §6).
10. **Added:** for listing residents, a slot is occupied iff `(land_id & 0xFF00) == 0x3000` (`mPr_CheckPrivate`). If `exists` == 0 on an occupied slot, that resident is out travelling (§3.2).

Don't require 'GAFE' at CD+4: in a brand-new town it is -1 until the save is reloaded.

## 10. One-time DME checklist (absolute addresses, player 1, villager slot 0)

| What | Address | Expect |
|---|---|---|
| itemName pointer table | 0x80655CB4 (16 × BE32) | 0x808BF660, 0x808C0660, 0x808C06A0, … (see JSON) |
| scene_no | 0x81266414 (s32) | 7 outdoors, 9 in the store |
| player_no | 0x8128C403 (u8) | 0 for player 1 |
| now_private | 0x8128C53C (ptr) | 0x81266420 for player 1 |
| weather / intensity | 0x8128CA6C / 0x8128CA6E (s16) | matches the sky |
| pockets | 0x81266488 (15 × u16) | item ids; 0x0000 = empty |
| exists | 0x812674A6 (u8) | 1 |
| shirt | 0x812674AA (u16) | 0x24xx |
| kabu prices / trend | 0x81286880 (7 × u16) / 0x8128688E | [0] = Sunday price 70..129 |
| town fruit | 0x81286A88 (u16) | 0x2800-0x2804 |
| villager 0 npc_id / looks / home block_x | 0x8127D838 / 0x8127D845 / 0x8127E0D1 | 0xE0xx / 0-5 / 1-5 |
| villager 0 memory 0 friendship | 0x8127D878 (s8) | |
| npc name cache | 0x8129A3E8 | last villager talked to |
| island_weather / intensity | 0x81293FA2 / 0x81293FA4 (s16) | 0/0 or 1/3 (set at game start) |
| climate | 0x81295BC8 (s32) | 0 in town, 1 on the island |

## 11. Open issues

1. **No live check of `header_derived` fields yet**: scene_no, player_no, weather, island_weather, town_fruit, the item-name table addresses, the npc name cache and the climate flag. The runtime pointer-table check (§7) and the DME checklist (§10) cover them.
2. **The spec hasn't been exercised against a running dolphin-lnk.** No build was available here, and this task didn't touch the network. All testing was offline: layout recomputation, the emulation re-check, JSON and hex validation, and reproduction of the verified anchors.
3. **Villager names aren't readable from MEM1.** §8 gives the partial workaround.
4. **SCENE_BUGGY's** purpose is unknown. (The acre label convention, with the row letter A-F taken from block_z 1-6 and the column 1-5 from block_x, is now settled by the map screen code; see §13.1.) **Map open items:** the extra-bridge rule and `indoor_acre` haven't been live-tested, and neither has `field_type` indoors (§13.2, §13.6).
5. **Some charmap entries are unverified.** 0xDF-0xFF are still placeholders, and 0xC7/0xCA are uncertain (§6). The m_font.h corrections come from glyph *names*. Nobody has compared them with the font texture.
6. **Item ids outside the ranges** (scenery, NPC and structure ids) have no names by design. Furniture names cover ids in the house-placed form; pocket ids use the same tables.
7. **Reads are unsynchronised**, so fields can tear. Clients must apply rule 8.
8. **The clients predate these rule changes.** Both `tools/pc_client/acmap.py` and the Android `Decoder.kt` still treat every out-of-array `now_private` as "visiting" (old rule 3). The PC client also lists residents by `exists == 1`, not by rule 10. Their owners need to update both.

## 12. Review resolutions (second review)

An independent reviewer re-derived every offset and found no critical or major defects. Six minor findings were each re-checked against the decomp before acting:

| # | Finding | Resolution |
|---|---|---|
| 1 | Rule 3 treats any out-of-array `now_private` (NULL, torn) as "visiting". | **Fixed.** Confirmed in `m_card.c:7200-7228`: bzero of common_data first, `now_private` / `player_no` = 4 last. Visiting now requires `player_no` == 4, a MEM1 pointer and a valid `town_id` (0x30xx, `mLd_MakeLandId`). Anything else → "Not in town". |
| 2 | `exists` described as "slot in use". | **Fixed.** Confirmed: the travel save clears it (`m_card.c:6279/6343/6668`, decoy path 4813), and `mPr_CheckPrivate` tests `land_id` only. The note is reworded. A `land_id` note and rule 10 (resident listing) are added. Rule 4 is unchanged; it's still correct for the active player. |
| 3 | Mon-Sat turnip prices are re-rolled if the game is loaded again on the update Sunday. | **Fixed.** Confirmed in `Kabu_manager` (same-date branch) and its callers (`m_start_data_init.c:556`, `m_time.c:434/510`). The `kabu_prices` and `kabu_trade_market` notes and §4 now say the values are final only when the date ≠ the update date. Also corrected: `update_time` is the Sunday that starts the week (`Kabu_set_schedule_day`), not necessarily the day the schedule was generated. |
| 4 | Charmap placeholders contradict `m_font.h` glyph names. | **Fixed, with one part declined.** Confirmed: 0xD2/0xD3 are spaces (the editor's blank key is 0xD3) and 0x8D is ø (`mFont_small_to_capital`). 0x8D, 0xD2-0xD3 and 0xD5-0xDE now follow m_font.h. Two additions from the same check: 0x99/0x9A (ª/º, same kind of conflict) and 0x80 (`CHAR_MESSAGE_TAG`, a control code per `mFont_char_save_data_check`, now blanked). **Declined:** 0xC7/0xCA squirrel/octopus. m_font.h's own comments doubt those names and suggest exactly the dog/bird glyphs msg_tool.py uses, so they're kept and flagged as uncertain. |
| 5 | Status `computed_unverified` is outside the documented set. | **Fixed.** These blocks now use `header_derived`, broadened to cover symbol-table + section-base addresses: no live/AR anchor, arithmetic checked. That's the same evidence level as `gamePT`. The validator now rejects any undocumented status anywhere in the file. The README also tells clients to treat unknown statuses as information, not errors. |
| 6 | `weather` never holds 4; island weather is separate. | **Fixed.** Confirmed in `ac_weather.c:77-85` (LEAVES → 0) and the island paths (`ac_weather.c:101-103, 399-402`). This is noted on `weather` and in §3.4/§5. Added `island_weather` (CD+0x2DBA2) and `island_weather_intensity` (CD+0x2DBA4) as header_derived. They're verified by a new end-of-struct chain in `layout_check.py` that is anchored on sizeof(common_data_t) = 0x2DC00 (§3.4). Also added `extras.climate` (`l_mFI_climate`, 0x81295BC8) so a client can tell when the island weather applies. |

## 13. Town map (`map`)

Source: `src/game/m_map_ovl.c` (the map screen, `mMP_*`), `src/data/model/kan_tizu.c`, `src/data/model/kan_hyouji2.c`.

- Every table address is `symbols.txt` + REL section base.
- The build asserts each address against the live-read value in `MAP_VERIFIED_LIVE`, and `layout_check.py` recomputes the struct offsets.
- Live check (2026-10-01): dolphin-lnk, `GAFE01` hash `ec3f974f…`, town "Cheevo", scene 7, read-only.

**Rules for clients.**
- Every image is read from emulated RAM and decoded at runtime. Never ship or cache decoded images in the project or the APK.
- The map never needs the item/fg grid (`Save.fg`, buried flags). Don't read it: no buried spots, fossils, money rocks or ground items.
- The player marker and the acre highlight are original shapes, never game art.

### 13.1 Grid (`map.grid`, verified_live)

The map shows block x 1..5 and block z 1..6 of the 7×10 block grid (`m_field_make.h:17-23`; loops at `m_map_ovl.c:653-656`).

- Columns are labelled 1-5 and rows A-F. The game draws those labels as window-frame textures, so clients draw their own.
- One acre = 22 map units (`mMP_BLOCK_SIZE_F`) = 22 visible texels.
- The dark-green separator is baked into each acre texture as its top row and left column.

### 13.2 Acre types (`map.acre_types`, verified_live)

- **Table.** `g_block_type_p` (0x80653E1C) → `l_block_type` (0x81295A3C), `u8[70]`, index `bz*7+bx`.
- **Same values from the save.** `type = data_combi_table[combi_table[i] >> 2].type`.
  - `combi_table` is at CD+0x173A8, `u16[70]`: combination_type is the high 14 bits, height the low 2 bits.
  - `data_combi_table` is at 0x8080DD80: 368 entries of 6 bytes.
  - `mFM_combo_info_c.type` is at **+4**. The header comment says 0x05; `layout_check.py` confirms +4.
  - Live, both sources gave the same 70 values.
- **Selection** (`mMP_make_max_no_table`, :643).
  - Scan z, then x. Skip border types (`skip_types` = 0-10, 61, 62), then pad to 30 entries with FLAT (39).
  - The skip list is derived from the enum names in the builder: everything ≤ `BORDER_CLIFF_CORNER_TOP_RIGHT`, plus the two `*_TRANSITION` and the two `*_TUNNEL` types.
  - One research note listed 63/64 as skipped. That is wrong: 63/64 are BEACH / BEACH_RIVER, which Cheevo's row F uses.
- **Bridge** (header_derived).
  - `PlusBridge_c` is at CD+0x213F0, an offset fixed by `_tmp6[0x213F0 - 0x213E7]` in `Save_t`.
  - `exists` is the first 1-bit field, so it is bit 7 of +2 (MWCC big-endian bitfields).
  - If the bridge exists in that acre and `pluss_bridge[type]` (0x806CDBDC) ≠ 0xFF, that value replaces the type.
  - Cheevo has no bridge (all zero), so this path is not live-tested.

### 13.3 Acre textures (`map.acre_texture`, verified_live)

- **Tables.** `l_map_texture` (0x806CD9C0) holds 108 pointers, indexed by type.
- **Palette.** Selector `l_map_pal` (0x806CDB70) → `l_kan_tizu_pal` (0x806CDCA0). Its live values are `kan_tizu1_pal` 0x806CDC60 and `kan_tizu2_pal` 0x806CDC80.
- **Format.**
  - The `kan_tizu_model` display list loads CI/4b 32×32 with `isDolphin`, which emu64 maps to GX C4 (`emu64.c` `fmtxtbl`).
  - The palette is a 16-entry TLUT, loaded as GX RGB5A3.
  - Index 0 is 0x0000, which is transparent.
- **Valid pointers.**
  - The 69 `kan_tizu_*_TA_tex_txt` images are contiguous: 0x80F0B020-0x80F13A20, 0x200 bytes each. The builder asserts this.
  - A pointer is valid iff it is in that range at a multiple of 0x200 from the start. Live, every used entry was valid.
- **Crop.**
  - `kan_tizu_v` (0x80AFC800) is a ±11 quad with s,t 0..704, i.e. 22.0 texels in 10.5 fixed point. Read live: `(-11,-11)→(0,704)`, `(11,11)→(704,0)`.
  - Crop texels [0,22) on both axes, with t=0 at the top.
  - Types 13, 63, 82 and 100 carry one extra column at x=22 as filter slack.

### 13.4 Building indicators (`map.buildings`, verified_live)

The shop, post office, station, dump, wishing well, police box, museum, tailor, dock and the player-house cluster are **drawn inside their acre's texture**. The game draws no separate icon for them on the grid; the label-bubble icons belong to the side panel. So their `marker` is `{kind: "ram_texture", texture: "acre_texture"}`.

- **Finding the acres.** `l_block_kind` holds `mRF_BLOCKKIND_*` bits (`m_random_field_h.h`). It is reached via `g_block_kind_p` 0x80653E20 → 0x81295924, `u32[70]`.
- **Order.** Indicators are listed in the game's label priority (`mMP_set_field_data`, :802).
- **Live check.** Every kind bit and block type matched in Cheevo: dump A1, post A2, station A3, shop A5, houses B3, well C1, police D5, museum E1, tailor F2, dock F5.
- **Fallback.** `fallback_marker` is an original coloured square with a short label. Use it only when the acre texture fails validation.

### 13.5 Villager houses (`map.villager_houses`, verified_live)

From `mMP_set_house_data` (:696-783) and `mMP_set_house_dl` (:990-1041).

- **Icon.** `kan_win_yane_tex` (0x80AFBBE0), IA/8b 16×16, which emu64 maps to GX IA4 (1 byte per pixel).
  - Size: `kan_hyouji2_v[8..11]` is a ±5 quad with s,t 0..512, i.e. 16 texels over 10 units (read live).
  - Colour: `env + (prim-env)·I`, alpha = A.
  - PRIM/ENV are the `G_SETPRIMCOLOR`/`G_SETENVCOLOR` words of `kan_win_npc2T_{1,2,3}_model` (bytes +4 and +12). Live values: (90,90,225) blue, (145,70,205) purple, (170,115,20) brown; ENV 225 for all three.
- **Tier.** `npclist[i].house_position.y` (CD+0x26164 + i·0x38 + 8) → `mCoBG_Height2GetLayer` → `mMP_check_layer`, with `step3 = (combi_table[0] & 3) == 2`.
- **Slot.**
  - Take fg = `data_combi_table[combi_table[home]>>2].fg_id`.
  - Search `mMP_house_pos_list` (0x80B075D8, 12-byte entries). **The terminator 0x03B8 is entry 201** (read live); `count` 202 includes it.
  - In the first entry that matches fg, use the slot with `ut_x == home_ut_x && ut_z == home_ut_z-1`, else slot 0.
  - Icon centre = acre top-left + ({5,13,17}[idx%3], {4,11,18}[idx/3]).
- **Live check.** All 8 Cheevo villagers matched a slot exactly. Tiers were 0 at y=280, 1 at y=160 and 2 at y=40.

### 13.6 Player (`map.player`)

- **Chain** (verified_live).
  - `gamePT` → GAME, and GAME+0x04 == `play_main`.
  - Then `play.actor_info` (+0x1DA8, `m_play.h:92`) `.list[ACTOR_PART_PLAYER=3]`: `num_actors` at +0x1DC4, actor at +0x1DC8.
  - Actor checks: id (+0x00, s16) == 0 and part (+0x02) == 3.
  - Live: GAME 0x8148FDE0, actor 0x814B3FE0, count 1.
- **Fields.** `world.position` is f32×3 at +0x28 and `shape_info.rotation.y` is at +0xDE. `layout_check.py` re-derives both from the `ACTOR` struct.
- **Transform.**
  - Block = `trunc(x/640)`, `trunc(z/640)` (`mFI_Wpos2BlockNum`).
  - Map units = `(x/640 - 1)·22`, and the same for z (y down).
  - Facing 0 = south, 0x4000 = east.
- **Live check.** (2520.4, 280.0, 1540.3), facing 0x4000: B-3 at its east edge, facing east, next to the B-4 river. `npclist.house_position` == ((bx·16+ut_x)·40, (bz·16+ut_z)·40) for every villager, which confirms 640 world units per acre.
- **Show when** all of these hold:
  - all chain and actor checks pass;
  - scene is 7;
  - `field_type` (CD+0x26001) == 0 (header_derived; reads 0 outdoors);
  - 1 ≤ bx ≤ 5 and 1 ≤ bz ≤ 6. The island is scene 7 at bz 8, so it is hidden.
- **Marker.** An original red dot with an arrow (`dot_with_arrow`) plus a magenta-ish acre box (`acre_highlight`). The game's own map shows only the acre (a pawn icon and the selection cursor); the exact dot is the companion's addition.
- **`indoor_acre`** (optional, header_derived).
  - `structure_exit_door_data.exit_position` is at CD+0x2854C. The +8 offset inside `Door_data_c` is checked by `layout_check.py`, but the CD+0x28544 base is only a header comment.
  - This is what the game marks when the map is opened indoors.
  - Not live-tested: it read (0,0,0) outdoors. A client that uses it should show only the acre box, and only when the block is in range.

### 13.7 Verification render

A spec-driven script followed only what `map` describes and composited the map in the session scratchpad, not the project. The result matches the in-game map style:

- rails along row A, with the dump, post office, station and shop;
- the 4 player houses in B-3;
- rivers with a pond and bridges; gray cliffs with green ramps;
- beach and ocean in row F, with the dock;
- villager houses coloured by tier;
- the magenta box and red dot for the player.

No decoded image is stored in the repository. Unit tests that need textures must use synthetic data.
