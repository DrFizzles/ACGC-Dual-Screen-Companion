"""Animal Crossing (GAFE01 rev 0) memory map loader and live-state decoder.

All offsets come from the shared spec (``spec/ac_memory_map.json``); nothing
about the game's layout is hard-coded here.  Names (town, players, items,
villagers) are decoded from emulated RAM at runtime using the spec's charmap.

Typical use::

    spec = load_spec()                 # or load_spec(path)
    reader = ACReader(source, spec)    # source: EmuLinkClient / MemoryImageSource
    state = reader.poll()              # plain dict, JSON-serialisable

Reads: fields close to each other are merged into one span, and every span is
read twice in the same batch (validity rule 8), so a steady-state poll is one
UDP request.  A second one is needed only to follow a new ``GAME`` pointer,
to fetch item names that are not cached yet, or to re-read a span whose two
copies differed.
"""

from __future__ import annotations

import json
import re
import struct
import time
import zlib
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from emulink import EmuLinkError

DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[2] / "spec" / "ac_memory_map.json"

PTR_MIN = 0x80000000
PTR_MAX = 0x817FFFFF   # last byte of MEM1 (24 MB)

SCALARS = {
    "u8": (">B", 1), "s8": (">b", 1),
    "u16": (">H", 2), "s16": (">h", 2),
    "u32": (">I", 4), "s32": (">i", 4),
    "f32": (">f", 4), "ptr": (">I", 4),
}

REQUIRED = {
    "globals": ["scene_no", "player_no", "now_private", "town_name", "rtc_sec", "rtc_min",
                "rtc_hour", "rtc_day", "rtc_weekday", "rtc_month", "rtc_year", "weather",
                "weather_intensity", "kabu_prices"],
    "players": ["name", "town_name", "exists", "pockets", "item_conditions", "wallet", "loan", "bank"],
    "villagers": ["npc_id"],
    "enums": ["weather", "weekday", "scene_no"],
}

# Validity rule 8: impossible values are rejected (reported as None plus a
# warning).  Applies to globals and player fields with these keys unless the
# spec gives its own "min"/"max" for the field.
PLAUSIBLE = {
    "rtc_sec": (0, 59), "rtc_min": (0, 59), "rtc_hour": (0, 23), "rtc_day": (1, 31),
    "rtc_weekday": (0, 6), "rtc_month": (1, 12),
    "wallet": (0, 99999), "kabu_prices": (0, 2000),
}

MERGE_GAP = 128    # reads at most this many bytes apart share one span
MAX_SPAN = 4096    # one batch entry (the server's limit)

# Status codes in GameState["status"]
ST_DISCONNECTED = "disconnected"
ST_WRONG_GAME = "wrong_game"
ST_NOT_IN_TOWN = "not_in_town"
ST_VISITING = "visiting"
ST_NO_PLAYER = "no_player"
ST_IN_TOWN = "in_town"


class SpecError(Exception):
    pass


def parse_int(value: Any) -> int | None:
    """Accept ints, hex strings ("0x1F") and decimal strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip(), 0)
    raise SpecError(f"not an integer: {value!r}")


def is_ptr(value: int | None) -> bool:
    return value is not None and PTR_MIN <= value <= PTR_MAX


# --------------------------------------------------------------------------- #
# Spec model
# --------------------------------------------------------------------------- #

@dataclass
class Field:
    key: str
    offset: int
    type: str
    size: int
    elem: str | None = None      # element type for arrays
    count: int | None = None     # element count for arrays
    length: int | None = None    # byte length for str
    base: str | None = None      # globals only
    enum: str | None = None
    slot_enum: str | None = None  # packed per-slot values (item_conditions)
    item_id: bool = False         # value(s) are item ids: resolve names
    min: int | None = None
    max: int | None = None
    status: str | None = None
    note: str | None = None
    mask: int | None = None
    shift: int = 0

    @classmethod
    def from_json(cls, obj: dict) -> "Field":
        key = obj.get("key")
        if not key:
            raise SpecError(f"field without key: {obj!r}")
        typ = str(obj.get("type", "")).strip()
        base = obj.get("base")
        if obj.get("addr") is not None:   # absolute address instead of base + offset
            base, offset = None, parse_int(obj["addr"])
        else:
            offset = parse_int(obj.get("offset", 0))
        common = dict(key=key, offset=offset, type=typ, base=base,
                      enum=obj.get("enum"), slot_enum=obj.get("slot_enum"),
                      item_id=bool(obj.get("item_id", False)),
                      min=parse_int(obj.get("min")), max=parse_int(obj.get("max")),
                      status=obj.get("status"), note=obj.get("note"),
                      mask=parse_int(obj.get("mask")), shift=parse_int(obj.get("shift")) or 0)
        if typ in SCALARS:
            return cls(size=SCALARS[typ][1], **common)
        if typ == "str":
            length = parse_int(obj.get("len"))
            if not length or length <= 0:
                raise SpecError(f"{key}: str needs a positive len")
            return cls(size=length, length=length, **common)
        m = re.fullmatch(r"(\w+)\[(\d*)\]", typ)
        if m and m.group(1) in SCALARS:
            count = parse_int(obj.get("count")) if obj.get("count") is not None else None
            if count is None and m.group(2):
                count = int(m.group(2))
            if not count or count <= 0:
                raise SpecError(f"{key}: array needs a positive count")
            elem = m.group(1)
            return cls(size=SCALARS[elem][1] * count, elem=elem, count=count, **common)
        raise SpecError(f"{key}: unsupported type {typ!r}")


@dataclass
class Group:
    """An array of records (players, villagers)."""
    base: str
    offset: int
    stride: int
    count: int
    fields: list[Field]

    def record_addr(self, bases: dict[str, int], index: int) -> int:
        return bases[self.base] + self.offset + index * self.stride

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


@dataclass
class NameRange:
    id_min: int
    id_max: int
    table_addr: int
    shift: int
    entry_len: int
    note: str = ""


@dataclass
class Block:
    """A small fixed struct outside the record groups (extras.npc_name_cache).

    ``dma_addr``/``dma_len``: the name-lookup buffer read together with the cache (optional)."""
    addr: int
    fields: list[Field]
    dma_addr: int | None = None
    dma_len: int = 0

    @property
    def read_start(self) -> int:
        """First byte of the one contiguous read that covers the buffer and the cache."""
        if self.dma_addr is not None and 0 < self.addr - self.dma_addr <= 256:
            return self.dma_addr
        return self.addr

    @property
    def size(self) -> int:
        return max(f.offset + f.size for f in self.fields)

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


@dataclass
class Spec:
    path: str | None
    raw: dict
    game_id: str
    id_addr: int
    revision: int
    revision_addr: int
    bases: dict[str, int]
    globals: list[Field]
    players: Group | None
    villagers: Group | None
    enums: dict[str, dict[int, str]]
    charmap: list[str]
    name_ranges: list[NameRange]
    empty_ids: set[int]
    rules: list[str]
    not_in_town_scenes: set[int] = dc_field(default_factory=set)
    names_check: tuple[int, list[int]] | None = None   # (pointer table addr, expected values)
    npc_cache: Block | None = None
    warnings: list[str] = dc_field(default_factory=list)

    # -- helpers --------------------------------------------------------------

    def global_field(self, key: str) -> Field | None:
        return next((f for f in self.globals if f.key == key), None)

    def global_addr(self, f: Field) -> int:
        base = self.bases.get(f.base, 0) if f.base else 0
        return (base + f.offset) & 0xFFFFFFFF

    def enum_label(self, enum_name: str | None, value: Any) -> str | None:
        if not enum_name or not isinstance(value, int):
            return None
        return self.enums.get(enum_name, {}).get(value)

    def decode_str(self, raw: bytes) -> str:
        # Names are space padded.  0x00-0x1F are glyphs in this charset, so we
        # never stop at NUL; we only trim trailing spaces.
        return "".join(self.charmap[b] for b in raw).rstrip(" ")

    def decode(self, f: Field, raw: bytes) -> Any:
        if len(raw) != f.size:
            return None
        if f.type == "str":
            return self.decode_str(raw)
        if f.elem:
            fmt, _ = SCALARS[f.elem]
            return [self._post(f, v) for v in struct.unpack(">" + fmt[1] * f.count, raw)]
        fmt, _ = SCALARS[f.type]
        return self._post(f, struct.unpack(fmt, raw)[0])

    @staticmethod
    def _post(f: Field, v: Any) -> Any:
        if f.mask is not None and isinstance(v, int):
            v = (v & f.mask) >> f.shift
        return v

    def find_name_range(self, item_id: int) -> NameRange | None:
        for r in self.name_ranges:
            if r.id_min <= item_id <= r.id_max:
                return r
        return None

    def name_entry(self, item_id: int) -> tuple[int, int] | None:
        """(address, length) of the name-table entry for ``item_id``."""
        r = self.find_name_range(item_id)
        if r is None:
            return None
        index = (item_id - r.id_min) >> r.shift
        return (r.table_addr + index * r.entry_len) & 0xFFFFFFFF, r.entry_len

    def missing_required(self) -> list[str]:
        missing = []
        have_g = {f.key for f in self.globals}
        missing += [f"globals.{k}" for k in REQUIRED["globals"] if k not in have_g]
        for name in ("players", "villagers"):
            grp = getattr(self, name)
            have = {f.key for f in grp.fields} if grp else set()
            missing += [f"{name}.fields.{k}" for k in REQUIRED[name] if k not in have]
        missing += [f"enums.{k}" for k in REQUIRED["enums"] if k not in self.enums]
        return missing


def _ascii_charmap() -> list[str]:
    return [chr(i) if 0x20 <= i < 0x7F else "?" for i in range(256)]


def parse_spec(obj: dict, path: str | None = None) -> Spec:
    if not isinstance(obj, dict):
        raise SpecError("spec root must be an object")
    warnings: list[str] = []
    if obj.get("schema") != 1:
        warnings.append(f"unexpected spec schema {obj.get('schema')!r} (expected 1)")

    game = obj.get("game") or {}
    bases = {k: parse_int(v) for k, v in (obj.get("bases") or {}).items()}

    def fields_of(items, where, plausible=False):
        out = []
        for item in items or []:
            try:
                f = Field.from_json(item)
            except (SpecError, ValueError) as exc:
                warnings.append(f"{where}: skipped field: {exc}")
                continue
            if plausible and f.min is None and f.max is None and f.key in PLAUSIBLE:
                f.min, f.max = PLAUSIBLE[f.key]
            out.append(f)
        return out

    gfields = fields_of(obj.get("globals"), "globals", plausible=True)
    for f in gfields:
        if f.base and f.base not in bases:
            warnings.append(f"globals.{f.key}: unknown base {f.base!r}")

    def group(name):
        g = obj.get(name)
        if not g:
            return None
        grp = Group(base=g.get("base", "common_data"), offset=parse_int(g.get("offset", 0)),
                    stride=parse_int(g.get("stride")), count=parse_int(g.get("count")),
                    fields=fields_of(g.get("fields"), name, plausible=(name == "players")))
        if grp.base not in bases:
            raise SpecError(f"{name}: unknown base {grp.base!r}")
        return grp

    enums: dict[str, dict[int, str]] = {}
    for ename, table in (obj.get("enums") or {}).items():
        enums[ename] = {}
        for k, v in table.items():
            try:
                enums[ename][parse_int(k)] = str(v)
            except (SpecError, ValueError):
                warnings.append(f"enums.{ename}: bad key {k!r}")

    charmap = obj.get("charmap")
    if not isinstance(charmap, list) or len(charmap) != 256:
        warnings.append("charmap missing or not 256 entries; using ASCII fallback")
        charmap = _ascii_charmap()
    charmap = [str(c) for c in charmap]

    names = obj.get("item_names") or {}
    entry_len = parse_int(names.get("entry_len", 16))
    ranges = []
    for r in names.get("ranges") or []:
        ranges.append(NameRange(id_min=parse_int(r["id_min"]), id_max=parse_int(r["id_max"]),
                                table_addr=parse_int(r["table_addr"]),
                                shift=parse_int(r.get("shift", 0)) or 0,
                                entry_len=parse_int(r.get("entry_len", entry_len)),
                                note=r.get("note", "")))
    empty_ids = {parse_int(x) for x in names.get("empty_ids", ["0x0000"])}

    names_check = None
    check = names.get("runtime_check")
    if check:
        try:
            names_check = (parse_int(check["pointer_table_addr"]),
                           [parse_int(x) for x in check["expected"]])
            if not names_check[1]:
                names_check = None
        except (KeyError, TypeError, ValueError, SpecError) as exc:
            warnings.append(f"item_names.runtime_check ignored: {exc}")

    npc_cache = None
    nc = (obj.get("extras") or {}).get("npc_name_cache")
    if nc:
        cfields = fields_of(nc.get("fields"), "extras.npc_name_cache")
        keys = {f.key for f in cfields}
        if nc.get("addr") and {"npc_id", "name"} <= keys:
            dma = parse_int(nc.get("dma_area_addr"))
            npc_cache = Block(addr=parse_int(nc["addr"]), fields=cfields, dma_addr=dma,
                              dma_len=parse_int(nc.get("dma_area_len")) or 0 if dma is not None else 0)
        else:
            warnings.append("extras.npc_name_cache needs addr and npc_id/name fields; ignored")

    validity = obj.get("validity") or {}
    for key in ("common_data", "gamePT", "play_main"):
        if key not in bases:
            raise SpecError(f"bases.{key} missing")

    spec = Spec(
        path=path, raw=obj,
        game_id=str(game.get("id", "GAFE01")),
        id_addr=parse_int(game.get("id_addr", "0x80000000")),
        revision=parse_int(game.get("revision", 0)),
        revision_addr=parse_int(game.get("revision_addr", "0x80000007")),
        bases=bases, globals=gfields,
        players=group("players"), villagers=group("villagers"),
        enums=enums, charmap=charmap, name_ranges=ranges, empty_ids=empty_ids,
        rules=list(validity.get("rules") or []),
        not_in_town_scenes={parse_int(x) for x in validity.get("not_in_town_scenes") or []},
        names_check=names_check, npc_cache=npc_cache,
        warnings=warnings,
    )
    missing = spec.missing_required()
    if missing:
        spec.warnings.append("spec is missing required keys: " + ", ".join(missing))
    return spec


def load_spec(path: str | Path | None = None) -> Spec:
    p = Path(path) if path else DEFAULT_SPEC_PATH
    try:
        with open(p, encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        raise SpecError(f"spec not found: {p}") from None
    except json.JSONDecodeError as exc:
        raise SpecError(f"spec {p} is not valid JSON: {exc}") from None
    return parse_spec(obj, str(p))


# --------------------------------------------------------------------------- #
# Read planning
# --------------------------------------------------------------------------- #

def coalesce(reads: list[tuple[Any, int, int]], max_gap: int = MERGE_GAP,
             max_span: int = MAX_SPAN) -> tuple[list[tuple[int, int]], dict[Any, tuple[int, int]]]:
    """Merge ``(tag, addr, size)`` reads into spans.

    Returns ``(spans, where)``: ``spans`` is a list of ``(addr, size)`` and
    ``where[tag] = (span index, offset in span)``.  Reads at most ``max_gap``
    bytes apart share a span; no span is longer than ``max_span``.
    """
    spans: list[tuple[int, int]] = []
    where: dict[Any, tuple[int, int]] = {}
    for tag, addr, size in sorted(reads, key=lambda r: (r[1], r[2])):
        if spans:
            s_addr, s_size = spans[-1]
            end = max(s_addr + s_size, addr + size)
            if addr <= s_addr + s_size + max_gap and end - s_addr <= max_span:
                spans[-1] = (s_addr, end - s_addr)
                where[tag] = (len(spans) - 1, addr - s_addr)
                continue
        spans.append((addr, size))
        where[tag] = (len(spans) - 1, 0)
    return spans, where


def read_stable(batch, reads: list[tuple[Any, int, int]]) -> tuple[dict[Any, bytes], list]:
    """Read ``(tag, addr, size)`` items (validity rule 8).

    ``batch(spans)`` must return one bytes object per ``(addr, size)`` span
    (``b''`` if unreadable), like ``source.batch_read``.  Reads are merged into
    spans and each span is read twice in one batch.  An item is accepted when
    its bytes agree in both copies; spans with a disagreeing item are re-read
    (twice) once more.  Returns ``({tag: bytes}, unstable_tags)``; unreadable
    or still-unstable items map to ``b''``.
    """
    if not reads:
        return {}, []
    spans, where = coalesce(reads)

    def twice(sp):
        res = batch(sp + sp)
        return list(zip(res[:len(sp)], res[len(sp):]))

    pairs = twice(spans)

    def piece(tag, size):
        i, off = where[tag]
        a, b = pairs[i]
        return a[off:off + size], b[off:off + size]

    retry = sorted({where[tag][0] for tag, _a, size in reads if len(set(piece(tag, size))) > 1})
    if retry:
        for i, pair in zip(retry, twice([spans[i] for i in retry])):
            pairs[i] = pair
    out: dict[Any, bytes] = {}
    unstable = []
    for tag, _addr, size in reads:
        a, b = piece(tag, size)
        if a != b:
            unstable.append(tag)
        out[tag] = a if a == b and len(a) == size else b""
    return out, unstable


NAME_PUNCT = " '.-&!"
NPC_NAME_TYPE_VILLAGER = 1    # mNpc_NAME_TYPE_NPC


IDENTITY_CODE_ADDR = 0x80003100   # first text section of a GameCube DOL (static once booted)
IDENTITY_CODE_LEN = 0x1000


def _blank(data: bytes) -> bool:
    """All 0x00 or all 0xFF: cleared or unmapped memory, not a real string."""
    return not data or data.count(0) == len(data) or data.count(0xFF) == len(data)


def _tag_name(tag: Any) -> str:
    if isinstance(tag, tuple) and tag[0] == "g":
        return tag[1]
    if isinstance(tag, tuple) and len(tag) == 3:
        return f"{tag[0]}[{tag[1]}].{tag[2]}"
    if isinstance(tag, tuple) and tag[0] == "name":
        return f"name 0x{tag[1]:04X}"
    return str(tag)


# --------------------------------------------------------------------------- #
# Live reader
# --------------------------------------------------------------------------- #

class ACReader:
    """Polls a memory source and turns it into a GameState dict."""

    def __init__(self, source, spec: Spec, handshake_interval: float = 10.0):
        self.source = source
        self.spec = spec
        self.handshake_interval = handshake_interval
        self.name_cache: dict[int, str | None] = {}   # item id -> name (None: no name)
        self.npc_names: dict[int, str] = {}           # villager npc_id -> name (harvested)
        self._cache_key: tuple | None = None
        self._handshake: dict | None = None
        self._handshake_time = 0.0
        self._last_game_ptr: int | None = None
        self._round_trips = 0

    # -- reads ----------------------------------------------------------------

    def _call(self, fn, *args):
        """Call the source and count request/reply exchanges (UDP transactions)."""
        before = getattr(self.source, "transactions", None)
        result = fn(*args)
        after = getattr(self.source, "transactions", None)
        self._round_trips += (after - before) if isinstance(before, int) else 1
        return result

    def _read_stable(self, reads: list[tuple[Any, int, int]]) -> tuple[dict[Any, bytes], list]:
        """:func:`read_stable` through this reader's source (counts round trips)."""
        return read_stable(lambda spans: self._call(self.source.batch_read, spans), reads)

    def _maybe_handshake(self) -> dict:
        """Identify the game image from memory: the game id plus a CRC32 of the first text section.

        This replaces the EmuLink "EMLKV2" handshake. Its first use makes dolphin-lnk hash boot.dol
        by reading the disc image from the server thread, which is not thread-safe (DiscIO
        Blob::Read) and crashed Dolphin when it raced the game's own disc reads during boot."""
        now = time.monotonic()
        if self._handshake is None or now - self._handshake_time >= self.handshake_interval:
            spec = self.spec
            data = self._call(self.source.batch_read, [(spec.id_addr, len(spec.game_id)),
                                                       (IDENTITY_CODE_ADDR, IDENTITY_CODE_LEN)])
            raw_id, code = (data + [b"", b""])[:2]
            game_id = "".join(chr(b) if 0x20 <= b <= 0x7E else "?" for b in (raw_id or b""))
            self._handshake = {"emulator": "memory", "game_id": game_id,
                               "game_hash": "%08x" % zlib.crc32(code or b""), "platform": "GCN"}
            self._handshake_time = now
        return self._handshake

    def _set_cache_key(self, key: tuple) -> None:
        if key != self._cache_key:
            self.name_cache.clear()
            self.npc_names.clear()
            self._cache_key = key

    def _groups(self):
        return [(n, g) for n, g in (("players", self.spec.players), ("villagers", self.spec.villagers)) if g]

    # -- main entry -------------------------------------------------------------

    def poll(self) -> dict:
        spec = self.spec
        self._round_trips = 0
        state = _empty_state(spec, self.source)
        state["warnings"] += spec.warnings
        try:
            self._poll_into(state)
        except EmuLinkError as exc:
            # Force a fresh handshake next time (the game may have changed).
            self._handshake = None
            self._last_game_ptr = None
            state.update(_empty_state(spec, self.source), warnings=state["warnings"],
                         connected=False, status=ST_DISCONNECTED, message=str(exc))
        state["round_trips"] = self._round_trips
        return state

    def _poll_into(self, state: dict) -> None:
        spec = self.spec
        hs = self._maybe_handshake()
        state["connected"] = True
        state["handshake"] = hs
        state["game"]["hash"] = hs.get("game_hash")

        # ---- batch 1: identity, game pointer, every field --------------------
        reads: list[tuple[Any, int, int]] = [
            ("id", spec.id_addr, len(spec.game_id)),
            ("rev", spec.revision_addr, 1),
            ("gamept", spec.bases["gamePT"], 4),
        ]
        spec_ptr = self._last_game_ptr
        if is_ptr(spec_ptr):
            reads.append(("exec", spec_ptr + 4, 4))   # speculative: same GAME as last poll
        if spec.names_check:   # 64 bytes; read every poll so a game change is caught
            addr, expected = spec.names_check
            reads.append(("names_check", addr, 4 * len(expected)))
        if spec.npc_cache:
            nc = spec.npc_cache
            reads.append(("npc_cache", nc.read_start, nc.addr - nc.read_start + nc.size))
        reads += [(("g", f.key), spec.global_addr(f), f.size) for f in spec.globals]
        for gname, grp in self._groups():
            for i in range(grp.count):
                rec = grp.record_addr(spec.bases, i)
                reads += [((gname, i, f.key), (rec + f.offset) & 0xFFFFFFFF, f.size)
                          for f in grp.fields]
        raw, unstable = self._read_stable(reads)

        # ---- rule 1: right game ----------------------------------------------
        game_id = raw["id"].decode("ascii", "replace") if raw["id"] else None
        revision = raw["rev"][0] if raw["rev"] else None
        state["game"].update(id=game_id, revision=revision)
        self._set_cache_key((hs.get("game_hash"), game_id, revision))
        if game_id != spec.game_id or revision != spec.revision:
            state["game"]["ok"] = False
            state.update(status=ST_WRONG_GAME,
                         message=f"Unsupported game {game_id!r} rev {revision} "
                                 f"(need {spec.game_id} rev {spec.revision})")
            self._last_game_ptr = None
            return
        state["game"]["ok"] = True

        g = {f.key: spec.decode(f, raw[("g", f.key)]) for f in spec.globals}
        records = {gname: [{f.key: spec.decode(f, raw[(gname, i, f.key)]) for f in grp.fields}
                           for i in range(grp.count)]
                   for gname, grp in self._groups()}
        players = records.get("players", [])
        villagers = records.get("villagers", [])

        # ---- provisional rules 2-5, to decide whether names are worth fetching
        game_ptr = _u32(raw["gamept"])
        need_exec = is_ptr(game_ptr) and game_ptr != spec_ptr
        exec_ptr = _u32(raw.get("exec")) if is_ptr(game_ptr) and not need_exec else None
        play_main = spec.bases["play_main"]
        scene = g.get("scene_no")
        scene_ok = isinstance(scene, int) and scene not in spec.not_in_town_scenes
        maybe_town = is_ptr(game_ptr) and (need_exec or exec_ptr == play_main) and scene_ok

        player_index, now_private = self._current_player(g)
        player = players[player_index] if player_index is not None else None
        resident = player is not None and player.get("exists") == 1
        names_ok = self._check_names(raw.get("names_check"))

        to_fetch: list[int] = []
        if maybe_town and names_ok:
            wanted = _item_ids(spec.globals, g)
            for v in villagers:
                if _valid_npc(v.get("npc_id")):
                    wanted |= _item_ids(spec.villagers.fields, v)
            if resident:
                wanted |= _item_ids(spec.players.fields, player)
            to_fetch = sorted(i for i in wanted if i not in spec.empty_ids
                              and i not in self.name_cache and spec.name_entry(i) is not None)

        # ---- batch 2 (only when needed): follow a new GAME pointer, names -----
        reads2: list[tuple[Any, int, int]] = []
        if need_exec:
            reads2.append(("exec", game_ptr + 4, 4))
        for item_id in to_fetch:
            addr, length = spec.name_entry(item_id)
            reads2.append((("name", item_id), addr, length))
        raw2, unstable2 = self._read_stable(reads2)
        if need_exec:
            exec_ptr = _u32(raw2["exec"])

        self._last_game_ptr = game_ptr if is_ptr(game_ptr) else None
        state.update(game_ptr=game_ptr, exec_ptr=exec_ptr)

        # ---- rule 2: in gameplay (GAME.exec == play_main) ----------------------
        if not is_ptr(game_ptr) or exec_ptr != play_main:
            state.update(status=ST_NOT_IN_TOWN, message="Not in town (title screen, menu or loading)")
            return
        # ---- rule 5: title demo / player select / intro also run play_main ------
        if not scene_ok:
            enum = spec.global_field("scene_no").enum if spec.global_field("scene_no") else None
            what = spec.enum_label(enum, scene) or ("scene unreadable" if scene is None else "scene")
            state.update(status=ST_NOT_IN_TOWN, message=f"Not in town ({what}, scene {scene})")
            return
        # ---- rule 3(c): neither a resident slot nor a valid visitor -------------
        # (NULL/torn now_private, e.g. while a foreign-save load rebuilds common_data)
        if player_index is None and not self._is_visiting(g, now_private):
            state.update(status=ST_NOT_IN_TOWN, now_private=now_private,
                         message="Not in town (player data not ready)")
            return

        # Static name tables are only cached in gameplay, after the table check.
        for item_id in to_fetch:
            if ("name", item_id) in unstable2:
                continue
            data = raw2.get(("name", item_id), b"")
            name = "" if _blank(data) else spec.decode_str(data)   # rule 9: trailing spaces only
            self.name_cache[item_id] = name or None
        self._harvest_npc_name(raw.get("npc_cache", b""), villagers)

        # ---- rule 3: resident player? (else visiting: town info only) ----------
        # ---- rule 4: that player's record must be in use (exists == 1) ---------
        if player_index is not None and not resident:
            state.update(status=ST_NO_PLAYER, player_index=player_index,
                         message=f"Not in town (player slot {player_index + 1} is not in use: "
                                 f"exists={player.get('exists')!r})")
            return

        warnings = state["warnings"]
        warnings += _apply_bounds(spec.globals, g, "")
        if not names_ok:
            warnings.append("item name tables not at the expected addresses "
                            "(item_names.runtime_check failed); showing item ids only")
        shown = [t for t in unstable if isinstance(t, tuple) and (
            t[0] in ("g", "villagers") or (resident and t[0] == "players" and t[1] == player_index))]
        if shown:
            names = ", ".join(_tag_name(t) for t in shown[:6]) + (" ..." if len(shown) > 6 else "")
            warnings.append(f"values kept changing while being read, shown as unknown: {names}")

        state["in_gameplay"] = True
        state["globals"] = g
        state["labels"] = self._labels(spec.globals, g)
        state["villagers"] = self._villagers(villagers)
        state["players"] = self._residents(players)
        state["now_private"] = now_private

        if player_index is None:
            state.update(status=ST_VISITING, message="Visiting / foreign player: showing town info only")
            return

        pno = g.get("player_no")
        if isinstance(pno, int) and pno != player_index:
            warnings.append(f"player_no={pno} disagrees with now_private slot {player_index}")
        warnings += _apply_bounds(spec.players.fields, player, "player.")
        state["player_index"] = player_index
        state["player"] = self._player(player_index, player)
        state["pockets"] = self._pockets(player)
        state.update(status=ST_IN_TOWN, message=f"In town as player {player_index + 1}")

    # -- pieces -------------------------------------------------------------------

    def _check_names(self, data: bytes | None) -> bool:
        """item_names.runtime_check: the item name pointer table must hold the
        expected values before any table_addr is trusted (and names fetched)."""
        if not self.spec.names_check:
            return True
        _addr, expected = self.spec.names_check
        return bool(data) and len(data) == 4 * len(expected) and \
            list(struct.unpack(f">{len(expected)}I", data)) == expected

    def _name_ok(self, raw: bytes) -> str | None:
        """A villager name: not blank, and only letters, digits, spaces and ' . - & !"""
        if _blank(raw):
            return None
        name = self.spec.decode_str(raw)
        if not name or any(not (ch.isalnum() or ch in NAME_PUNCT) for ch in name):
            return None
        return name

    def _harvest_npc_name(self, data: bytes, villagers: list[dict]) -> None:
        """extras.npc_name_cache holds the last villager name the game looked up.
        Keep it when its npc_id is one of this town's villagers, its npc_type says villager and
        (when the spec gives the lookup buffer) the buffer holds the same name in that id's slot;
        the buffer's other slots then name the neighbouring ids too."""
        cache = self.spec.npc_cache
        if not cache or not data:
            return
        off = cache.addr - cache.read_start
        c = data[off:off + cache.size]
        if len(c) < cache.size or _blank(c):
            return
        id_f, name_f, type_f = cache.field("npc_id"), cache.field("name"), cache.field("npc_type")
        npc_id = self.spec.decode(id_f, c[id_f.offset:id_f.offset + id_f.size])
        if type_f is not None and self.spec.decode(type_f, c[type_f.offset:type_f.offset + type_f.size]) != NPC_NAME_TYPE_VILLAGER:
            return
        name_raw = c[name_f.offset:name_f.offset + name_f.size]
        name = self._name_ok(name_raw)
        if not _valid_npc(npc_id) or name is None:
            return
        learned = {npc_id: name}
        if cache.read_start != cache.addr:
            dma = data[:cache.dma_len]
            nid = npc_id & 0xFF
            base = nid & 0xFC
            k = nid - base
            if dma[k * 8:k * 8 + 8] != name_raw[:8]:
                return   # stale or torn cache
            for j in range(len(dma) // 8):
                nm = self._name_ok(dma[j * 8:j * 8 + 8])
                if nm and base + j < 0xFF:
                    learned.setdefault(0xE000 | (base + j), nm)
        residents = {v.get("npc_id") for v in villagers if _valid_npc(v.get("npc_id"))}
        for vid, nm in learned.items():
            if vid in residents:
                self.npc_names[vid] = nm

    def _current_player(self, g: dict) -> tuple[int | None, int | None]:
        spec = self.spec
        now_private = g.get("now_private")
        grp = spec.players
        if grp is None or not isinstance(now_private, int):
            return None, now_private
        first = grp.record_addr(spec.bases, 0)
        end = first + grp.count * grp.stride
        if first <= now_private < end and (now_private - first) % grp.stride == 0:
            return (now_private - first) // grp.stride, now_private
        return None, now_private

    def _is_visiting(self, g: dict, now_private: Any) -> bool:
        """Validity rule 3(b): player_no == 4 (mPr_FOREIGNER), now_private is a MEM1
        pointer and town_id looks like a real town id ((id & 0xFF00) == 0x3000).
        The town_id test is skipped when the spec does not define town_id."""
        if g.get("player_no") != 4 or not is_ptr(now_private):
            return False
        if self.spec.global_field("town_id") is None:
            return True
        town_id = g.get("town_id")
        return isinstance(town_id, int) and (town_id & 0xFF00) == 0x3000

    def _residents(self, players: list[dict]) -> list[dict]:
        """Validity rule 10: a slot is occupied iff (land_id & 0xFF00) == 0x3000.
        ``travelling`` marks an occupied slot whose character is away (exists != 1).
        Without a land_id field, fall back to exists == 1."""
        has_land = self.spec.players is not None and self.spec.players.field("land_id") is not None
        out = []
        for i, p in enumerate(players):
            land_id = p.get("land_id")
            occupied = (isinstance(land_id, int) and (land_id & 0xFF00) == 0x3000) if has_land \
                else p.get("exists") == 1
            if occupied:
                out.append({"index": i, "name": p.get("name"), "travelling": p.get("exists") != 1})
        return out

    def item_name(self, item_id: Any) -> str | None:
        """Cached name for ``item_id`` (None if empty, unknown or not loaded yet)."""
        if not isinstance(item_id, int) or item_id in self.spec.empty_ids:
            return None
        return self.name_cache.get(item_id)

    def _labels(self, fields: list[Field], rec: dict) -> dict:
        """Display strings: enum labels and names of scalar item-id fields."""
        out = {}
        for f in fields:
            if f.enum:
                out[f.key] = self.spec.enum_label(f.enum, rec.get(f.key))
            elif f.item_id and not f.elem:
                out[f.key] = self.item_name(rec.get(f.key))
        return out

    def _player(self, index: int, rec: dict) -> dict:
        out = {"index": index}
        out.update(rec)
        out["labels"] = self._labels(self.spec.players.fields, rec)
        return out

    def _pockets(self, rec: dict) -> list[dict]:
        spec = self.spec
        pockets = rec.get("pockets") or []
        conds = rec.get("item_conditions")
        f = spec.players.field("item_conditions")
        cond_enum = (f and (f.slot_enum or f.enum)) or \
            ("item_condition" if "item_condition" in spec.enums else None)
        out = []
        for slot, item_id in enumerate(pockets):
            cond = (conds >> (slot * 2)) & 3 if isinstance(conds, int) else None
            empty = item_id in spec.empty_ids
            out.append({
                "slot": slot + 1,
                "id": item_id,
                "id_hex": f"0x{item_id:04X}",
                "empty": empty,
                "name": None if empty else self.item_name(item_id),
                "condition": cond,
                "condition_label": spec.enum_label(cond_enum, cond),
            })
        return out

    def _villagers(self, recs: list[dict]) -> list[dict]:
        out = []
        for i, rec in enumerate(recs):
            npc_id = rec.get("npc_id")
            if not _valid_npc(npc_id):
                continue
            v = {"index": i}
            v.update(rec)
            v["npc_id_hex"] = f"0x{npc_id:04X}"
            v["name"] = self.npc_names.get(npc_id)
            v["labels"] = self._labels(self.spec.villagers.fields, rec)
            out.append(v)
        return out


def _valid_npc(npc_id: Any) -> bool:
    return isinstance(npc_id, int) and (npc_id & 0xF000) == 0xE000


def _u32(data: bytes | None) -> int | None:
    return struct.unpack(">I", data)[0] if data and len(data) == 4 else None


def _item_ids(fields: list[Field], rec: dict) -> set[int]:
    ids: set[int] = set()
    for f in fields:
        if f.item_id:
            v = rec.get(f.key)
            ids.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, int))
    return ids


def _apply_bounds(fields: list[Field], rec: dict, prefix: str) -> list[str]:
    """Validity rule 8: replace impossible values with None; return warnings."""
    def ok(v, f):
        return (f.min is None or v >= f.min) and (f.max is None or v <= f.max)

    bad = []
    for f in fields:
        if f.min is None and f.max is None:
            continue
        v = rec.get(f.key)
        if isinstance(v, list):
            if any(isinstance(x, (int, float)) and not ok(x, f) for x in v):
                bad.append(f"{prefix}{f.key}={v}")
                rec[f.key] = [x if isinstance(x, (int, float)) and ok(x, f) else None for x in v]
        elif isinstance(v, (int, float)) and not ok(v, f):
            bad.append(f"{prefix}{f.key}={v}")
            rec[f.key] = None
    return [f"impossible values rejected (torn read or wrong offset?): {', '.join(bad)}"] if bad else []


def _empty_state(spec: Spec, source) -> dict:
    return {
        "schema": 1,
        "time": time.time(),
        "source": source.describe() if hasattr(source, "describe") else str(source),
        "connected": False,
        "handshake": None,
        "game": {"id": None, "revision": None, "hash": None, "ok": False,
                 "expected_id": spec.game_id, "expected_revision": spec.revision},
        "status": ST_DISCONNECTED,
        "message": "",
        "in_gameplay": False,
        "game_ptr": None,
        "exec_ptr": None,
        "now_private": None,
        "player_index": None,
        "globals": {},
        "labels": {},
        "player": None,
        "players": [],
        "pockets": [],
        "villagers": [],
        "warnings": [],
        "round_trips": 0,
    }
