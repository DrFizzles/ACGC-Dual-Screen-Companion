"""Read-only EmuLink (EMLKV2) UDP server for testing without Dolphin.

Serves either a MEM1 dump (``--dump mem1.raw``) or a synthetic 24 MB image
built from the spec with made-up sample values (``--synthetic``).  The wire
format mirrors dolphin-lnk's EmuLinkServer.cpp, including its 65000-byte batch
reply buffer.  Write packets are counted and ignored.

    python mock_server.py --synthetic [--spec PATH] [--port 55355] [--no-map]
    python mock_server.py --dump path/to/mem1.raw

The synthetic image contains no Nintendo data: every name in it is invented,
and the town map's acre images, palettes and house icon are made-up patterns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from emulink import (BATCH_MAGIC, DEFAULT_PORT, HANDSHAKE_MAGIC, MAX_PAYLOAD, MEM1_SIZE,  # noqa: E402
                     SERVER_REPLY_BUFFER, MemoryImage)
import acmap  # noqa: E402

RECV_BUFFER = MAX_PAYLOAD + 8   # the real server's receive buffer


class MockEmuLinkServer:
    """EmuLink server on a background thread.  ``port=0`` picks a free port.

    ``reply_limit`` emulates the server's batch reply buffer.  With
    ``overflow="faithful"`` an entry that does not fit is answered with len 0
    (what EmuLinkServer.cpp does while 2 bytes still fit); ``"truncate"`` stops
    at the first entry that does not fit, so ``actual`` < ``count``.
    """

    def __init__(self, image: bytes | bytearray, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 game_id: str | None = None, game_hash: str | None = None, platform: str = "GCN",
                 reply_limit: int = SERVER_REPLY_BUFFER, overflow: str = "faithful"):
        self.image = MemoryImage(image)
        self.game_id = game_id if game_id is not None else \
            bytes(image[0:6]).split(b"\0", 1)[0].decode("ascii", "replace")
        self.game_hash = game_hash if game_hash is not None else \
            hashlib.md5(bytes(image[0:0x100]), usedforsecurity=False).hexdigest()
        self.platform = platform
        self.reply_limit = reply_limit
        self.overflow = overflow
        self.stats = {"handshakes": 0, "single": 0, "batch": 0, "writes": 0, "dropped": 0,
                      "max_reply": 0, "overflowed_entries": 0}
        self._drop_next = 0
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        except OSError:
            pass
        self._sock.bind((host, port))
        self._sock.settimeout(0.1)
        self.host, self.port = self._sock.getsockname()[:2]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- control --------------------------------------------------------------

    def start(self) -> "MockEmuLinkServer":
        self._thread = threading.Thread(target=self._loop, name="mock-emulink", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._sock.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def drop_next(self, n: int = 1) -> None:
        """Silently ignore the next ``n`` requests (simulated packet loss)."""
        with self._lock:
            self._drop_next += n

    def serve_forever(self) -> None:
        self._loop()

    # -- protocol -------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                packet, sender = self._sock.recvfrom(65535)
            except (socket.timeout, TimeoutError):
                continue
            except ConnectionResetError:
                continue   # Windows: ICMP unreachable from a client that went away
            except OSError:
                if self._stop.is_set():
                    break
                continue
            reply = self.handle(packet)
            if reply is not None:
                try:
                    self._sock.sendto(reply, sender)
                except OSError:
                    pass

    def handle(self, packet: bytes) -> bytes | None:
        """Return the reply for one request datagram (None = no reply)."""
        with self._lock:
            if self._drop_next > 0:
                self._drop_next -= 1
                self.stats["dropped"] += 1
                return None
        n = len(packet)
        if n > RECV_BUFFER:
            self.stats["dropped"] += 1   # too big for the real server's buffer
            return None

        if n == 6 and packet == HANDSHAKE_MAGIC:
            self.stats["handshakes"] += 1
            return json.dumps({"emulator": "dolphin", "game_id": self.game_id,
                               "game_hash": self.game_hash, "platform": self.platform},
                              separators=(",", ":")).encode()

        if n >= 4 and packet[:2] == BATCH_MAGIC:
            count = struct.unpack_from("<H", packet, 2)[0]
            if 0 < count <= 256 and n >= 4 + count * 8:
                self.stats["batch"] += 1
                return self._batch(packet, count)
            # otherwise fall through, exactly like the real server

        if n >= 8:
            addr, size = struct.unpack_from("<II", packet, 0)
            if n != 8:
                self.stats["writes"] += 1   # read-only mock: never applied
                return None
            self.stats["single"] += 1
            if size > MAX_PAYLOAD:
                return None                  # the real server stays silent
            return self.image.read_or_zeros(addr, size)
        return None

    def _batch(self, packet: bytes, count: int) -> bytes:
        out = bytearray(BATCH_MAGIC + b"\0\0")
        actual = 0
        for i in range(count):
            addr, size = struct.unpack_from("<II", packet, 4 + i * 8)
            size = min(size, MAX_PAYLOAD)
            phys = self.image.check(addr, size)
            fits = len(out) + 2 + size <= self.reply_limit
            if phys is not None and fits:
                out += struct.pack("<H", size) + self.image.data[phys:phys + size]
            elif phys is None or self.overflow == "faithful":
                if len(out) + 2 > self.reply_limit:
                    break
                if phys is not None:
                    self.stats["overflowed_entries"] += 1   # valid but reported as len 0
                out += b"\0\0"
            else:
                break   # "truncate": stop here, the client must re-request the rest
            actual += 1
        struct.pack_into("<H", out, 2, actual)
        self.stats["max_reply"] = max(self.stats["max_reply"], len(out))
        return bytes(out)


# --------------------------------------------------------------------------- #
# Synthetic image
# --------------------------------------------------------------------------- #

GAME_STRUCT_ADDR = 0x81700000      # fake GAME struct (end of MEM1, unused by the spec)
FOREIGN_PRIVATE_ADDR = 0x81700100  # a now_private target outside the players array
NOT_PLAY_MAIN = 0x80600000         # some other GAME.exec (e.g. title screen)

# Invented names only -- nothing here comes from the game.
MOCK_ITEM_NAMES = ["Mock Lamp", "Test Pebble", "Sample Fern", "Dummy Chair", "Fake Hat",
                   "Proto Kettle", "Stub Rug", "Demo Clock", "Beta Vase", "Alpha Mug"]
MOCK_VILLAGER_NAME = "Mocky"
MOCK_NEIGHBOUR_NAMES = ["Abbo", "Mocky", "Corrin", "Dalby", "Evvo", "Fennick", "Gilby", "Hollo"]   # invented
SCENARIOS = ["town", "title", "title_demo", "null_game", "visiting", "rebuilding", "no_player", "wrong_game"]
TOWN_ID = 0x305C   # (id & 0xFF00) == 0x3000, like a real land id

SAMPLE = {
    "town_name": "Maple",
    "scene_no": 7,
    "rtc": dict(rtc_sec=22, rtc_min=5, rtc_hour=13, rtc_day=1, rtc_weekday=4, rtc_month=10, rtc_year=2026),
    "weather": 1,
    "weather_intensity": 2,
    "kabu_prices": [100, 92, 87, 140, 310, 75, 60],
    "kabu_trade_market": 0,
    "players": [
        dict(name="Ana", wallet=12345, bank=67890, loan=0, gender=1, face=3, exists=1),
        dict(name="Bo", wallet=500, bank=0, loan=39800, gender=0, face=1, exists=1),
    ],
    "villagers": [0xE001, 0xE00A, 0xE02F],
    "villager_cloth": 0x2400,
}


def _pack_value(spec: acmap.Spec, f: acmap.Field, value) -> bytes:
    if f.type == "str":
        rev = {}
        for i, ch in enumerate(spec.charmap):
            rev.setdefault(ch, i)
        space = rev.get(" ", 0x20)
        raw = bytes(rev.get(ch, space) for ch in str(value))[:f.length]
        return raw + bytes([space]) * (f.length - len(raw))
    if f.elem:
        fmt = acmap.SCALARS[f.elem][0][1]
        vals = list(value)[:f.count] + [0] * max(0, f.count - len(value))
        return struct.pack(">" + fmt * f.count, *vals)
    return struct.pack(acmap.SCALARS[f.type][0], value)


def _put(image: bytearray, addr: int, data: bytes) -> None:
    off = addr & 0x01FFFFFF
    image[off:off + len(data)] = data


def build_synthetic_image(spec: acmap.Spec, scenario: str = "town",
                          with_map: bool = False) -> tuple[bytearray, dict]:
    """Build a 24 MB MEM1 image from ``spec`` and return ``(image, expected)``.

    Scenarios: ``town`` (normal), ``title`` (GAME.exec != play_main),
    ``title_demo`` (play_main runs but scene_no is a not-in-town scene),
    ``null_game`` (gamePT == 0), ``visiting`` (player_no 4, now_private outside
    the players array), ``rebuilding`` (now_private NULL and town_id 0, as while
    a foreign save rebuilds common_data), ``no_player`` (current player's
    exists == 0), ``wrong_game``.
    ``expected`` holds the values the decoder should report.  With
    ``with_map`` (and a spec with a ``map`` section) the image also holds a
    made-up town map (:func:`add_synthetic_map`); ``expected["map"]`` describes it.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    image = bytearray(MEM1_SIZE)
    cd = spec.bases["common_data"]

    game_id = "GAFP01" if scenario == "wrong_game" else spec.game_id
    _put(image, spec.id_addr, game_id.encode("ascii"))
    _put(image, spec.revision_addr, bytes([spec.revision]))

    game_ptr = 0 if scenario == "null_game" else GAME_STRUCT_ADDR
    _put(image, spec.bases["gamePT"], struct.pack(">I", game_ptr))
    exec_ptr = NOT_PLAY_MAIN if scenario == "title" else spec.bases["play_main"]
    _put(image, GAME_STRUCT_ADDR + 4, struct.pack(">I", exec_ptr))

    def put_global(key, value):
        f = spec.global_field(key)
        if f is not None:
            _put(image, spec.global_addr(f), _pack_value(spec, f, value))

    # Item names: two invented names per range, written into the range's table.
    # Several ranges may share one table entry; the first name written there wins.
    names: dict[int, str] = {}
    written: dict[int, str] = {}
    for r in spec.name_ranges:
        for k in range(2):
            item_id = r.id_min + (k << r.shift)
            if item_id > r.id_max:
                break
            addr = r.table_addr + ((item_id - r.id_min) >> r.shift) * r.entry_len
            if addr not in written:
                n = len(written)
                name = MOCK_ITEM_NAMES[n % len(MOCK_ITEM_NAMES)]
                if n >= len(MOCK_ITEM_NAMES):
                    name += f" {n // len(MOCK_ITEM_NAMES) + 1}"
                name_field = acmap.Field(key="n", offset=0, type="str", size=r.entry_len, length=r.entry_len)
                _put(image, addr, _pack_value(spec, name_field, name))
                written[addr] = name
            names[item_id] = written[addr]
    # item_names.runtime_check: the pointer table the client verifies first.
    if spec.names_check:
        addr, expected_ptrs = spec.names_check
        _put(image, addr, struct.pack(f">{len(expected_ptrs)}I", *expected_ptrs))

    unknown_id = next(i for i in (0x9ABC, 0x7123, 0x5A5A, 0xFFF0)
                      if spec.find_name_range(i) is None and i not in spec.empty_ids)
    named = list(names)
    empty_id = min(spec.empty_ids) if spec.empty_ids else 0
    pockets = [named[0], named[1 % len(named)], empty_id, unknown_id] if named else [unknown_id]
    pockets += named[2:6]
    pockets += [empty_id] * (15 - len(pockets))
    pockets = pockets[:15]
    conditions = (1 << 2) | (2 << 6)   # slot 2 wrapped, slot 4 quest
    fruit_id = next((i for i in names if 0x2800 <= i <= 0x2807), named[0] if named else unknown_id)
    scene_no = min(spec.not_in_town_scenes) if scenario == "title_demo" and spec.not_in_town_scenes         else SAMPLE["scene_no"]

    current = 0
    players_base = spec.players.record_addr(spec.bases, 0)
    now_private = {"visiting": FOREIGN_PRIVATE_ADDR, "rebuilding": 0}.get(scenario, players_base)
    town_id = 0 if scenario == "rebuilding" else TOWN_ID
    player_rows = []
    for idx, sample in enumerate(SAMPLE["players"]):
        values = dict(sample, town_name=SAMPLE["town_name"],
                      pockets=pockets if idx == current else [empty_id] * 15,
                      item_conditions=conditions if idx == current else 0,
                      equipment=named[1 % len(named)] if named and idx == current else empty_id,
                      shirt=SAMPLE["villager_cloth"], land_id=TOWN_ID)
        if scenario == "no_player" and idx == current:
            values["exists"] = 0
        rec = spec.players.record_addr(spec.bases, idx)
        for f in spec.players.fields:
            if f.key in values:
                _put(image, rec + f.offset, _pack_value(spec, f, values[f.key]))
        player_rows.append(values)

    put_global("town_name", SAMPLE["town_name"])
    put_global("town_id", town_id)
    put_global("scene_no", scene_no)
    put_global("town_fruit", fruit_id)
    player_no = 4 if scenario in ("visiting", "rebuilding") else current
    put_global("player_no", player_no)
    for key, value in SAMPLE["rtc"].items():
        put_global(key, value)
    put_global("now_private", now_private)
    put_global("weather", SAMPLE["weather"])
    put_global("weather_intensity", SAMPLE["weather_intensity"])
    put_global("kabu_prices", SAMPLE["kabu_prices"])
    put_global("kabu_trade_market", SAMPLE["kabu_trade_market"])

    cloth_id = SAMPLE["villager_cloth"]
    for idx, npc_id in enumerate(SAMPLE["villagers"]):
        rec = spec.villagers.record_addr(spec.bases, idx)
        for f in spec.villagers.fields:
            if f.key == "npc_id":
                _put(image, rec + f.offset, _pack_value(spec, f, npc_id))
            elif f.key == "cloth":
                _put(image, rec + f.offset, _pack_value(spec, f, cloth_id))

    # extras.npc_name_cache: the last villager name the game looked up (invented).
    npc_name = None
    if spec.npc_cache:
        npc_name = MOCK_VILLAGER_NAME
        cache_values = {"npc_id": SAMPLE["villagers"][0], "name": npc_name, "npc_type": 1}
        for f in spec.npc_cache.fields:
            if f.key in cache_values:
                _put(image, spec.npc_cache.addr + f.offset, _pack_value(spec, f, cache_values[f.key]))
        # The lookup buffer: the 8 names of name ids b..b+7 (b = name_id & 0xFC), the cached
        # villager's in its slot, invented ones around it.
        if spec.npc_cache.dma_addr is not None:
            nid = SAMPLE["villagers"][0] & 0xFF
            base = nid & 0xFC
            for j, filler in enumerate(MOCK_NEIGHBOUR_NAMES):
                nm = npc_name if base + j == nid else filler
                _put(image, spec.npc_cache.dma_addr + 8 * j, nm.encode("ascii").ljust(8, b" "))

    expected = {
        "scenario": scenario,
        "game_id": game_id,
        "game_ptr": game_ptr,
        "now_private": now_private,
        "player_index": current,
        "globals": {
            "town_name": SAMPLE["town_name"], "scene_no": scene_no,
            "player_no": player_no, "town_id": town_id,
            "now_private": now_private, "weather": SAMPLE["weather"],
            "weather_intensity": SAMPLE["weather_intensity"],
            "kabu_prices": SAMPLE["kabu_prices"], **SAMPLE["rtc"],
        },
        "player": player_rows[current],
        "pockets": pockets,
        "conditions": conditions,
        "item_names": names,
        "unknown_id": unknown_id,
        "empty_id": empty_id,
        "villagers": SAMPLE["villagers"],
        "villager_cloth": cloth_id,
        "town_fruit": fruit_id,
        "npc_name": npc_name,
    }
    if with_map and spec.raw.get("map"):
        expected["map"] = add_synthetic_map(image, spec)
    return image, expected


# --------------------------------------------------------------------------- #
# Synthetic town map: made-up acre images, palettes, house icon and tables,
# written at the addresses the spec's "map" section gives.
# --------------------------------------------------------------------------- #

MAP_ACTOR_ADDR = 0x81710000        # fake player actor (end of MEM1, unused by the spec)
MAP_FAKE_PALETTES = 0x81720000     # palette data if the spec gives no expected pointers

# Building acres of the synthetic town (block_x, block_z); keys are indicator keys.
SYN_BUILDINGS = {"dump": (2, 1), "post_office": (4, 1), "station": (3, 1), "shop": (1, 2),
                 "police": (4, 2), "player_houses": (3, 3), "wishing_well": (2, 4),
                 "museum": (5, 5), "tailor": (1, 6), "dock": (4, 6)}
# Villager homes: (villager slot, block_x, block_z, ut_x, ut_z, house height y)
SYN_HOMES = [(0, 2, 2, 5, 9, 280.0), (1, 4, 4, 10, 3, 160.0), (2, 5, 3, 8, 12, 40.0)]
# Player actor: block (2, 3) = acre C-2, tile (7, 2), facing north.
SYN_PLAYER = {"x": 1580.0, "y": 280.0, "z": 2020.0, "facing": 0x8000}

# Palette indices of the synthetic acre images.
SYN_IX_TRANSPARENT, SYN_IX_SEPARATOR, SYN_IX_OUTSIDE, SYN_IX_MARK = 0, 1, 14, 15


def synthetic_palette(variant: int) -> list[int]:
    """16 RGB5A3 values: 0 transparent, 1 separator, 2-13 acre bodies,
    14 only outside the visible crop, 15 the orientation mark (white)."""
    pal = [0x0000, 0x8000 | (2 << 10) | (9 << 5) | 3]
    for i in range(2, 14):
        r, g, b = (i * 5) % 32, (31 - i * 2) % 32, (i * 3 + 7) % 32
        if variant:
            r, b = b, r
        pal.append(0x8000 | (r << 10) | (g << 5) | b)
    pal += [0x8000 | (31 << 10) | 31, 0xFFFF]
    return pal


def synthetic_acre_index(acre_type: int, x: int, y: int, vis_w: int, vis_h: int) -> int:
    """Palette index of texel (x, y) of the synthetic image for ``acre_type``."""
    if x >= vis_w or y >= vis_h:
        return SYN_IX_OUTSIDE
    if x == 0 or y == 0:
        return SYN_IX_SEPARATOR
    if 2 <= x <= 4 and 2 <= y <= 4:
        return SYN_IX_MARK          # near the top-left corner: catches flips
    return 2 + acre_type % 12


def synthetic_icon_value(x: int, y: int) -> int:
    """IA4 byte of the synthetic house icon (16x16): a diamond with a dark centre."""
    inside = abs(x - 7.5) + abs(y - 7.5) <= 7
    centre = 6 <= x <= 9 and 6 <= y <= 9
    return ((15 if inside else 0) << 4) | (0 if centre else 15)


def gx_tile_order(values: list[int], width: int, height: int, block_w: int, block_h: int) -> list[int]:
    """Row-major pixel values -> GX tile order (padding pixels are 0)."""
    out = []
    for ty in range(0, height, block_h):
        for tx in range(0, width, block_w):
            for y in range(ty, ty + block_h):
                for x in range(tx, tx + block_w):
                    out.append(values[y * width + x] if x < width and y < height else 0)
    return out


def add_synthetic_map(image: bytearray, spec: acmap.Spec, homes=SYN_HOMES, player=SYN_PLAYER) -> dict:
    """Write a made-up town map at the spec's map addresses; return what a reader should see."""
    m = spec.raw["map"]
    P = acmap.parse_int

    def addr_of(d):
        return P(d["addr"]) if d.get("addr") is not None else spec.bases[d["base"]] + P(d["offset"])

    def u16(a, v):
        _put(image, a, struct.pack(">H", v))

    g = m["grid"]
    cols, rows, fbx, fbz = g["cols"], g["rows"], g["first_block_x"], g["first_block_z"]
    bcols, brows = g["block_cols"], g["block_rows"]
    units = g["acre_map_units"]
    nblocks = bcols * brows

    def label(bx, bz):
        return f"{g['row_labels'][bz - fbz]}-{g['col_labels'][bx - fbx]}"

    # ---- acre types and building kinds
    at = m["acre_types"]
    type_count, pad = at["type_count"], at["pad_type"]
    skip = set(at.get("skip_types") or [])
    border = min(skip) if skip else 0
    inds = {i["key"]: i for i in m["buildings"]["indicators"]}
    building_types = {P(i["block_type"]) for i in inds.values() if i.get("block_type") is not None}
    generic = [t for t in range(type_count) if t not in skip and t not in building_types and t != pad]

    types = [border] * nblocks
    kinds = [0] * nblocks
    buildings = {}
    n = 0
    for bz in range(fbz, fbz + rows):
        for bx in range(fbx, fbx + cols):
            i = bz * bcols + bx
            key = next((k for k, b in SYN_BUILDINGS.items() if b == (bx, bz) and k in inds), None)
            if key:
                types[i], kinds[i] = P(inds[key]["block_type"]), P(inds[key]["kind_mask"])
                buildings[key] = label(bx, bz)
            else:
                types[i] = generic[(n * 5) % len(generic)]
                n += 1

    tab = at["table"]
    _put(image, P(tab["pointer_addr"]), struct.pack(">I", P(tab["expected_pointer"])))
    _put(image, P(tab["expected_pointer"]), bytes(types[:tab["count"]]))

    dct = at["from_save"]["data_combi_table"]
    dct_addr, elen = P(dct["addr"]), dct["entry_len"]
    for k in range(dct["count"]):     # entry k: made-up bg/fg ids, type k % type_count
        base = dct_addr + k * elen
        u16(base + P(dct["bg_id_offset"]), 0x0100 + k)
        u16(base + P(dct["fg_id_offset"]), 0x2000 + k)
        _put(image, base + P(dct["type_offset"]), bytes([k % type_count]))
    combi = [t << 2 for t in types]   # combination k = type, height 0 ...
    combi[0] |= 2                     # ... except block 0: a 3-step town (tier rule)
    _put(image, addr_of(at["from_save"]["combi_table"]), struct.pack(f">{len(combi)}H", *combi))
    if at.get("pluss_bridge"):
        pb = at["pluss_bridge"]
        _put(image, P(pb["addr"]), bytes([P(pb["none_value"])]) * pb["count"])

    # ---- acre images (C4) and palettes
    tx = m["acre_texture"]
    start, size = P(tx["valid_range"]["start"]), tx["size"]
    tw, th = tx["width"], tx["height"]
    vis = tx.get("visible") or {}
    vw, vh = vis.get("width", tw), vis.get("height", th)
    used = sorted({types[bz * bcols + bx] for bz in range(fbz, fbz + rows) for bx in range(fbx, fbx + cols)}
                  | {pad})
    ptrs = [0] * tx["pointer_table"]["count"]
    for slot, t in enumerate(used):
        ptrs[t] = start + slot * size
        vals = [synthetic_acre_index(t, x, y, vw, vh) for y in range(th) for x in range(tw)]
        tiled = gx_tile_order(vals, tw, th, 8, 8)          # C4: 8x8 tiles, high nibble first
        _put(image, ptrs[t], bytes((tiled[j] << 4) | tiled[j + 1] for j in range(0, len(tiled), 2)))
    _put(image, P(tx["pointer_table"]["addr"]), struct.pack(f">{len(ptrs)}I", *ptrs))
    pal = tx["palette"]
    sel = pal["selector_table"]
    _put(image, P(sel["addr"]), bytes(t % 2 for t in range(sel["count"])))
    pal_ptrs = [P(x) for x in pal.get("expected_pointers") or []] or \
        [MAP_FAKE_PALETTES + 0x20 * k for k in range(pal["pointer_table"]["count"])]
    _put(image, P(pal["pointer_table"]["addr"]), struct.pack(f">{len(pal_ptrs)}I", *pal_ptrs))
    palettes = [synthetic_palette(k) for k in range(len(pal_ptrs))]
    for p, values in zip(pal_ptrs, palettes):
        _put(image, p, struct.pack(">16H", *values))

    bk = m["buildings"]["block_kinds"]
    _put(image, P(bk["pointer_addr"]), struct.pack(">I", P(bk["expected_pointer"])))
    _put(image, P(bk["expected_pointer"]), struct.pack(f">{bk['count']}I", *kinds[:bk["count"]]))

    # ---- villager houses: icon (IA4), tier colours, house position list, homes
    vh_ = m["villager_houses"]
    mk = vh_["marker"]
    icon = gx_tile_order([synthetic_icon_value(x, y) for y in range(mk["height"]) for x in range(mk["width"])],
                         mk["width"], mk["height"], 8, 4)    # IA4: 8x4 tiles, 1 byte per pixel
    _put(image, P(mk["addr"]), bytes(icon))
    tiers = {}
    for t in vh_["tiers"]:
        dl = P(t["display_list_addr"])
        po, eo = P(t["prim_offset"]), P(t["env_offset"])
        prim = tuple(t.get("expected_prim") or (40 + 60 * t["tier"], 90, 200))
        env = tuple(t.get("expected_env") or (225, 225, 225))
        if po >= 4:
            _put(image, dl + po - 4, bytes([0xFA, 0, 0, 0]))   # set-prim-colour opcode word
        if eo >= 4:
            _put(image, dl + eo - 4, bytes([0xFB, 0, 0, 0]))   # set-env-colour opcode word
        _put(image, dl + po, bytes(prim) + b"\xff")
        _put(image, dl + eo, bytes(env) + b"\xff")
        tiers[t["tier"]] = (prim, env)

    # ---- the player's map figure: the made-up icon pattern in the spec's expected colours
    pi = m["player"].get("icon")
    if pi:
        _put(image, P(pi["addr"]), bytes(icon))
        dl = P(pi["display_list_addr"])
        _put(image, dl + P(pi["prim_offset"]), bytes(pi["expected_prim"]) + bytes([0xFF]))
        _put(image, dl + P(pi["env_offset"]), bytes(pi["expected_env"]) + bytes([0xFF]))

    sr = vh_["slot_rule"]
    hl = sr["house_pos_list"]
    hl_addr, hlen = P(hl["addr"]), hl["entry_len"]
    so, slen = P(hl["slots_offset"]), hl["slot_len"]
    sf = hl["slot_fields"]

    def entry(e, name, slots):
        base = hl_addr + e * hlen
        u16(base + P(hl["fg_name_offset"]), name)
        for j, (ux, uz, idx) in enumerate(slots):
            raw = bytearray(slen)
            raw[sf["ut_x"]], raw[sf["ut_z"]], raw[sf["idx"]] = ux & 0xFF, uz & 0xFF, idx
            _put(image, base + so + j * slen, bytes(raw))

    term_index = hl.get("terminator_index", hl["count"] - 1)
    for e in range(term_index):        # filler names never used by the synthetic acres
        entry(e, 0x1000 + e, [(e % 16, (e + 1) % 16, e % 9)] * hl["slot_count"])
    entry(0, 0x1000, [(0, 0, 4), (1, 1, 5), (2, 2, 6)])
    entry(term_index, P(hl["terminator"]), [(0, 0, 0)] * hl["slot_count"])

    vil = spec.villagers
    hy = vh_["tier_rule"]["house_y"]
    step3 = (combi[0] & 3) == 2
    houses = []
    fg_of = {}
    for slot, bx, bz, ux, uz, y in homes:
        rec = vil.record_addr(spec.bases, slot)
        for key, value in (("home_block_x", bx), ("home_block_z", bz), ("home_ut_x", ux), ("home_ut_z", uz)):
            fld = vil.field(key)
            if fld is not None:
                _put(image, rec + fld.offset, _pack_value(spec, fld, value))
        _put(image, addr_of(hy) + slot * P(hy["stride"]) + P(hy["field_offset"]), struct.pack(">f", y))
        fg_of[slot] = 0x2000 + types[bz * bcols + bx]
        layer = 2 if y < 100 else ((1 if y < 220 else 0) if step3 else 1)
        houses.append({"slot": slot, "block": (bx, bz), "acre": label(bx, bz),
                       "tier": layer if step3 else max(layer - 1, 0)})
    h0, h1, h2 = homes[0], homes[1], homes[2]
    # home 0: exact slot in its first matching entry (a later duplicate entry must be ignored)
    entry(5, fg_of[h0[0]], [(1, 1, 0), (h0[3], h0[4] - 1, 7), (3, 3, 2)])
    entry(20, fg_of[h0[0]], [(h0[3], h0[4] - 1, 1)] * hl["slot_count"])
    # home 1: its acre has an entry but no matching slot -> slot 0 of that entry
    entry(9, fg_of[h1[0]], [(9, 9, 2), (0, 0, 3), (1, 1, 5)])
    # home 2: no entry for its acre -> slot 0 of entry 0 (idx 4)
    spots = {h0[0]: (7, True), h1[0]: (2, False), h2[0]: (4, False)}
    xo, yo = sr["x_offsets"], sr["y_offsets"]
    for h in houses:
        idx, exact = spots[h["slot"]]
        bx, bz = h["block"]
        h.update(spot=idx, exact=exact, map=((bx - fbx) * units + xo[idx % 3], (bz - fbz) * units + yo[idx // 3]))

    # ---- player: chain from GAME, actor header, position, facing, field type
    pl = m["player"]
    ptr_values = {}
    for st in pl["chain"]:
        if st.get("addr") is not None:
            ptr_values[st["step"]] = GAME_STRUCT_ADDR          # gamePT (written by the scenario)
            continue
        a = ptr_values[st["from"]] + P(st["offset"])
        size = acmap.SCALARS[st["type"]][1]
        if st is pl["chain"][-1]:
            value = MAP_ACTOR_ADDR
        elif st.get("equals") is not None:
            off = a & 0x01FFFFFF
            if any(image[off:off + size]):
                continue                                   # e.g. GAME.exec, set by the scenario
            value = P(st["equals"])
        else:
            value = max(1, P(st.get("min", 1)))
        _put(image, a, struct.pack(acmap.SCALARS[st["type"]][0], value))
        if st["type"] == "ptr":
            ptr_values[st["step"]] = value
    for c in pl.get("actor_checks") or []:
        _put(image, MAP_ACTOR_ADDR + P(c["offset"]), struct.pack(acmap.SCALARS[c["type"]][0], P(c["equals"])))
    pos = pl["position"]
    for key, v in (("x_offset", player["x"]), ("y_offset", player["y"]), ("z_offset", player["z"])):
        _put(image, MAP_ACTOR_ADDR + P(pos[key]), struct.pack(">f", v))
    facing = player["facing"] - 0x10000 if player["facing"] >= 0x8000 else player["facing"]
    _put(image, MAP_ACTOR_ADDR + P(pl["facing"]["offset"]), struct.pack(">h", facing))
    ft = pl["show_when"].get("field_type")
    if ft:
        _put(image, addr_of(ft), struct.pack(acmap.SCALARS[ft.get("type", "u8")][0], P(ft["equals"])))

    wpa = pl["transform"]["world_units_per_acre"]
    pbx, pbz = int(player["x"] / wpa), int(player["z"] / wpa)
    cells = [(types[bz * bcols + bx], bx, bz) for bz in range(fbz, fbz + rows) for bx in range(fbx, fbx + cols)]
    return {
        "types": types, "kinds": kinds, "combi": combi, "cells": cells, "used_types": used,
        "texture_ptrs": {t: ptrs[t] for t in used}, "palettes": palettes, "palette_ptrs": pal_ptrs,
        "buildings": buildings, "houses": sorted(houses, key=lambda h: h["slot"]), "tiers": tiers,
        "visible": (vw, vh),
        "player": {"acre": label(pbx, pbz), "block": (pbx, pbz),
                   "tile": (int(player["x"] / (wpa / 16)) - pbx * 16, int(player["z"] / (wpa / 16)) - pbz * 16),
                   "map": ((player["x"] / wpa - fbx) * units, (player["z"] / wpa - fbz) * units),
                   "facing": player["facing"], "world": (player["x"], player["y"], player["z"])},
        "actor_addr": MAP_ACTOR_ADDR,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only mock EmuLink server (EMLKV2 over UDP).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dump", metavar="PATH", help="serve a MEM1 dump (Dolphin 'Dump MRAM' -> mem1.raw)")
    src.add_argument("--synthetic", action="store_true", help="serve a synthetic image built from the spec")
    ap.add_argument("--scenario", default="town", choices=SCENARIOS,
                    help="synthetic image variant (default: town)")
    ap.add_argument("--spec", help=f"spec path (default: {acmap.DEFAULT_SPEC_PATH})")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port (default {DEFAULT_PORT})")
    ap.add_argument("--save-image", metavar="PATH", help="also write the served image to PATH (mem1.raw format)")
    ap.add_argument("--no-map", action="store_true", help="synthetic image without the made-up town map")
    args = ap.parse_args(argv)

    if args.dump:
        image = bytearray(Path(args.dump).read_bytes())
        what = f"dump {args.dump}"
    else:
        try:
            spec = acmap.load_spec(args.spec)
        except acmap.SpecError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        image, _expected = build_synthetic_image(spec, args.scenario, with_map=not args.no_map)
        what = f"synthetic image ({args.scenario}) from {spec.path}"
    if args.save_image:
        Path(args.save_image).write_bytes(image)
        print(f"wrote {args.save_image}")

    server = MockEmuLinkServer(image, host=args.host, port=args.port)
    print(f"mock EmuLink serving {what} on udp://{server.host}:{server.port} "
          f"(game_id {server.game_id}); Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        print(f"stopped; stats: {server.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
