"""Builds spec/ac_memory_map.json from hand-checked field tables plus data derived
from the (read-only) ac-decomp checkout.

Derived from the decomp at build time:
  * charmap      - tools/msg_tool.py CHAR_MAP (CC0), control entries blanked and placeholder
                   entries corrected from the glyph names in include/m_font.h (CC0)
  * item_names   - an exact Python emulation of mIN_copy_name_str()
                   (src/game/m_item_name.c) + mRmTp_FtrItemNo2Item1ItemNo()
                   (src/game/m_room_type.c) + mNT_check_unknown()
                   (src/game/m_name_table.c), run for all 65536 item ids and
                   compressed into id ranges; the ranges are then re-checked
                   against the emulation for every id.
  * scene_no enum - enum scene_table in include/m_scene_table.h
  * table addresses - config/GAFE01_00/foresta/symbols.txt + REL section bases
  * map           - town map section: block-type enum (include/m_field_make.h), block-kind bits
                   (include/m_random_field_h.h), acre texture range and table addresses from
                   symbols.txt, each asserted against MAP_VERIFIED_LIVE (README §13)

Usage:
  python spec/tools/build_memory_map.py [--decomp PATH] [--check]
  --check : rebuild in memory and compare with the existing JSON (no write)
"""
import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_DIR = os.path.dirname(HERE)
OUT = os.path.join(SPEC_DIR, "ac_memory_map.json")
DEFAULT_DECOMP = (r"C:/Users/itsdr/AppData/Local/Temp/claude/C--Users-itsdr-Documents-Projects-"
                  r"ac-dual-screen/6ac4b8c1-7832-4197-ac7a-0c6b556fb8b5/scratchpad/ac-decomp")

# foresta.rel section bases in RAM (docs/decomp_basics.md:37-40). .text and .bss are
# confirmed by common_data (verified live) / play_main / gamePT; .data follows .rodata
# contiguously (rodata end 0x8064D4EC -> 32-aligned 0x8064D500).
REL_BASES = {".text": 0x803702A8, ".rodata": 0x80641260, ".data": 0x8064D500, ".bss": 0x8125A7C0}


def h(n, width=0):
    return "0x%0*X" % (width, n)


# --------------------------------------------------------------------------------------
# Hand-checked tables (see README.md for every derivation)
# --------------------------------------------------------------------------------------
def F(key, offset, typ, status, note="", **extra):
    d = {"key": key, "offset": h(offset), "type": typ}
    d.update(extra)
    d["status"] = status
    if note:
        d["note"] = note
    return d


def G(key, offset, typ, status, note="", **extra):
    d = F(key, offset, typ, status, note, **extra)
    return {"key": d.pop("key"), "base": "common_data", **d}


GLOBALS = [
    G("scene_no", 0x14, "s32", "header_derived", "Save.scene_no = current scene id (m_play.c:205-206)", enum="scene_no"),
    G("now_npc_max", 0x18, "u8", "header_derived", "number of villagers living in town"),
    G("town_name", 0x9120, "str", "verified_live", "land_info.name", len=8),
    G("town_id", 0x912A, "u16", "computed", "land_info.id (land_info verified at 0x9120; id at +0xA)"),
    G("kabu_prices", 0x20480, "u16[]", "ar_code",
      "Kabu_price_c.daily_price[7], indexed by weekday: [0]=Sunday buy price, "
      "[1..6]=Mon..Sat sell price. One price per day (no AM/PM). Today's price = kabu_prices[rtc_weekday]. "
      "Mon-Sat prices and kabu_trade_market are generated on Sunday but are re-rolled if the game is "
      "loaded again on the same Sunday (rtc date == kabu_update_* date, Kabu_manager); treat them as "
      "final only when the rtc date != the update date",
      count=7),
    G("kabu_trade_market", 0x2048E, "u16", "computed",
      "this week's trend type (provisional on the update Sunday, see kabu_prices)", enum="kabu_trend"),
    G("kabu_update_day", 0x20493, "u8", "computed",
      "Kabu_price_c.update_time.day: the Sunday that starts the schedule's week "
      "(Kabu_set_schedule_day rounds the generation date back to Sunday)"),
    G("kabu_update_month", 0x20495, "u8", "computed", "Kabu_price_c.update_time.month (1-12)"),
    G("kabu_update_year", 0x20496, "u16", "computed", "Kabu_price_c.update_time.year"),
    G("town_fruit", 0x20688, "u16", "header_derived", "native fruit item id (0x2800-0x2807); name via item_names", item_id=True),
    G("house_arrangement", 0x2068A, "u8", "header_derived",
      "2 bits per player: homes[] index of player i's house = (house_arrangement >> (2*i)) & 3 "
      "(ARRANGE_GET, m_house.c:9)"),
    G("player_no", 0x26003, "u8", "header_derived",
      "0-3 = player slot, 4 = mPr_FOREIGNER (visiting / visitor); see validity rule 3"),
    G("time_season", 0x26110, "u32", "computed", "common_data.time.season", enum="season"),
    G("rtc_sec", 0x26120, "u8", "computed", "common_data.time.rtc_time (OSRTCTime; base verified live)"),
    G("rtc_min", 0x26121, "u8", "computed"),
    G("rtc_hour", 0x26122, "u8", "verified_live"),
    G("rtc_day", 0x26123, "u8", "computed", "day of month 1-31"),
    G("rtc_weekday", 0x26124, "u8", "computed", "0 = Sunday", enum="weekday"),
    G("rtc_month", 0x26125, "u8", "computed", "1-12", enum="month"),
    G("rtc_year", 0x26126, "u16", "verified_live"),
    G("now_private", 0x2613C, "ptr", "computed",
      "Private_c* of the active player = time + sizeof(Time_c) (0x26110 + 0x2C). Points into the "
      "players array for a resident; at a separate Private_c when player_no == 4; NULL/garbage while "
      "the game rebuilds common_data (see validity rule 3)"),
    G("weather", 0x2666C, "s16", "header_derived",
      "current town weather type. Never 4: aWeather_weatherinfo_CommonSet stores type >= Falling leaves "
      "as 0, so K.K.'s falling-leaves effect reads as Clear. Not used on the island (see island_weather)",
      enum="weather"),
    G("weather_intensity", 0x2666E, "s16", "header_derived", enum="weather_intensity"),
    G("island_weather", 0x2DBA2, "s16", "header_derived",
      "weather the weather actor uses while the player is on the island (ac_weather.c:101-103, 399-402); "
      "set at game start to Clear or Rain (m_kankyo_weather.c:525-532). Use it when "
      "extras.climate == 1, or in SCENE_COTTAGE_MY / SCENE_COTTAGE_NPC",
      enum="weather"),
    G("island_weather_intensity", 0x2DBA4, "s16", "header_derived", enum="weather_intensity"),
]

PLAYER_FIELDS = [
    F("name", 0x0, "str", "verified_live", "player_ID.player_name", len=8),
    F("town_name", 0x8, "str", "computed", "player_ID.land_name (home town of this player)", len=8),
    F("player_id", 0x10, "u16", "computed", "player_ID.player_id"),
    F("land_id", 0x12, "u16", "computed",
      "player_ID.land_id; slot occupied iff (land_id & 0xFF00) == 0x3000 (mPr_CheckPrivate)"),
    F("gender", 0x14, "s8", "ar_code", enum="gender"),
    F("face", 0x15, "s8", "ar_code", "face type 0-7"),
    F("pockets", 0x68, "u16[]", "computed", "inventory.pockets: 15 item ids (0x0000 = empty)",
      count=15, item_id=True),
    F("item_conditions", 0x88, "u32", "computed",
      "2 bits per pocket slot: cond(slot) = (item_conditions >> (2*slot)) & 3", slot_enum="item_condition"),
    F("wallet", 0x8C, "u32", "verified_live", "Bells carried (max 99999)"),
    F("loan", 0x90, "u32", "ar_code", "remaining house loan in Bells"),
    F("equipment", 0x4A4, "u16", "computed", "item id of the held tool/umbrella (0 = none)", item_id=True),
    F("exists", 0x1086, "u8", "computed",
      "1 = this player's character is currently in this town file; 0 while the character is out "
      "travelling, or the slot is unused. Not a slot-in-use flag: see validity rule 10"),
    F("shirt", 0x108A, "u16", "computed", "cloth.item: item id of the worn shirt", item_id=True),
    F("fortune_day", 0x109D, "u8", "computed", "destiny.received_time.day"),
    F("fortune_month", 0x109F, "u8", "computed", "destiny.received_time.month"),
    F("fortune_year", 0x10A0, "u16", "computed", "destiny.received_time.year"),
    F("fortune_type", 0x10A2, "u8", "computed",
      "destiny.type; only meaningful if fortune date == today", enum="destiny"),
    F("birthday_month", 0x10A6, "u8", "computed", "unset = 0xFF"),
    F("birthday_day", 0x10A7, "u8", "computed", "unset = 0xFF"),
    F("bank", 0x122C, "u32", "ar_code", "bank_account (post office savings)"),
]

VILLAGER_FIELDS = [
    F("npc_id", 0x0, "u16", "ar_code", "valid villager iff (npc_id & 0xF000) == 0xE000; name index = npc_id & 0xFF"),
    F("land_id", 0x2, "u16", "computed", "id.land_id"),
    F("town_name", 0x4, "str", "computed", "id.land_name", len=8),
    F("name_id", 0xC, "u8", "computed", "id.name_id (= npc_id & 0xFF)"),
    F("looks", 0xD, "u8", "computed", "personality", enum="looks"),
    F("home_block_x", 0x899, "u8", "computed", "home_info.block_x: acre column 1-5"),
    F("home_block_z", 0x89A, "u8", "computed", "home_info.block_z: acre row 1-6"),
    F("home_ut_x", 0x89B, "u8", "computed", "home_info.ut_x: unit 0-15 inside the acre"),
    F("home_ut_z", 0x89C, "u8", "computed", "home_info.ut_z: unit 0-15 inside the acre"),
    F("catchphrase", 0x89D, "str", "computed", "save data (player-editable)", len=10),
    F("cloth", 0x8E4, "u16", "ar_code", "item id of the worn shirt", item_id=True),
    F("is_home", 0x8E8, "u8", "computed", "1 = inside their house"),
    F("moved_in", 0x8E9, "u8", "computed", "1 = moved in after town creation"),
    F("removing", 0x8EA, "u8", "computed", "1 = planning to move away"),
]

MEMORY_FIELDS = [
    F("player_name", 0x0, "str", "computed", "memory_player_id.player_name", len=8),
    F("player_town", 0x8, "str", "computed", "memory_player_id.land_name", len=8),
    F("player_id", 0x10, "u16", "computed", "memory_player_id.player_id"),
    F("land_id", 0x12, "u16", "computed", "memory_player_id.land_id"),
    F("last_speak_day", 0x17, "u8", "computed",
      "last_speak_time.day: date this villager last finished a conversation with that player "
      "(set when the talk ends, ac_quest_manager.c:1296, m_npc.c:645); cleared memories hold the "
      "mTM_rtcTime_clear_code date"),
    F("last_speak_month", 0x19, "u8", "computed", "last_speak_time.month (1-12)"),
    F("last_speak_year", 0x1A, "u16", "computed", "last_speak_time.year"),
    F("friendship", 0x30, "s8", "computed", "friendship toward that player"),
]

HOME_FIELDS = [
    F("owner_name", 0x0, "str", "header_derived", "ownerID.player_name", len=8),
    F("owner_town", 0x8, "str", "header_derived", "ownerID.land_name", len=8),
    F("owner_player_id", 0x10, "u16", "header_derived", "ownerID.player_id"),
    F("owner_land_id", 0x12, "u16", "header_derived", "ownerID.land_id"),
    F("has_basement", 0x24, "u8", "header_derived",
      "flags.has_basement (MSB-first bitfield: house_updated, has_saved, hra_member, has_basement, ...)",
      mask="0x10", shift=4),
    F("house_size", 0x2A, "u8", "header_derived", "size_info.size (bits 7-5)", mask="0xE0", shift=5,
      enum="house_size"),
    F("house_next_size", 0x2A, "u8", "header_derived",
      "size_info.next_size (bits 4-2): the size after an ordered upgrade; == house_size when none is pending",
      mask="0x1C", shift=2, enum="house_size"),
    F("statue_rank", 0x2A, "u8", "header_derived",
      "size_info.statue_rank (bits 1-0): 0 gold, 1 silver, 2 bronze, 3 jade", mask="0x03", shift=0,
      enum="statue_rank"),
]

ENUMS = {
    "weather": {"0": "Clear", "1": "Rain", "2": "Snow", "3": "Cherry blossoms", "4": "Falling leaves"},
    "weather_intensity": {"0": "None", "1": "Light", "2": "Normal", "3": "Heavy"},
    "weekday": {"0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday",
                "5": "Friday", "6": "Saturday"},
    "month": {str(i): m for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August", "September",
         "October", "November", "December"], start=1)},
    "gender": {"0": "Male", "1": "Female"},
    "looks": {"0": "Normal", "1": "Peppy", "2": "Lazy", "3": "Jock", "4": "Cranky", "5": "Snooty",
              "6": "Unset"},
    "item_condition": {"0": "Normal", "1": "Wrapped present", "2": "Quest item"},
    "kabu_trend": {"0": "Spike (type A)", "1": "Random (type B)", "2": "Falling (type C)"},
    "season": {"0": "Spring", "1": "Summer", "2": "Autumn", "3": "Winter"},
    "house_size": {"0": "Small (initial)", "1": "Medium", "2": "Large", "3": "Upstairs", "4": "Statue"},
    "statue_rank": {"0": "Gold", "1": "Silver", "2": "Bronze", "3": "Jade"},
    "destiny": {"0": "Normal", "1": "Popular", "2": "Unpopular", "3": "Bad luck", "4": "Money luck",
                "5": "Goods luck"},
}

# Labels for enum scene_table (index order parsed from include/m_scene_table.h).
SCENE_LABELS = {
    "SCENE_NPC_HOUSE": "Villager house", "SCENE_FG": "Outdoors (town)",
    "SCENE_SHOP0": "Shop: store (stage 1)", "SCENE_BROKER_SHOP": "Traveling merchant tent",
    "SCENE_POST_OFFICE": "Post office", "SCENE_START_DEMO": "Intro sequence (start demo)",
    "SCENE_START_DEMO2": "Intro sequence (start demo 2)", "SCENE_POLICE_BOX": "Police station",
    "SCENE_BUGGY": "BUGGY (purpose unverified)", "SCENE_PLAYERSELECT": "Player select",
    "SCENE_MY_ROOM_S": "Player house (small)", "SCENE_MY_ROOM_M": "Player house (medium)",
    "SCENE_MY_ROOM_L": "Player house (large)", "SCENE_CONVENI": "Shop: convenience store (stage 2)",
    "SCENE_SUPER": "Shop: supermarket (stage 3)", "SCENE_DEPART": "Shop: department store 1F (stage 4)",
    "SCENE_PLAYERSELECT_2": "Player select (2)", "SCENE_PLAYERSELECT_3": "Player select (3)",
    "SCENE_DEPART_2": "Shop: department store 2F", "SCENE_EVENT_ANNOUNCEMENT": "Event announcement",
    "SCENE_KAMAKURA": "Snow hut (kamakura)", "SCENE_TITLE_DEMO": "Title screen demo",
    "SCENE_PLAYERSELECT_SAVE": "Player select (save)", "SCENE_MUSEUM_ENTRANCE": "Museum entrance",
    "SCENE_MUSEUM_ROOM_PAINTING": "Museum: paintings", "SCENE_MUSEUM_ROOM_FOSSIL": "Museum: fossils",
    "SCENE_MUSEUM_ROOM_INSECT": "Museum: insects", "SCENE_MUSEUM_ROOM_FISH": "Museum: fish",
    "SCENE_MY_ROOM_LL1": "Player house (extra large, ground floor)",
    "SCENE_MY_ROOM_LL2": "Player house (upstairs)", "SCENE_MY_ROOM_BASEMENT_S": "Player basement (small)",
    "SCENE_MY_ROOM_BASEMENT_M": "Player basement (medium)", "SCENE_MY_ROOM_BASEMENT_L": "Player basement (large)",
    "SCENE_MY_ROOM_BASEMENT_LL1": "Player basement (extra large)", "SCENE_NEEDLEWORK": "Tailor shop",
    "SCENE_COTTAGE_MY": "Island cottage (player)", "SCENE_COTTAGE_NPC": "Island cottage (islander)",
    "SCENE_START_DEMO3": "Intro sequence (start demo 3)", "SCENE_LIGHTHOUSE": "Lighthouse",
    "SCENE_TENT": "Tent",
}
NOT_IN_TOWN_SCENES = ["SCENE_START_DEMO", "SCENE_START_DEMO2", "SCENE_PLAYERSELECT",
                      "SCENE_PLAYERSELECT_2", "SCENE_PLAYERSELECT_3", "SCENE_TITLE_DEMO",
                      "SCENE_PLAYERSELECT_SAVE", "SCENE_START_DEMO3"]

VALIDITY_RULES = [
    "1. Game check: 6 bytes at game.id_addr == game.id ('GAFE01') and the byte at game.revision_addr == game.revision (0). Otherwise show 'Unsupported game'.",
    "2. Gameplay check: p = BE32(bases.gamePT); require 0x80000000 <= p <= 0x817FFFFF and BE32(p + 4) == bases.play_main. Otherwise show 'Not in town'.",
    "3. Player check: np = now_private; P0 = common_data + players.offset. (a) If P0 <= np < P0 + players.count * players.stride and (np - P0) % players.stride == 0, current player index = (np - P0) / players.stride. (b) Else, if player_no == 4 (mPr_FOREIGNER) and 0x80000000 <= np <= 0x817FFFFF and (town_id & 0xFF00) == 0x3000, the player is visiting/foreign: show globals only (common_data.save holds the visited town). (c) Anything else (NULL or torn pointer, e.g. while a foreign-save load zeroes and rebuilds common_data) -> show 'Not in town'.",
    "4. For case 3(a), that player's 'exists' field must be 1; otherwise show 'Not in town'.",
    "5. Scene check: scene_no must not be in validity.not_in_town_scenes (title demo, player select, intro sequences). These scenes also run play_main, and the title demo fills now_private with a random demo player. Otherwise show 'Not in town'.",
    "6. Villager slot i is valid iff (npc_id & 0xF000) == 0xE000.",
    "7. Friendship with the current player: in villagers.memories, find the entry whose 20 bytes at +0x00 (player_name, player_town, player_id, land_id) equal the 20 bytes at the current Private_c +0x00 (same PersonalID_c layout; this is what mNpc_GetAnimalMemoryIdx compares). No match = has not talked to this player.",
    "8. Reads are not synchronised with the emulated CPU: read a block twice and accept it when both reads agree (or retry). Reject impossible values (rtc_month outside 1-12, rtc_hour > 23, wallet > 99999, a kabu price > 2000).",
    "9. Strings: decode every byte through charmap (0x00-0x1F are glyphs, so do not stop at 0x00) and concatenate the entries (an entry may be empty or longer than one character); then trim trailing ' ' characters (0x20, 0xD2 and 0xD3 all decode to ' ').",
    "10. Listing residents (optional): player slot i is occupied iff (players[i].land_id & 0xFF00) == 0x3000 (mPr_CheckPrivate). An occupied slot with exists == 0 is a resident whose character is out travelling, not an empty slot.",
]


# --------------------------------------------------------------------------------------
# Decomp-derived parts
# --------------------------------------------------------------------------------------
def read(decomp, rel):
    with open(os.path.join(decomp, rel), encoding="utf-8") as f:
        return f.read()


# Entries where msg_tool.py's CHAR_MAP holds a placeholder or a guess that contradicts the
# decomp's own glyph names in include/m_font.h (also CC0). byte -> (m_font.h name, glyph).
# The build asserts each m_font.h name, so a renumbered header fails loudly.
CHARMAP_OVERRIDES = {
    0x80: ("CHAR_MESSAGE_TAG", ""),         # control code: mFont_char_save_data_check (m_font.c:135)
    0x8D: ("CHAR_oe", "ø"),            # o-stroke; lower case of 0x18 per mFont_small_to_capital
    0x99: ("CHAR_FEMININE_ORDINAL", "ª"),
    0x9A: ("CHAR_MASCULINE_ORDINAL", "º"),
    0xD2: ("CHAR_SPACE_2", " "),            # short space
    0xD3: ("CHAR_SPACE_3", " "),            # wide space; the name editor's blank key (m_editor_ovl.c:416)
    0xD5: ("CHAR_LEFT_QUOTATION", "“"),
    0xD6: ("CHAR_RIGHT_QUOTATION", "”"),
    0xD7: ("CHAR_LEFT_APOSTROPHE", "‘"),
    0xD8: ("CHAR_RIGHT_APOSTROPHE", "’"),
    0xD9: ("CHAR_ETHEL", "Œ"),
    0xDA: ("CHAR_LOWER_ETHEL", "œ"),
    0xDB: ("CHAR_ORDINAL_e", "ᵉ"),
    0xDC: ("CHAR_ORDINAL_er", "ᵉʳ"),
    0xDD: ("CHAR_ORDINAL_re", "ʳᵉ"),
    0xDE: ("CHAR_BACKSLASH", "\\"),
}
# m_font.h itself is unsure about these two; msg_tool.py's glyphs are the alternatives its
# comments suggest, so they are kept and only flagged.
CHARMAP_UNCERTAIN = {0xC7: "CHAR_SYMBOL_SQUIRREL", 0xCA: "CHAR_SYMBOL_OCTOPUS"}


def load_font_names(decomp):
    names = {}
    for m in re.finditer(r"^#define\s+(CHAR_\w+)\s+(\d+)\b", read(decomp, "include/m_font.h"), re.M):
        names.setdefault(int(m.group(2)), m.group(1))
    return names


def load_charmap(decomp):
    tree = ast.parse(read(decomp, "tools/msg_tool.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "CHAR_MAP" for t in node.targets):
            cm = ast.literal_eval(node.value)
            break
    else:
        raise SystemExit("CHAR_MAP not found")
    assert len(cm) == 256 and all(len(c) == 1 for c in cm), "unexpected CHAR_MAP shape"
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ":
        assert cm[ord(c)] == c, "charmap is not ASCII-compatible at %r" % c
    cm = list(cm)
    blanked = {}
    for i, c in enumerate(cm):
        if c in ("\x7f", "\n"):          # message control-code prefix / newline
            blanked[h(i, 2)] = "U+%04X" % ord(c)
            cm[i] = ""
    font = load_font_names(decomp)
    overrides = {}
    for i, (name, glyph) in sorted(CHARMAP_OVERRIDES.items()):
        assert font.get(i) == name, "m_font.h: expected %s at 0x%02X, found %s" % (name, i, font.get(i))
        if glyph == "":
            blanked[h(i, 2)] = "U+%04X" % ord(cm[i])
        else:
            overrides[h(i, 2)] = name
        cm[i] = glyph
    for i, name in CHARMAP_UNCERTAIN.items():
        assert font.get(i) == name, "m_font.h: expected %s at 0x%02X" % (name, i)
    # bytes m_font.h has no glyph name for (it numbers them CHAR_223..CHAR_255)
    unnamed = [i for i in range(256) if re.fullmatch(r"CHAR_\d+", font.get(i, "CHAR_0"))]
    return cm, blanked, overrides, unnamed


def load_symbols(decomp):
    syms = {}
    pat = re.compile(r"^(\S+) = (\.\w+):0x([0-9A-Fa-f]+);")
    size_pat = re.compile(r"\bsize:0x([0-9A-Fa-f]+)")
    for line in read(decomp, "config/GAFE01_00/foresta/symbols.txt").splitlines():
        m = pat.match(line)
        if m:
            sec, off = m.group(2), int(m.group(3), 16)
            sm = size_pat.search(line)
            size = int(sm.group(1), 16) if sm else None
            if sec in REL_BASES:
                syms[m.group(1)] = (REL_BASES[sec] + off, size, sec, off)
    return syms


def load_defines(text):
    """Resolve simple '#define NAME expr' lines (hex/dec/+/-/parens/other names)."""
    raw = {}
    for m in re.finditer(r"^#define\s+(\w+)\s+([^\n/]+?)\s*(?://.*|/\*.*)?$", text, re.M):
        raw[m.group(1)] = m.group(2)
    cache = {}

    def ev(name, depth=0):
        if name in cache:
            return cache[name]
        expr = raw[name]
        if not re.fullmatch(r"[\w\s()+\-*]+", expr) or depth > 50:
            raise KeyError(name)
        expr = re.sub(r"\b([A-Za-z_]\w*)\b", lambda m: str(ev(m.group(1), depth + 1)), expr)
        cache[name] = int(eval(expr, {"__builtins__": {}}))   # only digits/operators remain
        return cache[name]

    return ev


def load_ftr_enums(decomp):
    nt = read(decomp, "include/m_name_table.h")
    south = {}
    for start_marker, end_marker, base in (("___FTR0_START = FTR0_START-1,", "FTR0_END", 0x1000),
                                           ("__FTR1_START = FTR1_START-1,", "FTR1_END", 0x3000)):
        body = nt[nt.index(start_marker) + len(start_marker): nt.index(end_marker, nt.index(start_marker))]
        names = re.findall(r"^\s*X\((\w+)\),?\s*$", body, re.M)
        rest = re.sub(r"^\s*X\(\w+\),?\s*$", "", body, flags=re.M).strip()
        assert rest == "", "unexpected content in ftr enum: %r" % rest[:80]
        for i, n in enumerate(names):
            south[n] = base + 4 * i
    fd = read(decomp, "include/m_ftr_def.h")
    body = fd[fd.index("enum ftr_name {") + len("enum ftr_name {"): fd.index("FTR_NUM")]
    idx_names = re.findall(r"^\s*(FTR_\w+),\s*$", body, re.M)
    idx = {n: i for i, n in enumerate(idx_names)}
    ftr_num = len(idx_names)
    # cross-check the two enumerations: ftr_idx(item) must equal the m_ftr_def index
    for n, item in south.items():
        if n in idx:
            fi = (item - 0x1000) >> 2 if item < 0x3000 else 0x400 + ((item - 0x3000) >> 2)
            assert fi == idx[n], (n, hex(item), fi, idx[n])
    return south, idx, ftr_num


def load_scene_enum(decomp):
    text = read(decomp, "include/m_scene_table.h")
    body = text[text.index("enum scene_table {") + len("enum scene_table {"): text.index("SCENE_NUM")]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.findall(r"(SCENE_\w+)", body)


ITEM1_TABLES = ["itemName_paper", "itemName_money", "itemName_tool", "itemName_fish", "itemName_cloth",
                "itemName_etc", "itemName_carpet", "itemName_wall", "itemName_fruit", "itemName_plant",
                "itemName_minidisk", "itemName_dummy", "itemName_ticket", "itemName_insect",
                "itemName_hukubukuro", "itemName_kabu"]
ITEM1_NUM_MACROS = ["PAPER_NUM", "MONEY_NUM", "TOOL_NUM", "FISH_NUM", "CLOTH_NUM", "ETC_NUM",
                    "CARPET_NUM", "WALL_NUM", "FRUIT_NUM", "PLANT_NUM", "MINIDISK_NUM", "DIARY_NUM",
                    "TICKET_NUM", "INSECT_NUM", "HUKUBUKURO_NUM", "KABU_NUM"]


def build_item_emulator(decomp, syms):
    D = load_defines(read(decomp, "include/m_name_table.h"))
    south, fidx, ftr_num = load_ftr_enums(decomp)
    S = lambda n: south[n]
    W = lambda n: south[n] + 3
    kinds = [D(m) for m in ITEM1_NUM_MACROS]
    for sym, n in zip(ITEM1_TABLES, kinds):          # table size must match the *_NUM macro
        assert syms[sym][1] == n * 16, (sym, syms[sym][1], n)
    assert syms["ftrName_table"][1] == 0x400 * 16 and syms["ftrName2_table"][1] == (ftr_num - 0x400) * 16

    def ftr_idx(no):
        t = no >> 12
        if t == 1:
            return (no - 0x1000) >> 2
        if t == 3:
            return 0x400 + ((no - 0x3000) >> 2)
        return 0

    def ftr2item1(no):  # mRmTp_FtrItemNo2Item1ItemNo(no, FALSE)
        fi = ftr_idx(no)
        if S("FTR_FMANEKIN000") <= no <= W("FTR_FMANEKIN254"):
            return D("ITM_CLOTH_START") + ((no - S("FTR_FMANEKIN000")) >> 2)
        if S("FTR_SUM_MONSHIRO") <= no <= W("FTR_NOG_KA"):
            return D("ITM_INSECT_START") + ((no - S("FTR_SUM_MONSHIRO")) >> 2)
        if S("FTR_SUM_FUNA") <= no <= W("FTR_NOG_PIRALUKU"):
            i = (no - S("FTR_SUM_FUNA")) >> 2
            return D("ITM_FISH_START") + i if 0 <= i <= D("FISH_NUM") + 1 else 0
        if S("FTR_FUMBRELLA00") <= no <= W("FTR_FUMBRELLA31"):
            return D("ITM_UMBRELLA_START") + ((no - S("FTR_FUMBRELLA00")) >> 2)
        if fidx["FTR_NOG_BALLOON_COMMON0"] <= fi <= 0x403:
            return D("ITM_BALLOON_START") + ((fi - fidx["FTR_NOG_BALLOON_COMMON0"]) & 7)
        if S("FTR_NOG_COLLEGENOTE") <= no <= W("FTR_IKE_NIKKI_WAFU1"):
            return D("ITM_DIARY_START") + (((no - S("FTR_NOG_COLLEGENOTE")) >> 2) & 15)
        if fidx["FTR_UTIWA0"] <= fi <= 0x45A:
            return D("ITM_BLUEBELL_FAN") + ((fi - fidx["FTR_UTIWA0"]) & 7)
        if fidx["FTR_KAZAGURUMA0"] <= fi <= 0x462:
            return D("ITM_YELLOW_PINWHEEL") + ((fi - fidx["FTR_KAZAGURUMA0"]) & 7)
        if fidx["FTR_GOLD_ITEM0"] <= fi <= 0x452:
            return D("ITM_GOLDEN_NET") + ((fi - fidx["FTR_GOLD_ITEM0"]) & 3)
        if fidx["FTR_TOOL0"] <= fi <= 0x466:
            return D("ITM_NET") + ((fi - fidx["FTR_TOOL0"]) & 3)
        return no

    sign_lo, sign_hi, sign_item = D("SIGNBOARD_START"), D("SIGNBOARD_END"), D("ITM_SIGNBOARD")

    def name_of(x):
        """Returns ('EMPTY'|'UNKNOWN'|None) or (table_symbol, byte_offset) like mIN_copy_name_str."""
        it = ftr2item1(x) & 0xFFFF
        t, cat, ix = it >> 12, (it >> 8) & 0xF, it & 0xFF
        if (t == 2 and ix >= kinds[cat]) or (t == 3 and ftr_idx(it) >= ftr_num):   # mNT_check_unknown
            return "UNKNOWN"
        if t == 2:
            return (ITEM1_TABLES[cat], ix * 16)
        if t == 1:
            return ("ftrName_table", ((it // 4) & 0x3FF) * 16)
        if t == 3:
            return ("ftrName2_table", ((it // 4) & 0x3FF) * 16)
        if sign_lo <= it <= sign_hi:
            return ("itemName_etc", (sign_item & 0xFF) * 16)
        if it == 0:
            return "EMPTY"
        return None

    return name_of


def describe(x, sym):
    t = x >> 12
    cat = sym.replace("itemName_", "").replace("dummy", "diary")
    if t == 2:
        return "item1 category: %s" % cat
    if sym in ("ftrName_table", "ftrName2_table"):
        return "furniture (%s, 4 facings per item)" % ("FTR0" if t == 1 else "FTR1")
    if t in (1, 3):
        return "furniture-form id converted to its %s item name (mRmTp_FtrItemNo2Item1ItemNo)" % cat
    if x < 0x1000:
        return "signboard ids 0x0900-0x0920 all use itemName_etc[30]"
    return sym


def build_item_ranges(decomp, syms):
    name_of = build_item_emulator(decomp, syms)
    table = [name_of(x) for x in range(0x10000)]
    ranges, x = [], 0
    while x < 0x10000:
        v = table[x]
        if not isinstance(v, tuple):
            x += 1
            continue
        sym, off0 = v
        best = None
        for shift in (0, 2, 6):                  # longest run wins; ties prefer smaller shift
            n = 1
            while x + n < 0x10000 and table[x + n] == (sym, off0 + ((n >> shift) * 16)):
                n += 1
            if best is None or n > best[1]:
                best = (shift, n)
        shift, n = best
        ranges.append({"id_min": h(x, 4), "id_max": h(x + n - 1, 4),
                       "table_addr": h(syms[sym][0] + off0, 8), "shift": shift,
                       "note": "%s; %s+0x%X" % (describe(x, sym), sym, off0)})
        x += n
    # re-check the generic lookup against the emulation for every id
    for x in range(0x10000):
        hit = [r for r in ranges if int(r["id_min"], 16) <= x <= int(r["id_max"], 16)]
        assert len(hit) <= 1
        exp = table[x]
        if hit:
            r = hit[0]
            got = int(r["table_addr"], 16) + ((x - int(r["id_min"], 16)) >> r["shift"]) * 16
            assert isinstance(exp, tuple) and got == syms[exp[0]][0] + exp[1], hex(x)
        else:
            assert not isinstance(exp, tuple), hex(x)
    empty = [h(x, 4) for x in range(0x10000) if table[x] == "EMPTY"]
    unknown = [x for x in range(0x10000) if table[x] == "UNKNOWN"]
    return ranges, empty, unknown


def spans(ids):
    out = []
    for x in ids:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [[h(a, 4), h(b, 4)] for a, b in out]


# --------------------------------------------------------------------------------------
# Town map (src/game/m_map_ovl.c). Every address below comes from symbols.txt + the REL
# section bases; MAP_VERIFIED_LIVE lists the ones that were read back live (README §13).
# --------------------------------------------------------------------------------------
def load_block_types(decomp):
    """enum { mFM_BLOCK_TYPE_* } in include/m_field_make.h, in index order up to _NUM."""
    text = read(decomp, "include/m_field_make.h")
    start = text.index("mFM_BLOCK_TYPE_BORDER_CLIFF_TOP,")
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", text[start: text.index("mFM_BLOCK_TYPE_NUM", start)], flags=re.S)
    names = re.findall(r"\b(mFM_BLOCK_TYPE_\w+)\s*,", body)
    assert "=" not in body and len(names) == len(set(names)), "unexpected block type enum shape"
    return [n.replace("mFM_BLOCK_TYPE_", "") for n in names]


def load_block_kinds(decomp):
    text = read(decomp, "include/m_random_field_h.h")
    return {m.group(1): 1 << int(m.group(2))
            for m in re.finditer(r"^#define\s+mRF_BLOCKKIND_(\w+)\s+\(1 << (\d+)\)", text, re.M)}


# Building indicators, in the game's label priority (mMP_set_field_data, m_map_ovl.c:802-833;
# PLAYER is labelled separately at label_info[1][2]). All of them are drawn INSIDE the acre
# texture of their block type; the fallback marker is an original placeholder for clients
# that cannot (or may not) draw the RAM texture.
# key, label, mRF_BLOCKKIND_*, mFM_BLOCK_TYPE_*, fallback colour
MAP_BUILDINGS = [
    ("player_houses", "Houses", "PLAYER", "PLAYER_HOUSE", "#3D7BE0"),
    ("shop", "Shop", "SHOP", "TRACKS_SHOP", "#E0703D"),
    ("police", "Police", "POLICE", "POLICE_BOX", "#3DA0E0"),
    ("post_office", "Post", "POSTOFFICE", "TRACKS_POST_OFFICE", "#E0B93D"),
    ("wishing_well", "Well", "SHRINE", "SHRINE", "#8E6BD6"),
    ("station", "Station", "STATION", "TRACKS_STATION", "#7A7A7A"),
    ("dump", "Dump", "DUMP", "TRACKS_DUMP", "#8A6A3D"),
    ("museum", "Museum", "MUSEUM", "MUSEUM", "#B04FA8"),
    ("tailor", "Tailor", "TAILORS", "NEEDLEWORK", "#D6487A"),
    ("dock", "Dock", "DOCK", "PORT", "#2E9E8F"),
]

# Absolute addresses read live from the running game (GAFE01 rev 0, town "Cheevo") through
# dolphin-lnk, with the value that confirmed each one. The build asserts the spec reproduces them.
MAP_VERIFIED_LIVE = {
    "g_block_type_p": 0x80653E1C,       # holds 0x81295A3C (= l_block_type)
    "l_block_type": 0x81295A3C,         # 70 types; equal to the save-derived types
    "g_block_kind_p": 0x80653E20,       # holds 0x81295924 (= l_block_kind)
    "l_block_kind": 0x81295924,         # kind bits match the building block types
    "data_combi_table": 0x8080DD80,     # type at +4 reproduces l_block_type for all 70 blocks
    "l_map_texture": 0x806CD9C0,        # [0] == kan_tizu_f_TA_tex_txt; all used entries in range
    "l_map_pal": 0x806CDB70,
    "pluss_bridge": 0x806CDBDC,
    "l_kan_tizu_pal": 0x806CDCA0,       # holds kan_tizu1_pal, kan_tizu2_pal
    "kan_tizu1_pal": 0x806CDC60,
    "kan_tizu2_pal": 0x806CDC80,
    "kan_win_yane_tex": 0x80AFBBE0,
    "kan_win_npc2T_1_model": 0x80AFC3A0,  # +4 PRIM, +12 ENV (G_SETPRIMCOLOR / G_SETENVCOLOR words)
    "kan_win_npc2T_2_model": 0x80AFC3C0,
    "kan_win_npc2T_3_model": 0x80AFC3E0,
    "mMP_house_pos_list": 0x80B075D8,   # every villager found an exact slot; terminator at [201]
}
TEXTURE_FORMATS = {
    "C4": "4-bit palette index. GX tiles of 8x8 pixels (32 bytes), tiles stored row-major across the "
          "image; inside a tile 8 rows of 4 bytes, high nibble = left pixel. Pixel colour = palette[index].",
    "RGB5A3": "BE16. Bit 15 set: opaque RGB555, channel c (5 bits) -> (c << 3) | (c >> 2). Bit 15 clear: "
              "A3RGB444, alpha a (3 bits) -> (a << 5) | (a << 2) | (a >> 1), channel c (4 bits) -> c * 17.",
    "IA4": "1 byte per pixel (GX_TF_IA4, what the N64 IA/8b format maps to in emu64). GX tiles of 8x4 "
           "pixels (32 bytes), tiles row-major; inside a tile 4 rows of 8 bytes. High nibble = alpha, low "
           "nibble = intensity, each nibble n -> n * 17.",
}


def build_map(decomp, syms):
    A = lambda name: syms[name][0]
    types = load_block_types(decomp)
    T = {n: i for i, n in enumerate(types)}
    assert len(types) == 108, len(types)
    kinds = load_block_kinds(decomp)
    # mMP_check_bg_kind (m_map_ovl.c:632): border acres are left out of the 5x6 map
    skip = sorted(set(range(T["BORDER_CLIFF_CORNER_TOP_RIGHT"] + 1))
                  | {T["BORDER_CLIFF_LEFT_TRANSITION"], T["BORDER_CLIFF_RIGHT_TRANSITION"],
                     T["BORDER_CLIFF_LEFT_TUNNEL"], T["BORDER_CLIFF_RIGHT_TUNNEL"]})
    # acre textures: 69 contiguous 0x200-byte C4 images
    tex = sorted((v[0], v[1]) for k, v in syms.items() if re.fullmatch(r"kan_tizu_\w+_TA_tex_txt", k))
    assert len(tex) == 69 and all(sz == 0x200 for _, sz in tex)
    assert all(b[0] - a[0] == 0x200 for a, b in zip(tex, tex[1:])), "acre textures not contiguous"
    tex_start, tex_end = tex[0][0], tex[-1][0] + 0x200
    # table sizes must match the header constants
    assert syms["l_map_texture"][1] == 108 * 4 and syms["l_map_pal"][1] == 108 == syms["pluss_bridge"][1]
    assert syms["l_kan_tizu_pal"][1] == 8 and syms["kan_tizu1_pal"][1] == 0x20 == syms["kan_tizu2_pal"][1]
    assert syms["data_combi_table"][1] == 368 * 6 and syms["mMP_house_pos_list"][1] == 202 * 12
    assert syms["l_block_type"][1] == 70 and syms["l_block_kind"][1] == 70 * 4
    assert syms["kan_win_yane_tex"][1] == 0x100 and syms["kan_win_npc2T_table"][1] == 12
    for name, addr in MAP_VERIFIED_LIVE.items():
        assert A(name) == addr, "%s: symbols.txt gives 0x%08X, live 0x%08X" % (name, A(name), addr)

    LIVE, HDR = "verified_live", "header_derived"
    block_index = "block_z * 7 + block_x"
    return {
        "status": LIVE,
        "note": "Town map as the game's map screen draws it (src/game/m_map_ovl.c): 5x6 acre images, "
                "building indicators and the player's location. Every image is read from emulated RAM "
                "and decoded at runtime; never ship decoded images. The map never needs the item/fg "
                "grid (buried items, fossils, money rocks): do not read it. All tables except the "
                "villager and player data are static for a session: read them once.",
        "grid": {
            "status": LIVE,
            "cols": 5, "rows": 6,
            "col_labels": ["1", "2", "3", "4", "5"],
            "row_labels": ["A", "B", "C", "D", "E", "F"],
            "first_block_x": 1, "first_block_z": 1,
            "block_cols": 7, "block_rows": 10,
            "block_index": block_index,
            "acre": "map column c (0-4) is block_x = c + 1; map row r (0-5) is block_z = r + 1",
            "acre_label": "row_labels[block_z - 1] + '-' + col_labels[block_x - 1]",
            "acre_map_units": 22,
            "map_units": "one acre = 22 units = 22 visible texels; x right, y down (row A on top, "
                         "block_z grows southwards). The game has no outer frame or labels in the "
                         "acre art: draw your own (the acre art does include a 1-texel dark line on "
                         "its top row and left column)."},
        "acre_types": {
            "status": LIVE,
            "type_count": 108,
            "table": {"status": LIVE, "pointer_addr": h(A("g_block_type_p"), 8),
                      "expected_pointer": h(A("l_block_type"), 8), "entry_type": "u8", "count": 70,
                      "index": block_index,
                      "note": "g_block_type_p -> l_block_type (m_field_make.c:1361-1377), rebuilt from the "
                              "save on every field load. If the pointer differs from expected_pointer, "
                              "use from_save."},
            "from_save": {
                "status": LIVE,
                "combi_table": {"base": "common_data", "offset": "0x173A8", "entry_type": "u16",
                                "count": 70, "index": block_index,
                                "note": "mFM_combination_c: combination_type = v >> 2, height = v & 3"},
                "data_combi_table": {"addr": h(A("data_combi_table"), 8), "entry_len": 6, "count": 368,
                                     "bg_id_offset": "0x0", "fg_id_offset": "0x2", "type_offset": "0x4",
                                     "note": "mFM_combo_info_c {u16 bg_id, u16 fg_id, u8 type}; type is at "
                                             "+4 (the header comment says 0x05)"},
                "formula": "type = u8[data_combi_table.addr + (combi_table[i] >> 2) * 6 + type_offset]; "
                           "reject combination_type >= count"},
            "skip_types": skip,
            "pad_type": T["FLAT"],
            "selection": "mMP_make_max_no_table (m_map_ovl.c:643): fill a 30-entry list with pad_type; "
                         "scan block_z = 1..6 (outer) and block_x = 1..5 (inner); skip any type in "
                         "skip_types; append every other type (after the bridge rule) to the list in "
                         "order. List entry n is drawn at map column n % 5, row n / 5. In a normal town "
                         "nothing is skipped, so entry n is simply block (n % 5 + 1, n / 5 + 1). Reject "
                         "a type >= type_count.",
            "bridge": {"status": HDR, "base": "common_data", "offset": "0x213F0",
                       "block_x_offset": "0x0", "block_z_offset": "0x1", "flags_offset": "0x2",
                       "exists_mask": "0x80",
                       "note": "PlusBridge_c (the extra bridge bought from the town's request). The "
                               "offset is fixed by explicit padding in Save_t; exists is the first "
                               "1-bit field, which MWCC stores in the high bit. If exists and the "
                               "block matches and pluss_bridge[type] != none_value, type = "
                               "pluss_bridge[type]."},
            "pluss_bridge": {"status": LIVE, "addr": h(A("pluss_bridge"), 8), "entry_type": "u8",
                             "count": 108, "none_value": "0xFF"},
        },
        "acre_texture": {
            "status": LIVE,
            "pointer_table": {"addr": h(A("l_map_texture"), 8), "entry_type": "ptr", "count": 108,
                              "index": "acre type (after the bridge rule)"},
            "valid_range": {"start": h(tex_start, 8), "end": h(tex_end, 8), "count": len(tex),
                            "rule": "start <= ptr < end and (ptr - start) % size == 0"},
            "format": "C4", "width": 32, "height": 32, "size": 0x200,
            "visible": {"x": 0, "y": 0, "width": 22, "height": 22,
                        "note": "kan_tizu_v maps the 22x22-unit quad to texels 0..22 (s,t 0..0x2C0 in "
                                "10.5 fixed point); t = 0 is the top edge, no flip. Crop to 22x22."},
            "palette": {
                "format": "RGB5A3", "entries": 16, "size": 0x20, "transparent_index": 0,
                "selector_table": {"addr": h(A("l_map_pal"), 8), "entry_type": "u8", "count": 108,
                                   "index": "acre type", "note": "0 or 1"},
                "pointer_table": {"addr": h(A("l_kan_tizu_pal"), 8), "entry_type": "ptr", "count": 2,
                                  "index": "selector"},
                "expected_pointers": [h(A("kan_tizu1_pal"), 8), h(A("kan_tizu2_pal"), 8)]},
        },
        "texture_formats": TEXTURE_FORMATS,
        "buildings": {
            "status": LIVE,
            "note": "The shop, post office, station, dump, wishing well, police box, museum, tailor, dock "
                    "and the 4-house player cluster are drawn INSIDE the acre texture of their block "
                    "type, so drawing acre_texture already shows them (marker.kind 'ram_texture', "
                    "texture 'acre_texture'). Only villager houses are separate icons (villager_houses). "
                    "block_kinds identifies the building acres; fallback_marker is an original "
                    "placeholder (coloured square + short label) to draw only when the acre texture "
                    "fails validation.",
            "block_kinds": {"status": LIVE, "pointer_addr": h(A("g_block_kind_p"), 8),
                            "expected_pointer": h(A("l_block_kind"), 8), "entry_type": "u32", "count": 70,
                            "index": block_index},
            "indicators": [
                {"key": key, "label": label, "source": "block_kinds",
                 "kind_mask": h(kinds[kind]), "block_type": T[btype],
                 "marker": {"kind": "ram_texture", "texture": "acre_texture"},
                 "fallback_marker": {"kind": "original_marker", "shape": "square", "label": label,
                                     "color": color},
                 "status": LIVE}
                for key, label, kind, btype, color in MAP_BUILDINGS],
            "match": "acre (block_x, block_z) has the building iff block_kinds[block_index] & kind_mask; "
                     "when several bits are set, the first matching indicator wins (game label order).",
        },
        "villager_houses": {
            "status": LIVE,
            "source": "villagers: every valid slot i (validity rule 6) with 1 <= home_block_x <= 5 and "
                      "1 <= home_block_z <= 6 gets one icon in that acre (mMP_set_house_data, "
                      "m_map_ovl.c:696-783).",
            "marker": {"kind": "ram_texture", "addr": h(A("kan_win_yane_tex"), 8), "format": "IA4",
                       "width": 16, "height": 16, "size": 0x100, "map_size_units": 10,
                       "tint": "rgb = env + (prim - env) * I / 255, alpha = A (combiner of "
                               "kan_win_npc2T_*_model)"},
            "fallback_marker": {"kind": "original_marker", "shape": "square", "label": "",
                                "note": "a 10-unit square in the tier's prim colour"},
            "tiers": [
                {"tier": i, "display_list_addr": h(A("kan_win_npc2T_%d_model" % (i + 1)), 8),
                 "prim_offset": "0x4", "env_offset": "0xC", "color_len": 4,
                 "expected_prim": prim, "expected_env": [225, 225, 225], "status": LIVE}
                for i, prim in enumerate([[90, 90, 225], [145, 70, 205], [170, 115, 20]])],
            "tier_rule": {
                "house_y": {"base": "common_data", "offset": "0x26164", "stride": "0x38",
                            "field_offset": "0x8", "type": "f32", "index": "villager slot i",
                            "note": "npclist[i].house_position.y"},
                "step3": "(combi_table[0] & 3) == 2  (combi_table from acre_types.from_save; "
                         "mRF_CheckFieldStep3)",
                "rule": "layer = 2 if y < 100; else (1 if y < 220 else 0) if step3; else 1. "
                        "If not step3: layer = max(layer - 1, 0). tier = layer "
                        "(mMP_check_layer + mCoBG_Height2GetLayer)."},
            "slot_rule": {
                "fg_name": "BE16(data_combi_table.addr + (combi_table[home_block_z * 7 + home_block_x] "
                           ">> 2) * 6 + fg_id_offset)",
                "house_pos_list": {"addr": h(A("mMP_house_pos_list"), 8), "entry_len": 12, "count": 202,
                                   "terminator": "0x03B8", "terminator_index": 201, "fg_name_offset": "0x0",
                                   "slots_offset": "0x2", "slot_count": 3, "slot_len": 3,
                                   "slot_fields": {"ut_x": 0, "ut_z": 1, "idx": 2}},
                "rule": "walk entries until fg_name == terminator; take the FIRST entry whose fg_name "
                        "matches; in it take the first slot with ut_x == home_ut_x and ut_z == "
                        "home_ut_z - 1, else that entry's slot 0. No matching entry: slot 0 of entry 0. "
                        "idx = slot.idx (0-8).",
                "placement": "icon centre = acre top-left + (x_offsets[idx % 3], y_offsets[idx / 3]) "
                             "map units (y down)",
                "x_offsets": [5, 13, 17], "y_offsets": [4, 11, 18]},
            "draw_order": "within an acre, draw in ascending tier order (tier 0 first), as the game "
                          "does; it only matters where icons overlap",
        },
        "player": {
            "status": LIVE,
            "chain": [
                {"step": "game", "addr": "0x812F31B8", "type": "ptr", "check": "in MEM1",
                 "note": "bases.gamePT"},
                {"step": "exec", "from": "game", "offset": "0x4", "type": "u32", "equals": "0x8062B370",
                 "note": "GAME.exec == bases.play_main"},
                {"step": "player_count", "from": "game", "offset": "0x1DC4", "type": "s32", "min": 1,
                 "note": "play.actor_info.list[ACTOR_PART_PLAYER].num_actors (m_play.h:92 actor_info "
                         "@0x1DA8; m_actor.h:1159-1167)"},
                {"step": "actor", "from": "game", "offset": "0x1DC8", "type": "ptr", "check": "in MEM1",
                 "note": "play.actor_info.list[3].actor"}],
            "actor_checks": [
                {"offset": "0x0", "type": "s16", "equals": "0x0", "note": "id == mAc_PROFILE_PLAYER"},
                {"offset": "0x2", "type": "u8", "equals": "0x3", "note": "part == ACTOR_PART_PLAYER"}],
            "position": {"x_offset": "0x28", "y_offset": "0x2C", "z_offset": "0x30", "type": "f32",
                         "note": "actor.world.position"},
            "facing": {"offset": "0xDE", "type": "s16", "full_turn": 65536,
                       "map_vector": "(sin(2*pi*a/65536), cos(2*pi*a/65536)) as (right, down): "
                                     "0 = south (down), 0x4000 = east (right), 0x8000 = north, "
                                     "0xC000 = west",
                       "note": "actor.shape_info.rotation.y (the drawn facing)"},
            "transform": {"world_units_per_acre": 640, "map_units_per_acre": 22,
                          "block": "block_x = trunc(x / 640), block_z = trunc(z / 640)",
                          "map_x": "(x / 640 - 1) * 22", "map_y": "(z / 640 - 1) * 22",
                          "note": "1 tile = 40 world units, 16 tiles per acre; map origin (0, 0) = "
                                  "block (1, 1) = world (640, 640)"},
            "show_when": {
                "scene_no": [7],
                "field_type": {"base": "common_data", "offset": "0x26001", "type": "u8", "equals": "0x0",
                               "status": HDR,
                               "note": "common_data.field_type == mFI_FIELDTYPE2_FG (reads 0 outdoors "
                                       "live; the indoor value is not yet observed)"},
                "block_x_min": 1, "block_x_max": 5, "block_z_min": 1, "block_z_max": 6,
                "note": "plus every chain/actor check and validity rules 1-5; hide the marker otherwise "
                        "(the island is scene 7 too, at block_z 8, so the block range hides it). "
                        "Read twice and accept when both reads agree (validity rule 8)."},
            "marker": {"kind": "original_marker", "shape": "dot_with_arrow", "color": "#FF2D2D",
                       "outline": "#FFFFFF",
                       "note": "dot at (map_x, map_y), small arrow along map_vector. Never a character "
                               "sprite."},
            "acre_highlight": {"kind": "original_marker", "shape": "box", "color": "#FF00E6",
                               "note": "outline the acre (block_x, block_z), in the spirit of the "
                                       "game's magenta cursor"},
            "indoor_acre": {
                "status": HDR, "optional": True,
                "next_scene": {"base": "common_data", "offset": "0x28530", "type": "s32"},
                "exit_position": {"base": "common_data", "offset": "0x2854C", "type": "s16", "count": 3,
                                  "note": "structure_exit_door_data.exit_position (x, y, z)"},
                "rule": "the game's map, opened indoors (field_type != 0 and next_scene != 0), marks the "
                        "acre of exit_position (m_map_ovl.c:858-862). A client may show only "
                        "acre_highlight there, never the dot."}},
    }


# --------------------------------------------------------------------------------------
def build(decomp):
    syms = load_symbols(decomp)
    # sanity: bases used by clients must match the symbol table + section bases
    assert syms["common_data"][0] == 0x81266400
    assert syms["gamePT"][0] == 0x812F31B8
    assert syms["play_main"][0] == 0x8062B370
    # mNpc_LoadNpcNameString's buffer sits 0x48 bytes before l_npc_name_cache (both .bss, m_npc.c)
    assert syms["dma_area$2710"][:2] == (syms["l_npc_name_cache"][0] - 0x48, 0x40)
    assert syms["common_data"][1] == 0x2DC00          # anchors the island_weather tail (layout_check.py)
    charmap, blanked, overrides, unnamed = load_charmap(decomp)
    ranges, empty, unknown = build_item_ranges(decomp, syms)
    scenes = load_scene_enum(decomp)
    assert scenes.index("SCENE_FG") == 7 and scenes.index("SCENE_TITLE_DEMO") == 33
    scene_enum = {str(i): SCENE_LABELS.get(n, n.replace("SCENE_", "Test/debug scene ")) for i, n in enumerate(scenes)}
    ptr_tab = syms["itemName_table$398"]
    return {
        "schema": 1,
        "game": {"id": "GAFE01", "id_addr": "0x80000000", "revision": 0, "revision_addr": "0x80000007"},
        "bases": {"common_data": "0x81266400", "gamePT": "0x812F31B8", "play_main": "0x8062B370"},
        "globals": GLOBALS,
        "players": {"base": "common_data", "offset": "0x20", "stride": "0x2440", "count": 4,
                    "fields": PLAYER_FIELDS},
        "villagers": {"base": "common_data", "offset": "0x17438", "stride": "0x988", "count": 15,
                      "fields": VILLAGER_FIELDS,
                      "memories": {"offset": "0x10", "stride": "0x138", "count": 7,
                                   "note": "Anmmem_c memories[7] inside each villager record; see validity rule 7",
                                   "fields": MEMORY_FIELDS}},
        "homes": {"base": "common_data", "offset": "0x9CE8", "stride": "0x26B0", "count": 4,
                  "note": "mHm_hs_c homes[4]. Player i's house is homes[(house_arrangement >> (2*i)) & 3]; "
                          "confirm by comparing owner_player_id/owner_name with that player's player_id/name. "
                          "Loan states: loan > 0 = paying; loan == 0 and (house_size > 0 or house_next_size > 0 "
                          "or has_basement) = paid off; loan == 0 on an untouched small house = no loan yet.",
                  "fields": HOME_FIELDS},
        "enums": dict(ENUMS, scene_no=scene_enum),
        "charmap": charmap,
        "charmap_notes": {"source": "ac-decomp tools/msg_tool.py CHAR_MAP (CC0), corrected from the glyph "
                                    "names in include/m_font.h (CC0)",
                          "blanked_control_entries": blanked,
                          "m_font_overrides": overrides,
                          "uncertain_entries": {h(i, 2): n for i, n in sorted(CHARMAP_UNCERTAIN.items())},
                          "placeholder_entries": "%s-%s: m_font.h has no glyph names for these; the entries are "
                                                 "msg_tool.py's placeholders (the Latin-1 char equal to the "
                                                 "byte), not verified glyphs" % (h(unnamed[0], 2),
                                                                                  h(unnamed[-1], 2)),
                          "entry_length": "an entry is 0, 1 or 2 characters; decode by concatenating entries"},
        "item_names": {
            "entry_len": 16,
            "status": "header_derived",
            "empty_ids": empty,
            "unknown_ranges": spans(unknown),
            "lookup": "first (only) range with id_min <= id <= id_max; index = (id - id_min) >> shift; "
                      "name = trim(charmap-decode(read(table_addr + index*entry_len, entry_len))). "
                      "Ranges never overlap. empty_ids -> show an em dash; unknown_ranges -> the game "
                      "itself shows 'Unknown'; any other unmatched id has no name (show the hex id).",
            "runtime_check": {
                "pointer_table_addr": h(ptr_tab[0], 8),
                "expected": [h(syms[s][0], 8) for s in ITEM1_TABLES],
                "note": "itemName_table$398 (16 BE32 pointers, item1 categories 0..15). If the values read "
                        "here equal 'expected', the foresta .data base and every table_addr are confirmed."},
            "ranges": ranges,
        },
        "extras": {
            "npc_name_cache": {
                "addr": h(syms["l_npc_name_cache"][0], 8),
                "status": "header_derived",
                "note": "Villager names are NOT in MEM1: mNpc_LoadNpcNameString DMAs them from ARAM on demand. "
                        "The game keeps the most recently looked-up name here (mNpc_NameCache_c, m_npc.c:3806). "
                        "A client may harvest {npc_id -> name} pairs from it while the player talks to "
                        "villagers. Accept only npc_id values that are town villagers ((id & 0xF000) == 0xE000).",
                "fields": [F("npc_id", 0x0, "u16", "header_derived"),
                           F("name", 0x2, "str", "header_derived", len=8),
                           F("npc_type", 0xA, "u8", "header_derived",
                             "mNpc_NAME_TYPE_*: 1 = villager (NPC); accept a name only when it is 1")],
                "dma_area_addr": h(syms["dma_area$2710"][0], 8),
                "dma_area_len": 64,
                "dma_area_note": "mNpc_LoadNpcNameString's static 64-byte DMA buffer (m_npc.c:3765): every name "
                                 "lookup copies the 8 names of name ids b..b+7 here, b = name_id & 0xFC "
                                 "(ALIGN_PREV(name_id * 8, 32) / 8), 8 bytes each. Cross-check: accept the cached "
                                 "(npc_id, name) only when the buffer's slot (npc_id & 0xFF) - b holds the same 8 "
                                 "bytes; a stale or torn cache fails this. The other slots name villagers 0xE000 | "
                                 "(b + k)."},
            "daily": {
                "status": "header_derived",
                "note": "Buried items and the glowing spot (m_all_grow_ovl.c, m_museum.c). fg[z][x] is "
                        "fg_blocks_z x fg_blocks_x blocks of units x units u16 item ids (row-major by ut_z, "
                        "then ut_x); block (bx, bz) starts at fg_addr + ((bz-1)*fg_blocks_x + (bx-1))*fg_block_bytes. "
                        "deposit has one u16 per block row: unit (ut_x, ut_z) of block index b is buried iff "
                        "(BE16(deposit_addr + (b*units + ut_z)*2) >> ut_x) & 1. A buried fossil is fossil_item with "
                        "its buried bit set; the daily renewal tops buried fossils up to fossil_daily_max "
                        "(mMsm_DepositFossil), so fossils dug today = fossil_daily_max - buried fossils. "
                        "shine_pos[player_no] (4 bytes: block_x 1-5, block_z 1-6, ut_x, ut_z; all 0 = none "
                        "today) is that player's glowing spot; the unit holds shine_spot_item until it is dug.",
                "fg_addr": "0x%08X" % (0x81266400 + 0x137A8),
                "fg_block_bytes": "0x200",
                "fg_blocks_x": 5,
                "fg_blocks_z": 6,
                "units": 16,
                "deposit_addr": "0x%08X" % (0x81266400 + 0x20F1C),
                "fossil_item": "0x2511",
                "fossil_daily_max": 5,
                "shine_spot_item": "0x005C",
                "shine_pos_addr": "0x%08X" % (0x81266400 + 0x23E40),
                "shine_pos_stride": "0x4"},
            "climate": {
                "addr": h(syms["l_mFI_climate"][0], 8),
                "type": "s32",
                "status": "header_derived",
                "note": "l_mFI_climate (m_field_info.c). The player is on the island (island_weather applies) "
                        "iff the value == 1 (mFI_CLIMATE_ISLAND); mFI_GetClimate treats 0 and 2-5 as the town."},
        },
        "map": build_map(decomp, syms),
        "validity": {"rules": VALIDITY_RULES,
                     "not_in_town_scenes": [scenes.index(n) for n in NOT_IN_TOWN_SCENES]},
    }


# Absolute addresses read live with Dolphin Memory Engine on retail GAFE01 rev 0.
VERIFIED_LIVE = {("globals", "town_name"): 0x8126F520, ("players", "name"): 0x81266420,
                 ("players", "wallet"): 0x812664AC, ("globals", "rtc_hour"): 0x8128C522,
                 ("globals", "rtc_year"): 0x8128C526}


def absolute(spec, sect, key, player=0):
    cd = int(spec["bases"]["common_data"], 16)
    if sect == "globals":
        f = next(f for f in spec["globals"] if f["key"] == key)
        return cd + int(f["offset"], 16)
    rec = spec[sect]
    f = next(f for f in rec["fields"] if f["key"] == key)
    return cd + int(rec["offset"], 16) + player * int(rec["stride"], 16) + int(f["offset"], 16)


HEX_RE = re.compile(r"^0x[0-9A-F]+$")
REQUIRED = {
    "globals": ["scene_no", "player_no", "now_private", "town_name", "rtc_sec", "rtc_min", "rtc_hour",
                "rtc_day", "rtc_weekday", "rtc_month", "rtc_year", "weather", "weather_intensity",
                "kabu_prices"],
    "players": ["name", "town_name", "exists", "pockets", "item_conditions", "wallet", "loan", "bank"],
    "villagers": ["npc_id"],
    "enums": ["weather", "weekday", "scene_no"],
}
TYPES = {"u8", "s8", "u16", "s16", "u32", "s32", "f32", "ptr", "str", "u16[]", "u8[]", "u32[]"}
STATUSES = {"verified_live", "ar_code", "header_derived", "computed"}
SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4, "ptr": 4}


def validate(spec):
    errs = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if (k in ("offset", "stride", "id_min", "id_max", "addr", "pointer_addr", "expected_pointer",
                          "equals", "start", "end", "terminator", "none_value", "exists_mask", "kind_mask")
                        or k.endswith("_offset") or k.endswith("_addr") or path.endswith(".bases")):
                    if not (isinstance(v, str) and HEX_RE.match(v)):
                        errs.append("%s.%s not a hex string: %r" % (path, k, v))
                if k == "status" and v not in STATUSES:     # every status anywhere in the file
                    errs.append("%s.status not a documented value: %r" % (path, v))
                walk(v, path + "." + k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))

    walk(spec, "$")
    for (sect, key), addr in VERIFIED_LIVE.items():
        if absolute(spec, sect, key) != addr:
            errs.append("%s.%s does not reproduce verified address 0x%08X" % (sect, key, addr))
    for v in spec["item_names"]["empty_ids"] + spec["item_names"]["runtime_check"]["expected"]:
        if not HEX_RE.match(v):
            errs.append("bad hex %r" % v)
    keys = {"globals": [f["key"] for f in spec["globals"]],
            "players": [f["key"] for f in spec["players"]["fields"]],
            "villagers": [f["key"] for f in spec["villagers"]["fields"]],
            "enums": list(spec["enums"])}
    for sect, req in REQUIRED.items():
        for k in req:
            if k not in keys[sect]:
                errs.append("missing required %s key %s" % (sect, k))
        if len(set(keys[sect])) != len(keys[sect]):
            errs.append("duplicate keys in %s" % sect)
    for f in (spec["globals"] + spec["players"]["fields"] + spec["villagers"]["fields"]
              + spec["villagers"]["memories"]["fields"]):
        if f["type"] not in TYPES:
            errs.append("bad type %s" % f)
        if f["status"] not in STATUSES:
            errs.append("bad status %s" % f["key"])
        if f["type"] == "str" and "len" not in f or f["type"].endswith("[]") and "count" not in f:
            errs.append("missing len/count %s" % f["key"])
        if f.get("enum") and f["enum"] not in spec["enums"]:
            errs.append("unknown enum %s" % f["enum"])
        size = SIZES.get(f["type"], 1)
        if int(f["offset"], 16) % size:
            errs.append("misaligned %s" % f["key"])
    for f in spec["globals"]:                             # sizeof(common_data_t) == 0x2DC00
        size = SIZES.get(f["type"], 1) * f.get("len", f.get("count", 1))
        if f["type"] == "u16[]":
            size = 2 * f["count"]
        if int(f["offset"], 16) + size > 0x2DC00:
            errs.append("global %s outside common_data" % f["key"])
    for f in spec["homes"]["fields"]:
        if f.get("enum") and f["enum"] not in spec["enums"]:
            errs.append("unknown enum %s" % f["enum"])
        if int(f["offset"], 16) >= int(spec["homes"]["stride"], 16):
            errs.append("homes.%s outside record" % f["key"])
    # homes[4] ends where fg starts; deposit ends at last_grow_time (m_common_data.h)
    if 0x9CE8 + 4 * int(spec["homes"]["stride"], 16) != 0x137A8:
        errs.append("homes[4] does not end at fg (0x137A8)")
    d = spec["extras"]["daily"]
    if int(d["deposit_addr"], 16) - 0x81266400 + d["fg_blocks_x"] * d["fg_blocks_z"] * d["units"] * 2 != 0x212DC:
        errs.append("deposit[] does not end at last_grow_time (0x212DC)")
    for rec in ("players", "villagers"):
        stride = int(spec[rec]["stride"], 16)
        for f in spec[rec]["fields"]:
            if int(f["offset"], 16) >= stride:
                errs.append("%s.%s outside record" % (rec, f["key"]))
    if len(spec["charmap"]) != 256:
        errs.append("charmap must have 256 entries")
    errs += validate_map(spec.get("map"))
    prev = -1
    for r in spec["item_names"]["ranges"]:
        lo, hi = int(r["id_min"], 16), int(r["id_max"], 16)
        if not (prev < lo <= hi):
            errs.append("ranges overlap/unsorted at %s" % r["id_min"])
        prev = hi
    return errs


MAP_MARKER_KINDS = {"ram_texture", "original_marker"}
COLOR_RE = re.compile(r"^#[0-9A-F]{6}$")


def validate_map(m):
    if not isinstance(m, dict):
        return ["missing map section"]
    errs = []
    req = {"grid", "acre_types", "acre_texture", "texture_formats", "buildings", "villager_houses", "player"}
    errs += ["map missing %s" % k for k in sorted(req - set(m))]
    if errs:
        return errs
    g = m["grid"]
    if (len(g["col_labels"]), len(g["row_labels"])) != (g["cols"], g["rows"]) or g["cols"] * g["rows"] != 30:
        errs.append("map.grid labels/dims inconsistent")
    at, tx = m["acre_types"], m["acre_texture"]
    n = at["type_count"]
    if not all(0 <= t < n for t in at["skip_types"] + [at["pad_type"]]) or at["pad_type"] in at["skip_types"]:
        errs.append("map.acre_types skip/pad types invalid")
    for tab in (tx["pointer_table"], tx["palette"]["selector_table"], at["pluss_bridge"]):
        if tab["count"] != n:
            errs.append("map table %s does not cover all %d types" % (tab["addr"], n))
    if tx["size"] != tx["width"] * tx["height"] // 2 or tx["format"] not in m["texture_formats"]:
        errs.append("map.acre_texture size/format inconsistent")
    vr = tx["valid_range"]
    if int(vr["end"], 16) - int(vr["start"], 16) != vr["count"] * tx["size"]:
        errs.append("map.acre_texture.valid_range inconsistent")
    vis = tx["visible"]
    if vis["width"] != g["acre_map_units"] or vis["height"] != g["acre_map_units"]:
        errs.append("map visible crop != acre_map_units")
    pal = tx["palette"]
    if pal["size"] != 2 * pal["entries"] or len(pal["expected_pointers"]) != pal["pointer_table"]["count"]:
        errs.append("map palette inconsistent")
    vh = m["villager_houses"]["marker"]
    if vh["format"] not in m["texture_formats"] or vh["size"] != vh["width"] * vh["height"]:
        errs.append("map villager house texture inconsistent")
    if len(m["villager_houses"]["tiers"]) != 3:
        errs.append("map needs 3 house tiers")
    keys = set()
    for b in m["buildings"]["indicators"]:
        if not (0 <= b["block_type"] < n) or b["block_type"] in at["skip_types"]:
            errs.append("building %s: bad block_type" % b["key"])
        if bin(int(b["kind_mask"], 16)).count("1") != 1:
            errs.append("building %s: kind_mask must be one bit" % b["key"])
        if not COLOR_RE.match(b["fallback_marker"]["color"]) or not b["fallback_marker"]["label"]:
            errs.append("building %s: fallback marker needs label + #RRGGBB" % b["key"])
        keys.add(b["key"])

    def kinds(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("marker", "fallback_marker", "acre_highlight") and v.get("kind") not in MAP_MARKER_KINDS:
                    errs.append("map marker kind %r not in %s" % (v.get("kind"), sorted(MAP_MARKER_KINDS)))
                kinds(v)
        elif isinstance(o, list):
            for v in o:
                kinds(v)

    kinds(m)
    if len(keys) != len(m["buildings"]["indicators"]):
        errs.append("duplicate building keys")
    p = m["player"]
    if p["marker"]["kind"] != "original_marker":
        errs.append("player marker must be an original marker")
    if p["chain"][0]["addr"] != "0x812F31B8" or p["chain"][1]["equals"] != "0x8062B370":
        errs.append("player chain must start at bases.gamePT / play_main")
    # the addresses read back live (README §13)
    live = {"g_block_type_p": at["table"]["pointer_addr"], "l_block_type": at["table"]["expected_pointer"],
            "g_block_kind_p": m["buildings"]["block_kinds"]["pointer_addr"],
            "l_block_kind": m["buildings"]["block_kinds"]["expected_pointer"],
            "data_combi_table": at["from_save"]["data_combi_table"]["addr"],
            "l_map_texture": tx["pointer_table"]["addr"], "l_map_pal": pal["selector_table"]["addr"],
            "pluss_bridge": at["pluss_bridge"]["addr"], "l_kan_tizu_pal": pal["pointer_table"]["addr"],
            "kan_tizu1_pal": pal["expected_pointers"][0], "kan_tizu2_pal": pal["expected_pointers"][1],
            "kan_win_yane_tex": vh["addr"],
            "mMP_house_pos_list": m["villager_houses"]["slot_rule"]["house_pos_list"]["addr"]}
    live.update({"kan_win_npc2T_%d_model" % (t["tier"] + 1): t["display_list_addr"]
                 for t in m["villager_houses"]["tiers"]})
    for name, addr in MAP_VERIFIED_LIVE.items():
        if int(live[name], 16) != addr:
            errs.append("map %s does not reproduce verified address 0x%08X" % (name, addr))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decomp", default=DEFAULT_DECOMP)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    spec = build(a.decomp)
    errs = validate(spec)
    if errs:
        print("\n".join(errs))
        return 1
    text = json.dumps(spec, indent=1, ensure_ascii=True) + "\n"
    if a.check:
        with open(OUT, encoding="ascii") as f:
            same = f.read() == text
        print("up to date" if same else "DIFFERS from generated output")
        return 0 if same else 1
    with open(OUT, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    print("wrote %s: %d globals, %d player fields, %d villager fields, %d item ranges"
          % (OUT, len(spec["globals"]), len(spec["players"]["fields"]), len(spec["villagers"]["fields"]),
             len(spec["item_names"]["ranges"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
