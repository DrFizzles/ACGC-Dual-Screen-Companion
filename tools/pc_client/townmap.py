"""Town map for the second screen: acre images, building indicators, player location.

Everything the map needs is described by the spec's ``map`` section
(``spec/ac_memory_map.json``, README section 13) and read from emulated RAM at
runtime:

* the 5 x 6 acre images (GX C4 textures plus an RGB5A3 palette), decoded with
  :mod:`gctex` and laid out the way the game's map screen does it;
* building indicators: the game draws the shop, post office, station, etc.
  *inside* the acre images, so they appear with the acre art.  Villager houses
  are separate icons, tinted per height tier, also decoded from RAM.  When an
  acre image fails validation, an original placeholder (coloured square and a
  short label) marks the building instead;
* the player: an original red dot with a facing arrow at the exact position,
  plus a box around the current acre.

Nothing else is read or drawn.  In particular the item/fg grid (buried spots,
fossils, money rocks, ground items) is never read.  Decoded images live only in
memory (and in the PNG the caller asks for); nothing is cached on disk.

Reads use the same rules as :mod:`acmap`: spans are read twice in one batch and
accepted when both copies agree (validity rule 8).  Static tables (texture and
palette pointers, house-icon data) are read once per game hash; the town layout
(acre types, building kinds, villager homes) when it changes or every
``layout_interval`` seconds; decoded acre images are cached per texture.  A
steady-state :meth:`TownMapReader.update` therefore reads only the player chain
and position: one UDP request.

Typical use::

    reader = acmap.ACReader(source, spec)
    tmap = TownMapReader(source, spec, scale=4)
    state = reader.poll()
    status = tmap.update(state)        # JSON-friendly dict
    png = tmap.render().png()          # RGBA image of the map
"""

from __future__ import annotations

import math
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import acmap
import gctex
from emulink import EmuLinkError

P = acmap.parse_int
is_ptr = acmap.is_ptr
Color = tuple[int, int, int, int]

# Algorithm constants that the spec states only in prose (map.villager_houses.tier_rule):
LAYER_LOW_Y = 100.0     # house y below this -> layer 2 (mCoBG_Height2GetLayer)
LAYER_MID_Y = 220.0     # below this -> layer 1 (3-step towns), else layer 0
STEP3_MASK, STEP3_VALUE = 3, 2   # (combi_table[0] & 3) == 2 -> 3-step town (mRF_CheckFieldStep3)
TILES_PER_ACRE = 16     # map.player.transform: 1 tile = 40 world units, 16 tiles per acre
FACING_LABELS = ("S", "SE", "E", "NE", "N", "NW", "W", "SW")   # 0 = south, 0x4000 = east

# Status codes (TownMapReader.update()["status"])
MS_OK = "ok"                   # layout shown; the player marker may still be hidden (see player.reason)
MS_STALE = "stale"             # not in gameplay: last layout of this game session, no marker
MS_NO_LAYOUT = "no_layout"     # nothing to show yet
MS_UNAVAILABLE = "unavailable"  # spec has no map section, or wrong game
MS_DISCONNECTED = "disconnected"

# Original colours of this companion's own drawing (frame, labels, placeholders).
COL_BACKGROUND = (250, 241, 196, 255)
COL_FRAME = (238, 128, 34, 255)
COL_FRAME_EDGE = (168, 78, 14, 255)
COL_GRID = (28, 70, 36, 255)             # behind transparent texels; separator fallback
COL_LABEL_COL = (40, 150, 56, 255)
COL_LABEL_ROW = (36, 128, 214, 255)
COL_LABEL_SHADOW = (60, 50, 30, 255)
COL_FALLBACK_ACRE = (120, 196, 110, 255)
COL_TEXT = (255, 255, 255, 255)
COL_TEXT_SHADOW = (20, 20, 20, 255)


class MapSpecError(Exception):
    pass


# The rendered map holds acre art and the house icon decoded from the game, so it
# must never be written into this project (it would end up committed or packaged).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProjectPathError(ValueError):
    """Raised for an output path at or under PROJECT_ROOT."""


def check_output_path(path, root: Path | None = None) -> Path:
    """Return ``path`` fully resolved (symlinks, ``..``, case on Windows), or raise
    :class:`ProjectPathError` if it is the project root or anywhere under it."""
    root = (root or PROJECT_ROOT).resolve()
    target = Path(os.fspath(path)).expanduser().resolve()
    norm = os.path.normcase
    root_s, target_s = norm(str(root)), norm(str(target))
    if target_s == root_s or target_s.startswith(root_s.rstrip(os.sep) + os.sep):
        raise ProjectPathError(f"refusing to write a map decoded from the game into the project "
                               f"({target} is under {root}); write it outside the project, "
                               f"for example to a temp folder")
    return target


# --------------------------------------------------------------------------- #
# Spec model
# --------------------------------------------------------------------------- #

def _color(value, where: str) -> Color:
    s = str(value).strip().lstrip("#")
    if len(s) != 6:
        raise MapSpecError(f"{where}: colour {value!r} is not #RRGGBB")
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255
    except ValueError:
        raise MapSpecError(f"{where}: colour {value!r} is not #RRGGBB") from None


@dataclass
class Indicator:
    key: str
    label: str
    mask: int
    block_type: int | None
    marker_kind: str
    fallback_label: str
    fallback_color: Color


@dataclass
class ChainStep:
    name: str
    type: str
    size: int
    addr: int | None = None      # absolute
    src: str | None = None       # or: value of an earlier step + offset
    offset: int = 0
    in_mem1: bool = False
    equals: int | None = None
    min: int | None = None


@dataclass
class Tier:
    tier: int
    dl_addr: int
    prim_offset: int
    env_offset: int
    color_len: int
    expected_prim: tuple | None
    expected_env: tuple | None


@dataclass
class SlotRule:
    addr: int
    entry_len: int
    count: int
    terminator: int
    terminator_index: int | None
    fg_name_offset: int
    slots_offset: int
    slot_count: int
    slot_len: int
    f_ut_x: int
    f_ut_z: int
    f_idx: int
    x_offsets: list[int]
    y_offsets: list[int]


class MapSpec:
    """The numbers of the spec's ``map`` section, parsed and checked once."""

    def __init__(self, raw: dict, spec: acmap.Spec):
        self.raw = raw
        bases = spec.bases

        def get(obj, key, where, default=None, required=True):
            if isinstance(obj, dict) and key in obj and obj[key] is not None:
                return obj[key]
            if required and default is None:
                raise MapSpecError(f"map.{where}.{key} missing")
            return default

        def num(obj, key, where, default=None, required=True):
            v = get(obj, key, where, default, required)
            if v is None:
                return None
            try:
                return P(v)
            except (ValueError, acmap.SpecError):
                raise MapSpecError(f"map.{where}.{key}: not a number: {v!r}") from None

        def addr(obj, where):
            if isinstance(obj, dict) and obj.get("addr") is not None:
                return num(obj, "addr", where)
            base = get(obj, "base", where)
            if base not in bases:
                raise MapSpecError(f"map.{where}: unknown base {base!r}")
            return (bases[base] + num(obj, "offset", where)) & 0xFFFFFFFF

        # ---- grid
        g = get(raw, "grid", "")
        self.cols, self.rows = num(g, "cols", "grid"), num(g, "rows", "grid")
        self.col_labels = [str(x) for x in get(g, "col_labels", "grid", [str(i + 1) for i in range(self.cols)])]
        self.row_labels = [str(x) for x in get(g, "row_labels", "grid",
                                               [chr(65 + i) for i in range(self.rows)])]
        if len(self.col_labels) != self.cols or len(self.row_labels) != self.rows:
            raise MapSpecError("map.grid: label count does not match cols/rows")
        self.first_bx, self.first_bz = num(g, "first_block_x", "grid"), num(g, "first_block_z", "grid")
        self.block_cols, self.block_rows = num(g, "block_cols", "grid"), num(g, "block_rows", "grid")
        self.units = num(g, "acre_map_units", "grid")
        self.blocks = self.block_cols * self.block_rows

        # ---- acre types
        at = get(raw, "acre_types", "")
        self.type_count = num(at, "type_count", "acre_types")
        t = get(at, "table", "acre_types")
        self.type_ptr_addr = num(t, "pointer_addr", "acre_types.table")
        self.type_ptr_expected = num(t, "expected_pointer", "acre_types.table", required=False)
        self.type_table_count = num(t, "count", "acre_types.table")
        fs = get(at, "from_save", "acre_types")
        ct = get(fs, "combi_table", "acre_types.from_save")
        self.combi_addr, self.combi_count = addr(ct, "acre_types.from_save.combi_table"), \
            num(ct, "count", "acre_types.from_save.combi_table")
        dc = get(fs, "data_combi_table", "acre_types.from_save")
        w = "acre_types.from_save.data_combi_table"
        self.dct_addr, self.dct_entry_len, self.dct_count = addr(dc, w), num(dc, "entry_len", w), num(dc, "count", w)
        self.dct_fg_offset, self.dct_type_offset = num(dc, "fg_id_offset", w), num(dc, "type_offset", w)
        self.skip_types = {P(x) for x in get(at, "skip_types", "acre_types", [], required=False)}
        self.pad_type = num(at, "pad_type", "acre_types")
        br = at.get("bridge")
        self.bridge = None
        if br:
            w = "acre_types.bridge"
            self.bridge = dict(addr=addr(br, w), bx=num(br, "block_x_offset", w), bz=num(br, "block_z_offset", w),
                               flags=num(br, "flags_offset", w), mask=num(br, "exists_mask", w))
            self.bridge["size"] = max(self.bridge["bx"], self.bridge["bz"], self.bridge["flags"]) + 1
        pb = at.get("pluss_bridge")
        self.pluss = None
        if pb:
            w = "acre_types.pluss_bridge"
            self.pluss = dict(addr=num(pb, "addr", w), count=num(pb, "count", w), none=num(pb, "none_value", w))

        # ---- acre textures
        tx = get(raw, "acre_texture", "")
        pt = get(tx, "pointer_table", "acre_texture")
        self.tex_ptr_addr, self.tex_ptr_count = num(pt, "addr", "acre_texture.pointer_table"), \
            num(pt, "count", "acre_texture.pointer_table")
        vr = get(tx, "valid_range", "acre_texture")
        self.tex_start, self.tex_end = num(vr, "start", "acre_texture.valid_range"), num(vr, "end", "acre_texture.valid_range")
        try:
            self.tex_format = gctex.canonical_format(get(tx, "format", "acre_texture"))
        except gctex.TextureError as exc:
            raise MapSpecError(f"map.acre_texture: {exc}") from None
        self.tex_w, self.tex_h = num(tx, "width", "acre_texture"), num(tx, "height", "acre_texture")
        self.tex_size = num(tx, "size", "acre_texture")
        if self.tex_size != gctex.texture_size(self.tex_format, self.tex_w, self.tex_h):
            raise MapSpecError("map.acre_texture.size does not match format/width/height")
        vis = get(tx, "visible", "acre_texture", {}, required=False)
        self.vis = (num(vis, "x", "", 0), num(vis, "y", "", 0),
                    num(vis, "width", "", self.tex_w), num(vis, "height", "", self.tex_h))
        if self.vis[0] + self.vis[2] > self.tex_w or self.vis[1] + self.vis[3] > self.tex_h:
            raise MapSpecError("map.acre_texture.visible is outside the texture")
        pal = get(tx, "palette", "acre_texture")
        self.pal_format = str(get(pal, "format", "acre_texture.palette")).upper()
        if self.pal_format not in gctex.TLUT_FORMATS:
            raise MapSpecError(f"map.acre_texture.palette: unsupported format {self.pal_format!r}")
        self.pal_entries, self.pal_size = num(pal, "entries", "acre_texture.palette"), num(pal, "size", "acre_texture.palette")
        sel = get(pal, "selector_table", "acre_texture.palette")
        self.pal_sel_addr, self.pal_sel_count = num(sel, "addr", "palette.selector_table"), num(sel, "count", "palette.selector_table")
        ppt = get(pal, "pointer_table", "acre_texture.palette")
        self.pal_ptr_addr, self.pal_ptr_count = num(ppt, "addr", "palette.pointer_table"), num(ppt, "count", "palette.pointer_table")
        self.pal_expected = [P(x) for x in pal.get("expected_pointers") or []]

        # ---- buildings
        b = raw.get("buildings") or {}
        bk = b.get("block_kinds")
        self.kinds = None
        if bk:
            w = "buildings.block_kinds"
            self.kinds = dict(ptr=num(bk, "pointer_addr", w), expected=num(bk, "expected_pointer", w, required=False),
                              count=num(bk, "count", w))
        self.indicators: list[Indicator] = []
        for i, ind in enumerate(b.get("indicators") or []):
            w = f"buildings.indicators[{i}]"
            fb = ind.get("fallback_marker") or {}
            self.indicators.append(Indicator(
                key=str(get(ind, "key", w)), label=str(ind.get("label") or ind["key"]),
                mask=num(ind, "kind_mask", w), block_type=num(ind, "block_type", w, required=False),
                marker_kind=str((ind.get("marker") or {}).get("kind", "")),
                fallback_label=str(fb.get("label") or ind.get("label") or ind["key"]),
                fallback_color=_color(fb.get("color", "#808080"), w + ".fallback_marker")))

        # ---- villager houses
        vh = raw.get("villager_houses")
        self.houses = vh is not None
        if vh:
            mk = get(vh, "marker", "villager_houses")
            w = "villager_houses.marker"
            self.icon_addr = num(mk, "addr", w)
            try:
                self.icon_format = gctex.canonical_format(get(mk, "format", w))
            except gctex.TextureError as exc:
                raise MapSpecError(f"map.{w}: {exc}") from None
            self.icon_w, self.icon_h, self.icon_size = num(mk, "width", w), num(mk, "height", w), num(mk, "size", w)
            self.icon_units = num(mk, "map_size_units", w)
            self.tiers: list[Tier] = []
            for i, t in enumerate(get(vh, "tiers", "villager_houses")):
                w = f"villager_houses.tiers[{i}]"
                self.tiers.append(Tier(
                    tier=num(t, "tier", w), dl_addr=num(t, "display_list_addr", w),
                    prim_offset=num(t, "prim_offset", w), env_offset=num(t, "env_offset", w),
                    color_len=num(t, "color_len", w, 4),
                    expected_prim=tuple(t["expected_prim"][:3]) if t.get("expected_prim") else None,
                    expected_env=tuple(t["expected_env"][:3]) if t.get("expected_env") else None))
            self.tiers.sort(key=lambda t: t.tier)
            hy = get(get(vh, "tier_rule", "villager_houses"), "house_y", "villager_houses.tier_rule")
            self.house_y = dict(addr=addr(hy, "tier_rule.house_y"), stride=num(hy, "stride", "tier_rule.house_y"),
                                field=num(hy, "field_offset", "tier_rule.house_y"))
            sr = get(vh, "slot_rule", "villager_houses")
            hl = get(sr, "house_pos_list", "villager_houses.slot_rule")
            w = "villager_houses.slot_rule.house_pos_list"
            sf = get(hl, "slot_fields", w)
            self.slot_rule = SlotRule(
                addr=num(hl, "addr", w), entry_len=num(hl, "entry_len", w), count=num(hl, "count", w),
                terminator=num(hl, "terminator", w), terminator_index=num(hl, "terminator_index", w, required=False),
                fg_name_offset=num(hl, "fg_name_offset", w), slots_offset=num(hl, "slots_offset", w),
                slot_count=num(hl, "slot_count", w), slot_len=num(hl, "slot_len", w),
                f_ut_x=num(sf, "ut_x", w), f_ut_z=num(sf, "ut_z", w), f_idx=num(sf, "idx", w),
                x_offsets=[P(x) for x in get(sr, "x_offsets", "villager_houses.slot_rule")],
                y_offsets=[P(x) for x in get(sr, "y_offsets", "villager_houses.slot_rule")])
            if len(self.slot_rule.x_offsets) != 3 or len(self.slot_rule.y_offsets) != 3:
                raise MapSpecError("map.villager_houses.slot_rule: x_offsets/y_offsets need 3 values")
            fb = vh.get("fallback_marker") or {}
            self.house_fallback_label = str(fb.get("label") or "")

        # ---- player
        pl = raw.get("player")
        self.player = pl is not None
        if pl:
            self.chain: list[ChainStep] = []
            names = set()
            for i, st in enumerate(get(pl, "chain", "player")):
                w = f"player.chain[{i}]"
                typ = str(get(st, "type", w))
                if typ not in acmap.SCALARS:
                    raise MapSpecError(f"map.{w}: unsupported type {typ!r}")
                step = ChainStep(name=str(get(st, "step", w)), type=typ, size=acmap.SCALARS[typ][1],
                                 in_mem1=str(st.get("check", "")).lower() == "in mem1",
                                 equals=num(st, "equals", w, required=False), min=num(st, "min", w, required=False))
                if st.get("addr") is not None:
                    step.addr = num(st, "addr", w)
                else:
                    step.src, step.offset = str(get(st, "from", w)), num(st, "offset", w)
                    if step.src not in names:
                        raise MapSpecError(f"map.{w}: 'from' {step.src!r} is not an earlier step")
                names.add(step.name)
                self.chain.append(step)
            if not self.chain or self.chain[-1].type != "ptr":
                raise MapSpecError("map.player.chain must end with the actor pointer")
            self.actor_checks = []
            for i, c in enumerate(pl.get("actor_checks") or []):
                w = f"player.actor_checks[{i}]"
                typ = str(get(c, "type", w))
                self.actor_checks.append((num(c, "offset", w), typ, num(c, "equals", w)))
            pos = get(pl, "position", "player")
            self.pos_type = str(pos.get("type", "f32"))
            self.pos_offsets = (num(pos, "x_offset", "player.position"), num(pos, "y_offset", "player.position"),
                                num(pos, "z_offset", "player.position"))
            fc = get(pl, "facing", "player")
            self.facing = (num(fc, "offset", "player.facing"), str(fc.get("type", "s16")),
                           num(fc, "full_turn", "player.facing", 65536))
            tr = get(pl, "transform", "player")
            self.world_per_acre = float(get(tr, "world_units_per_acre", "player.transform"))
            sw = get(pl, "show_when", "player")
            self.show_scenes = {P(x) for x in get(sw, "scene_no", "player.show_when")}
            ft = sw.get("field_type")
            self.field_type = None
            if ft:
                self.field_type = dict(addr=addr(ft, "player.show_when.field_type"), type=str(ft.get("type", "u8")),
                                       equals=num(ft, "equals", "player.show_when.field_type"))
            self.show_bx = (num(sw, "block_x_min", "player.show_when", self.first_bx),
                            num(sw, "block_x_max", "player.show_when", self.first_bx + self.cols - 1))
            self.show_bz = (num(sw, "block_z_min", "player.show_when", self.first_bz),
                            num(sw, "block_z_max", "player.show_when", self.first_bz + self.rows - 1))
            mk = pl.get("marker") or {}
            self.marker_color = _color(mk.get("color", "#FF2D2D"), "player.marker")
            self.marker_outline = _color(mk.get("outline", "#FFFFFF"), "player.marker")
            self.highlight_color = _color((pl.get("acre_highlight") or {}).get("color", "#FF00E6"),
                                          "player.acre_highlight")
            ia = pl.get("indoor_acre")
            self.indoor = None
            if ia:
                self.indoor = dict(next_scene=addr(ia["next_scene"], "player.indoor_acre.next_scene"),
                                   next_type=str(ia["next_scene"].get("type", "s32")),
                                   exit=addr(ia["exit_position"], "player.indoor_acre.exit_position"),
                                   exit_type=str(ia["exit_position"].get("type", "s16")),
                                   exit_count=num(ia["exit_position"], "count", "player.indoor_acre.exit_position", 3))

    # -- helpers ----------------------------------------------------------------

    def block_index(self, bx: int, bz: int) -> int:
        return bz * self.block_cols + bx

    def in_grid(self, bx: Any, bz: Any) -> bool:
        return isinstance(bx, int) and isinstance(bz, int) and \
            self.first_bx <= bx < self.first_bx + self.cols and self.first_bz <= bz < self.first_bz + self.rows

    def acre_label(self, bx: int, bz: int) -> str | None:
        if not self.in_grid(bx, bz):
            return None
        return f"{self.row_labels[bz - self.first_bz]}-{self.col_labels[bx - self.first_bx]}"

    def tex_ptr_valid(self, ptr: int | None) -> bool:
        return ptr is not None and self.tex_start <= ptr < self.tex_end and (ptr - self.tex_start) % self.tex_size == 0


def load_map_spec(spec: acmap.Spec) -> MapSpec | None:
    """The spec's map section, or None if the spec has none."""
    raw = spec.raw.get("map") if isinstance(spec.raw, dict) else None
    if not raw:
        return None
    return MapSpec(raw, spec)


# --------------------------------------------------------------------------- #
# Pure rules (unit-tested on their own)
# --------------------------------------------------------------------------- #

def house_tier(y: float, step3: bool) -> int:
    """map.villager_houses.tier_rule (mMP_check_layer + mCoBG_Height2GetLayer)."""
    if y < LAYER_LOW_Y:
        layer = 2
    elif step3:
        layer = 1 if y < LAYER_MID_Y else 0
    else:
        layer = 1
    if not step3:
        layer = max(layer - 1, 0)
    return layer


def house_slot(hpl: bytes, fg_name: int, ut_x: int, ut_z: int, rule: SlotRule) -> tuple[int | None, bool]:
    """map.villager_houses.slot_rule: ``(idx, exact)`` for a house, or (None, False)
    when the list is unreadable."""
    def slots(entry_off):
        out = []
        for j in range(rule.slot_count):
            o = entry_off + rule.slots_offset + j * rule.slot_len
            out.append(hpl[o:o + rule.slot_len])
        return out

    for e in range(rule.count):
        off = e * rule.entry_len
        if off + rule.entry_len > len(hpl):
            break
        name = struct.unpack_from(">H", hpl, off + rule.fg_name_offset)[0]
        if name == rule.terminator:
            break
        if name == fg_name:
            ss = slots(off)
            for s in ss:
                if len(s) == rule.slot_len and s[rule.f_ut_x] == ut_x and s[rule.f_ut_z] == ut_z - 1:
                    return s[rule.f_idx], True
            return (ss[0][rule.f_idx], False) if len(ss[0]) == rule.slot_len else (None, False)
    first = slots(0)[0] if len(hpl) >= rule.entry_len else b""
    return (first[rule.f_idx], False) if len(first) == rule.slot_len else (None, False)


def select_acres(m: MapSpec, types: list[int | None], bridge: tuple[int, int, bool] | None = None,
                 pluss: bytes | None = None) -> list[tuple[int | None, int | None, int | None]]:
    """mMP_make_max_no_table: the acre drawn in each map cell, as ``(type, bx, bz)``.

    Cell n is drawn at column n % cols, row n // cols.  Border types are
    skipped and the list is padded with ``pad_type`` (block None).  An
    unreadable type keeps its cell (type None) so later acres do not shift.
    """
    out: list[tuple[int | None, int | None, int | None]] = []
    for bz in range(m.first_bz, m.first_bz + m.rows):
        for bx in range(m.first_bx, m.first_bx + m.cols):
            i = m.block_index(bx, bz)
            t = types[i] if i < len(types) else None
            if t is None or t >= m.type_count:
                out.append((None, bx, bz))
                continue
            if t in m.skip_types:
                continue
            if bridge and bridge[2] and (bx, bz) == (bridge[0], bridge[1]) and pluss is not None \
                    and t < len(pluss) and m.pluss and pluss[t] != m.pluss["none"]:
                t = pluss[t]
                if t >= m.type_count:
                    out.append((None, bx, bz))
                    continue
            out.append((t, bx, bz))
    n = m.cols * m.rows
    out = out[:n]
    out += [(m.pad_type, None, None)] * (n - len(out))
    return out


def facing_vector(angle: int, full_turn: int = 65536) -> tuple[float, float]:
    """(right, down) unit vector on the map for a facing angle (0 = south)."""
    a = 2 * math.pi * (angle % full_turn) / full_turn
    return math.sin(a), math.cos(a)


def facing_label(angle: int, full_turn: int = 65536) -> str:
    step = full_turn / 8
    return FACING_LABELS[int(((angle % full_turn) + step / 2) // step) % 8]


def player_place(m: MapSpec, x: float, z: float) -> dict:
    """World position -> acre, tile and map units (map.player.transform)."""
    wpa = m.world_per_acre
    bx, bz = int(x / wpa), int(z / wpa)          # trunc, like mFI_Wpos2BlockNum
    tile = wpa / TILES_PER_ACRE
    return {
        "block": (bx, bz),
        "tile": (int(x / tile) - bx * TILES_PER_ACRE, int(z / tile) - bz * TILES_PER_ACRE),
        "map": ((x / wpa - m.first_bx) * m.units, (z / wpa - m.first_bz) * m.units),
    }


def _decode(typ: str, data: bytes):
    fmt, size = acmap.SCALARS[typ]
    return struct.unpack(fmt, data)[0] if data and len(data) == size else None


def _u32(data: bytes | None) -> int | None:
    return struct.unpack(">I", data)[0] if data and len(data) == 4 else None


# --------------------------------------------------------------------------- #
# Raster
# --------------------------------------------------------------------------- #

_FONT_SRC = {
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.", "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####", "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.", "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.", "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.", "9": ".###. #...# #...# .#### ....# ...#. .##..",
    "A": ".###. #...# #...# ##### #...# #...# #...#", "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.", "D": "###.. #..#. #...# #...# #...# #..#. ###..",
    "E": "##### #.... #.... ####. #.... #.... #####", "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####", "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.", "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#", "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#", "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.", "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#", "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.", "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.", "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.", "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..", "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "-": "..... ..... ..... .###. ..... ..... .....", "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    " ": "..... ..... ..... ..... ..... ..... .....",
}
FONT = {ch: [[c == "#" for c in row] for row in src.split()] for ch, src in _FONT_SRC.items()}
GLYPH_W, GLYPH_H = 5, 7


class Canvas:
    """An opaque RGBA raster with the few drawing operations the map needs."""

    def __init__(self, width: int, height: int, color: Color = (0, 0, 0, 255)):
        self.width, self.height = width, height
        self.rgba = bytearray(bytes(color) * (width * height))

    def copy(self) -> "Canvas":
        c = Canvas.__new__(Canvas)
        c.width, c.height, c.rgba = self.width, self.height, bytearray(self.rgba)
        return c

    def pixel(self, x: int, y: int) -> Color:
        o = (y * self.width + x) * 4
        return tuple(self.rgba[o:o + 4])

    def png(self) -> bytes:
        return gctex.encode_png(self.width, self.height, bytes(self.rgba))

    def blend(self, x: int, y: int, color: Color, coverage: float = 1.0) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        a = color[3] * coverage / 255.0
        if a <= 0:
            return
        o = (y * self.width + x) * 4
        if a >= 1:
            self.rgba[o:o + 3] = bytes(color[:3])
        else:
            px = self.rgba
            for k in range(3):
                px[o + k] = int(color[k] * a + px[o + k] * (1 - a) + 0.5)
        self.rgba[o + 3] = 255

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Fill [x0, x1) x [y0, y1)."""
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.width, int(x1)), min(self.height, int(y1))
        if x0 >= x1 or y0 >= y1:
            return
        if color[3] == 255:
            row = bytes(color) * (x1 - x0)
            for y in range(y0, y1):
                o = (y * self.width + x0) * 4
                self.rgba[o:o + len(row)] = row
        else:
            for y in range(y0, y1):
                for x in range(x0, x1):
                    self.blend(x, y, color)

    def outline_rect(self, x0: int, y0: int, x1: int, y1: int, t: int, color: Color) -> None:
        self.fill_rect(x0, y0, x1, y0 + t, color)
        self.fill_rect(x0, y1 - t, x1, y1, color)
        self.fill_rect(x0, y0 + t, x0 + t, y1 - t, color)
        self.fill_rect(x1 - t, y0 + t, x1, y1 - t, color)

    def blit(self, src: bytes, sw: int, sh: int, dx: int, dy: int, dw: int, dh: int) -> None:
        """Draw an RGBA image scaled (nearest neighbour) to dw x dh at (dx, dy), alpha-blended."""
        xs = [(tx * sw) // dw for tx in range(dw)]
        cache: dict[int, bytes | None] = {}
        for ty in range(dh):
            y = dy + ty
            if not 0 <= y < self.height:
                continue
            sy = (ty * sh) // dh
            row = src[sy * sw * 4:(sy + 1) * sw * 4]
            if sy not in cache:
                opaque = all(row[4 * sx + 3] == 255 for sx in set(xs))
                cache[sy] = b"".join(bytes(row[4 * sx:4 * sx + 4]) for sx in xs) if opaque else None
            line = cache[sy]
            if line is not None:
                a, b = max(0, -dx), min(dw, self.width - dx)
                if a < b:
                    o = (y * self.width + dx + a) * 4
                    self.rgba[o:o + (b - a) * 4] = line[a * 4:b * 4]
                continue
            for tx, sx in enumerate(xs):
                c = row[4 * sx:4 * sx + 4]
                if c[3]:
                    self.blend(dx + tx, y, tuple(c))

    def fill_sdf(self, bbox: tuple[float, float, float, float], sdf: Callable[[float, float], float],
                 layers: list[tuple[float, Color]]) -> None:
        """Anti-aliased fill of a shape given by its signed distance (px, negative inside).

        ``layers`` are ``(grow, color)`` pairs drawn in order: each fills the
        shape grown by ``grow`` pixels (an outline is a larger layer under a
        smaller one).  Coverage is ``clamp(0.5 - distance)``: one sample per pixel.
        """
        x0, y0, x1, y1 = bbox
        for py in range(max(0, int(math.floor(y0))), min(self.height, int(math.ceil(y1)) + 1)):
            for px in range(max(0, int(math.floor(x0))), min(self.width, int(math.ceil(x1)) + 1)):
                d = sdf(px + 0.5, py + 0.5)
                for grow, color in layers:
                    cov = 0.5 - (d - grow)
                    if cov > 0:
                        self.blend(px, py, color, min(1.0, cov))

    def text(self, x: int, y: int, s: str, color: Color, k: int = 1) -> None:
        """Draw ``s`` (upper-cased; unknown characters as '?') with the 5x7 font at scale k."""
        for n, ch in enumerate(s.upper()):
            glyph = FONT.get(ch, FONT["?"])
            gx = x + n * (GLYPH_W + 1) * k
            for r, row in enumerate(glyph):
                for c, on in enumerate(row):
                    if on:
                        self.fill_rect(gx + c * k, y + r * k, gx + (c + 1) * k, y + (r + 1) * k, color)


def text_size(s: str, k: int = 1) -> tuple[int, int]:
    return (len(s) * (GLYPH_W + 1) - 1) * k if s else 0, GLYPH_H * k


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _in_triangle(px, py, tri) -> bool:
    (ax, ay), (bx, by), (cx, cy) = tri
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _tri_sdf(px, py, tri) -> float:
    """Signed distance to a triangle (negative inside)."""
    a, b, c = tri
    d = min(_seg_dist(px, py, *a, *b), _seg_dist(px, py, *b, *c), _seg_dist(px, py, *c, *a))
    return -d if _in_triangle(px, py, tri) else d


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #

@dataclass
class _Static:
    tex_ptrs: list[int]
    pal_sel: bytes | None
    pal_ptrs: list[int | None]          # None = failed validation
    dct: bytes | None
    pluss: bytes | None
    hpl: bytes | None
    icon: list[Color] | None            # IA4 decoded as (I, I, I, A)
    tiers: dict[int, tuple[tuple, tuple]]   # tier -> (prim rgb, env rgb)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Layout:
    source: str
    types: list[int | None]
    combi: list[int] | None
    entries: list[tuple[int | None, int | None, int | None]]
    tex_keys: list[tuple[int, int] | None]
    buildings: list[dict]
    houses: list[dict]
    bridge: tuple | None
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False                         # some acre image missing: re-read next poll
    invalid: list[int] = field(default_factory=list)  # map cells drawn as placeholders


class TownMapReader:
    """Builds the town map image and a JSON-friendly status from emulated RAM.

    ``update(state)`` takes the GameState from :class:`acmap.ACReader.poll`
    (its validity checks decide whether the map may be read at all, and its
    villager records give the house locations).  ``render()`` returns the
    composited :class:`Canvas`.
    """

    def __init__(self, source, spec: acmap.Spec, scale: int = 4, layout_interval: float = 10.0,
                 indoor_acre: bool = True):
        self.source = source
        self.spec = spec
        self.map = load_map_spec(spec)
        self.scale = max(1, int(scale))
        self.layout_interval = layout_interval
        self.indoor_acre = indoor_acre
        self.round_trips = 0
        self.status: dict = {}
        self._written_key = None
        self._reset(None)

    # -- session ----------------------------------------------------------------

    def _reset(self, key) -> None:
        self._session = key
        self._static: _Static | None = None
        self._layout: _Layout | None = None
        self._layout_sig = None
        self._layout_time = 0.0
        self._palettes: dict[int, list[Color]] = {}
        self._textures: dict[tuple[int, int], bytes] = {}
        self._guess: dict[str, int] = {}
        self._player: dict | None = None
        self._highlight: dict | None = None
        self._base: Canvas | None = None
        self._base_key = None
        self._rendered: Canvas | None = None
        self._rendered_key = None
        self._sep_color: Color = COL_GRID

    # -- reads --------------------------------------------------------------------

    def _batch(self, spans):
        before = getattr(self.source, "transactions", None)
        res = self.source.batch_read(spans)
        after = getattr(self.source, "transactions", None)
        self.round_trips += (after - before) if isinstance(before, int) else 1
        return res

    # -- main entry ---------------------------------------------------------------

    def update(self, state: dict) -> dict:
        self.round_trips = 0
        st = {"status": MS_UNAVAILABLE, "message": "", "layout": None, "buildings": [],
              "villager_houses": [], "player": None, "highlight": None, "image": None,
              "warnings": [], "round_trips": 0}
        if self.map is None:
            st["message"] = "the spec has no map section"
        else:
            try:
                self._update(state, st)
            except EmuLinkError as exc:
                self._guess.clear()
                self._player = self._highlight = None
                st.update(status=MS_DISCONNECTED, message=str(exc))
            self._fill(st)
        st["round_trips"] = self.round_trips
        self.status = st
        return st

    def _update(self, state: dict, st: dict) -> None:
        m = self.map
        game = state.get("game") or {}
        if not state.get("connected"):
            self._player = self._highlight = None
            self._guess.clear()
            st.update(status=MS_DISCONNECTED, message=state.get("message") or "not connected")
            return
        if not game.get("ok"):
            self._reset(None)
            st.update(status=MS_UNAVAILABLE, message=state.get("message") or "unsupported game")
            return
        key = (game.get("hash"), game.get("id"), game.get("revision"))
        if key != self._session:
            self._reset(key)
        if not state.get("in_gameplay"):
            self._player = self._highlight = None
            st.update(status=MS_STALE if self._layout else MS_NO_LAYOUT,
                      message=f"map not refreshed: {state.get('message') or state.get('status')}")
            return

        now = time.monotonic()
        sig = self._signature(state)
        need_static = self._static is None
        need_layout = (need_static or self._layout is None or self._layout.incomplete
                       or sig != self._layout_sig or now - self._layout_time >= self.layout_interval)
        first = m.chain[0] if m.player else None
        seed = (first is not None and first.addr == self.spec.bases.get("gamePT")
                and first.name not in self._guess and is_ptr(state.get("game_ptr")))
        if seed:
            self._guess[first.name] = state["game_ptr"]   # the GAME pointer ACReader saw this poll

        fetched: dict[Any, bytes] = {}
        unstable: set = set()

        def fetch(reads):
            reads = [r for r in reads if r[0] not in fetched]
            if reads:
                raw, bad = acmap.read_stable(self._batch, reads)
                fetched.update(raw)
                unstable.update(bad)

        probe = _PlayerProbe(m, self._guess, self.indoor_acre) if m.player else None
        # Round 1: static tables (once per game), layout (when due), player chain.
        reads = probe.plan(fetched) if probe else []
        if need_static:
            reads += self._static_reads()
        if need_layout:
            reads += self._layout_reads(state)
        fetch(reads)
        if need_static:
            self._static = self._parse_static(fetched)
            if self._static is None:
                st["warnings"].append("map tables unreadable (acre texture pointer table)")
        layout = None
        if need_layout and self._static is not None:
            layout, why = self._parse_layout(fetched, state)
            if layout is None:
                st["warnings"].append(why)
        # Round 2: acre images and palettes not cached yet; the chain if the guess was wrong.
        reads = probe.plan(fetched) if probe else []
        if layout is not None:
            reads += self._texture_reads(layout)
        fetch(reads)
        if layout is not None:
            self._decode_textures(layout, fetched)
            self._layout, self._layout_sig, self._layout_time = layout, sig, now
        elif need_layout and sig != self._layout_sig:
            self._layout = None     # another town now; the old layout is wrong
        for _ in range(len(m.chain) + 1 if probe else 0):
            more = probe.plan(fetched)
            if not more:
                break
            fetch(more)

        if probe:
            player, highlight = probe.result(fetched, unstable, state)
            if player.get("reason") == "unstable read" and self._player and self._player.get("visible"):
                player = dict(self._player, stale=True)          # keep the last good marker
                highlight = self._highlight
            self._player, self._highlight = player, highlight
        if self._static:
            st["warnings"] += self._static.warnings
        if self._layout:
            st["warnings"] += self._layout.warnings
        st.update(status=MS_OK if self._layout else MS_NO_LAYOUT,
                  message="" if self._layout else "town layout not readable yet")

    def _signature(self, state: dict) -> tuple:
        g = state.get("globals") or {}
        homes = tuple((v.get("index"), v.get("npc_id"), v.get("home_block_x"), v.get("home_block_z"),
                       v.get("home_ut_x"), v.get("home_ut_z")) for v in state.get("villagers") or [])
        return g.get("town_id"), g.get("town_name"), homes

    # -- static tables --------------------------------------------------------------

    def _static_reads(self) -> list:
        m = self.map
        r = [("tex_ptrs", m.tex_ptr_addr, 4 * m.tex_ptr_count),
             ("pal_sel", m.pal_sel_addr, m.pal_sel_count),
             ("pal_ptrs", m.pal_ptr_addr, 4 * m.pal_ptr_count),
             ("dct", m.dct_addr, m.dct_entry_len * m.dct_count)]
        r += [(("pal", p), p, m.pal_size) for p in m.pal_expected]
        if m.pluss:
            r.append(("pluss", m.pluss["addr"], m.pluss["count"]))
        if m.houses:
            sr = m.slot_rule
            r.append(("hpl", sr.addr, sr.entry_len * sr.count))
            r.append(("icon", m.icon_addr, m.icon_size))
            for t in m.tiers:
                r.append((("dl", t.tier), t.dl_addr, max(t.prim_offset, t.env_offset) + t.color_len))
        return r

    def _parse_static(self, f: dict) -> _Static | None:
        m = self.map
        w: list[str] = []
        raw = f.get("tex_ptrs")
        if not raw:
            return None
        tex_ptrs = list(struct.unpack(f">{m.tex_ptr_count}I", raw))
        pal_sel = f.get("pal_sel") or None
        pal_ptrs: list[int | None] = []
        raw = f.get("pal_ptrs")
        if raw:
            for p in struct.unpack(f">{m.pal_ptr_count}I", raw):
                ok = (p in m.pal_expected) if m.pal_expected else is_ptr(p)
                pal_ptrs.append(p if ok else None)
            if None in pal_ptrs:
                w.append("acre palette pointers do not match the spec; acres using them show placeholders")
        else:
            w.append("acre palette tables unreadable; acres show placeholders")
        dct = f.get("dct") or None
        if dct is None:
            w.append("data_combi_table unreadable (needed for the save fallback and house spots)")
        pluss = f.get("pluss") or None
        icon = hpl = None
        tiers: dict[int, tuple[tuple, tuple]] = {}
        if m.houses:
            sr = m.slot_rule
            hpl = f.get("hpl") or None
            if hpl and sr.terminator_index is not None:
                o = sr.terminator_index * sr.entry_len + sr.fg_name_offset
                if struct.unpack_from(">H", hpl, o)[0] != sr.terminator:
                    hpl = None
            if hpl is None:
                w.append("house position list unreadable or its terminator moved; houses drawn at acre centres")
            data = f.get("icon")
            if data and any(data):
                try:
                    icon = gctex.decode_colors(data, m.icon_format, m.icon_w, m.icon_h)
                except gctex.TextureError as exc:
                    w.append(f"house icon not decodable ({exc}); using placeholders")
            else:
                w.append("house icon unreadable; using placeholders")
            for t in m.tiers:
                dl = f.get(("dl", t.tier))
                if dl:
                    prim = tuple(dl[t.prim_offset:t.prim_offset + 3])
                    env = tuple(dl[t.env_offset:t.env_offset + 3])
                    if (t.expected_prim and prim != t.expected_prim) or (t.expected_env and env != t.expected_env):
                        w.append(f"house tier {t.tier} colours {prim}/{env} differ from the spec's "
                                 f"{t.expected_prim}/{t.expected_env}; using the values read")
                    tiers[t.tier] = (prim, env)
                elif t.expected_prim and t.expected_env:
                    tiers[t.tier] = (t.expected_prim, t.expected_env)
                    w.append(f"house tier {t.tier} display list unreadable; using the spec's colours")
        return _Static(tex_ptrs=tex_ptrs, pal_sel=pal_sel, pal_ptrs=pal_ptrs, dct=dct, pluss=pluss,
                       hpl=hpl, icon=icon, tiers=tiers, warnings=w)

    # -- layout ---------------------------------------------------------------------

    def _layout_reads(self, state: dict) -> list:
        m = self.map
        r = [("type_ptr", m.type_ptr_addr, 4), ("combi", m.combi_addr, 2 * m.combi_count)]
        if m.type_ptr_expected is not None:
            r.append(("type_tab", m.type_ptr_expected, m.type_table_count))
        if m.bridge:
            r.append(("bridge", m.bridge["addr"], m.bridge["size"]))
        if m.kinds:
            r.append(("kinds_ptr", m.kinds["ptr"], 4))
            if m.kinds["expected"] is not None:
                r.append(("kinds_tab", m.kinds["expected"], 4 * m.kinds["count"]))
        if m.houses:
            hy = m.house_y
            for v in state.get("villagers") or []:
                i = v.get("index")
                if isinstance(i, int):
                    r.append((("house_y", i), hy["addr"] + i * hy["stride"] + hy["field"], 4))
        return r

    def _parse_layout(self, f: dict, state: dict) -> tuple[_Layout | None, str]:
        m, s = self.map, self._static
        w: list[str] = []
        combi = None
        raw = f.get("combi")
        if raw:
            combi = list(struct.unpack(f">{m.combi_count}H", raw))
        save_types = None
        if combi is not None and s.dct:
            save_types = []
            for v in combi:
                k = v >> 2
                save_types.append(s.dct[k * m.dct_entry_len + m.dct_type_offset] if k < m.dct_count else None)
        tab_types = None
        ptr = _u32(f.get("type_ptr"))
        if ptr is not None and ptr == m.type_ptr_expected and f.get("type_tab"):
            tab_types = list(f["type_tab"])
        if tab_types is not None and save_types is not None:
            n = min(len(tab_types), len(save_types))
            diff = sum(1 for i in range(n) if save_types[i] is not None and tab_types[i] != save_types[i])
            if diff:
                return None, (f"acre type table disagrees with the save at {diff} blocks "
                              "(field being rebuilt?); keeping the previous map")
        if tab_types is not None:
            types, source = tab_types, "table"
        elif save_types is not None:
            types, source = save_types, "save"
            w.append(f"acre type table pointer is {ptr if ptr is None else hex(ptr)} "
                     f"(expected {hex(m.type_ptr_expected) if m.type_ptr_expected else '?'}); "
                     "acre types taken from the save")
        else:
            return None, "acre types unreadable (type table pointer and save combi table)"
        types = [t if t is not None and t < m.type_count else None for t in types]
        if len(types) < m.blocks:
            types += [None] * (m.blocks - len(types))

        bridge = None
        raw = f.get("bridge")
        if m.bridge and raw:
            bridge = (raw[m.bridge["bx"]], raw[m.bridge["bz"]], bool(raw[m.bridge["flags"]] & m.bridge["mask"]))
        entries = select_acres(m, types, bridge, s.pluss)
        tex_keys = [self._tex_key(t) for t, _bx, _bz in entries]

        buildings = []
        if m.kinds:
            kp = _u32(f.get("kinds_ptr"))
            raw = f.get("kinds_tab")
            if kp is not None and kp == m.kinds["expected"] and raw:
                kinds = struct.unpack(f">{m.kinds['count']}I", raw)
                for bz in range(m.first_bz, m.first_bz + m.rows):
                    for bx in range(m.first_bx, m.first_bx + m.cols):
                        i = m.block_index(bx, bz)
                        k = kinds[i] if i < len(kinds) else 0
                        ind = next((d for d in m.indicators if k & d.mask), None)
                        if ind:
                            buildings.append({"key": ind.key, "label": ind.label, "block": (bx, bz),
                                              "acre": m.acre_label(bx, bz), "indicator": ind})
            else:
                w.append("building kind table not at the expected address; building list unavailable")

        houses = self._houses(state, f, combi, w) if m.houses else []
        return _Layout(source=source, types=types, combi=combi, entries=entries, tex_keys=tex_keys,
                       buildings=buildings, houses=houses, bridge=bridge, warnings=w), ""

    def _houses(self, state: dict, f: dict, combi: list[int] | None, w: list[str]) -> list[dict]:
        m, s = self.map, self._static
        sr = m.slot_rule
        step3 = bool(combi) and (combi[0] & STEP3_MASK) == STEP3_VALUE
        out = []
        missing_fields = False
        for v in state.get("villagers") or []:
            i = v.get("index")
            bx, bz, ux, uz = (v.get(k) for k in ("home_block_x", "home_block_z", "home_ut_x", "home_ut_z"))
            if not all(isinstance(x, int) for x in (i, bx, bz, ux, uz)):
                missing_fields = True
                continue
            if not m.in_grid(bx, bz):
                continue
            y = _decode("f32", f.get(("house_y", i), b""))
            if y is None or not math.isfinite(y):
                w.append(f"villager slot {i}: house height unreadable; drawn in tier 0")
                tier = 0
            else:
                tier = house_tier(y, step3)
            idx, exact = None, False
            if combi is not None and s.dct and s.hpl:
                k = combi[m.block_index(bx, bz)] >> 2
                if k < m.dct_count:
                    fg = struct.unpack_from(">H", s.dct, k * m.dct_entry_len + m.dct_fg_offset)[0]
                    idx, exact = house_slot(s.hpl, fg, ux, uz, sr)
            if idx is None or not 0 <= idx <= 8:
                idx, exact = 4, False   # acre centre
            cx = (bx - m.first_bx) * m.units + sr.x_offsets[idx % 3]
            cy = (bz - m.first_bz) * m.units + sr.y_offsets[idx // 3]
            out.append({"slot": i, "npc_id": v.get("npc_id"), "block": (bx, bz), "acre": m.acre_label(bx, bz),
                        "spot": idx, "exact": exact, "tier": tier, "map": (cx, cy)})
        if missing_fields:
            w.append("villager home fields missing from the spec's villagers group; some houses not shown")
        out.sort(key=lambda h: (h["tier"], h["slot"]))    # the game draws tier 0 first
        return out

    # -- textures -------------------------------------------------------------------

    def _tex_key(self, t: int | None) -> tuple[int, int] | None:
        m, s = self.map, self._static
        if t is None or t >= len(s.tex_ptrs):
            return None
        ptr = s.tex_ptrs[t]
        if not m.tex_ptr_valid(ptr) or s.pal_sel is None or t >= len(s.pal_sel):
            return None
        sel = s.pal_sel[t]
        pal = s.pal_ptrs[sel] if sel < len(s.pal_ptrs) else None
        return (ptr, pal) if pal is not None else None

    def _texture_reads(self, layout: _Layout) -> list:
        m = self.map
        r = []
        for key in set(k for k in layout.tex_keys if k):
            if key not in self._textures:
                r.append((("tex", key[0]), key[0], m.tex_size))
                if key[1] not in self._palettes:
                    r.append((("pal", key[1]), key[1], m.pal_size))
        return r

    def _decode_textures(self, layout: _Layout, f: dict) -> None:
        m = self.map
        bad = []
        for n, key in enumerate(layout.tex_keys):
            if key is None:
                bad.append(n)
                continue
            if key in self._textures:
                continue
            ptr, pal = key
            if pal not in self._palettes:
                data = f.get(("pal", pal))
                if data:
                    self._palettes[pal] = gctex.decode_palette(data, m.pal_format, m.pal_entries)
            data = f.get(("tex", ptr))
            if not data or pal not in self._palettes:
                layout.incomplete = True      # read again next poll
                layout.tex_keys[n] = None
                bad.append(n)
                continue
            colors = gctex.decode_colors(data, m.tex_format, m.tex_w, m.tex_h, self._palettes[pal])
            x0, y0, vw, vh = m.vis
            out = bytearray()
            for y in range(y0, y0 + vh):
                for c in colors[y * m.tex_w + x0:y * m.tex_w + x0 + vw]:
                    out += bytes(c)
            self._textures[key] = bytes(out)
        if bad:
            cells = [m.acre_label(layout.entries[n][1], layout.entries[n][2]) or f"cell {n}" for n in bad]
            layout.warnings.append("acre images not available for " + ", ".join(cells) + "; placeholders drawn")
        layout.invalid = bad

    # -- status ---------------------------------------------------------------------

    def _fill(self, st: dict) -> None:
        m = self.map
        lay = self._layout
        if lay is not None:
            st["layout"] = {
                "source": lay.source,
                "acre_types": [t for t, _bx, _bz in lay.entries],
                "invalid_acres": [m.acre_label(lay.entries[n][1], lay.entries[n][2]) or f"cell {n}"
                                  for n in lay.invalid],
                "bridge": list(lay.bridge) if lay.bridge else None,
                "age_s": round(time.monotonic() - self._layout_time, 1),
            }
            st["buildings"] = [{"key": b["key"], "label": b["label"], "acre": b["acre"]} for b in lay.buildings]
            st["villager_houses"] = [{"slot": h["slot"], "npc_id": f"0x{h['npc_id']:04X}"
                                      if isinstance(h["npc_id"], int) else None,
                                      "acre": h["acre"], "spot": h["spot"], "tier": h["tier"]}
                                     for h in sorted(lay.houses, key=lambda h: h["slot"])]
        if self._player is not None:
            st["player"] = dict(self._player)
        st["highlight"] = dict(self._highlight) if self._highlight else None
        W, H = self._geometry()["size"]
        st["image"] = {"width": W, "height": H, "scale": self.scale}

    # -- rendering ------------------------------------------------------------------

    def _geometry(self) -> dict:
        m, S = self.map, self.scale
        k = max(1, (3 * S) // 4)                # label glyph scale
        margin, frame = 2 * S, 2 * S
        label_w = GLYPH_W * k + 2 * S
        label_h = GLYPH_H * k + 2 * S
        acre = m.units * S
        gx = margin + label_w + frame
        gy = margin + label_h + frame
        inner_w, inner_h = m.cols * acre + S, m.rows * acre + S   # + closing separator line
        W = gx + inner_w + frame + margin
        H = gy + inner_h + frame + margin
        return {"S": S, "k": k, "margin": margin, "frame": frame, "label_w": label_w, "label_h": label_h,
                "acre": acre, "gx": gx, "gy": gy, "inner": (inner_w, inner_h), "size": (W, H)}

    def map_to_pixel(self, mx: float, my: float) -> tuple[float, float]:
        """Map units (origin = top-left of the acre grid) -> image pixels."""
        g = self._geometry()
        return g["gx"] + mx * g["S"], g["gy"] + my * g["S"]

    def render(self) -> Canvas:
        """The composited map: acres, building indicators, villager houses, player."""
        if self.map is None:
            raise MapSpecError("the spec has no map section")
        base = self._render_base()
        p = self._player if self._player and self._player.get("visible") else None
        key = (self._base_key, p and (tuple(p["map"]), p["facing"]),
               self._highlight and tuple(self._highlight["block"]))
        if self._rendered is not None and key == self._rendered_key:
            return self._rendered
        c = base.copy()
        g = self._geometry()
        if self._highlight:
            bx, bz = self._highlight["block"]
            x0 = g["gx"] + (bx - self.map.first_bx) * g["acre"]
            y0 = g["gy"] + (bz - self.map.first_bz) * g["acre"]
            t = max(2, g["S"] // 2 + 1)
            c.outline_rect(x0, y0, x0 + g["acre"] + g["S"], y0 + g["acre"] + g["S"], t, self.map.highlight_color)
        if p:
            self._draw_player(c, p)
        self._rendered, self._rendered_key = c, key
        return c

    def _render_base(self) -> Canvas:
        lay = self._layout
        key = self._base_content_key()
        if self._base is not None and key == self._base_key:
            return self._base
        m, g = self.map, self._geometry()
        S, acre = g["S"], g["acre"]
        W, H = g["size"]
        c = Canvas(W, H, COL_BACKGROUND)
        gx, gy, fr = g["gx"], g["gy"], g["frame"]
        iw, ih = g["inner"]
        # frame
        c.fill_rect(gx - fr, gy - fr, gx + iw + fr, gy + ih + fr, COL_FRAME)
        e = max(1, S // 3)
        c.outline_rect(gx - fr, gy - fr, gx + iw + fr, gy + ih + fr, e, COL_FRAME_EDGE)
        c.fill_rect(gx, gy, gx + iw, gy + ih, COL_GRID)
        # labels
        k = g["k"]
        sh = max(1, k // 2)
        for i, lab in enumerate(m.col_labels):
            tw, th = text_size(lab, k)
            x = gx + i * acre + (acre - tw) // 2
            y = g["margin"] + (g["label_h"] - th) // 2
            c.text(x + sh, y + sh, lab, COL_LABEL_SHADOW, k)
            c.text(x, y, lab, COL_LABEL_COL, k)
        for i, lab in enumerate(m.row_labels):
            tw, th = text_size(lab, k)
            x = g["margin"] + (g["label_w"] - tw) // 2
            y = gy + i * acre + (acre - th) // 2
            c.text(x + sh, y + sh, lab, COL_LABEL_SHADOW, k)
            c.text(x, y, lab, COL_LABEL_ROW, k)

        self._sep_color = COL_GRID
        if lay is not None:
            vw, vh = m.vis[2], m.vis[3]
            first = next((self._textures[kk] for kk in lay.tex_keys if kk and kk in self._textures), None)
            if first is not None:
                o = (vw // 2) * 4                # middle of the top row: the baked-in separator
                sep = tuple(first[o:o + 4])
                if sep[3] == 255:
                    self._sep_color = sep
            for n in range(len(lay.entries)):
                x0 = gx + (n % m.cols) * acre
                y0 = gy + (n // m.cols) * acre
                tex = self._textures.get(lay.tex_keys[n]) if lay.tex_keys[n] else None
                if tex is not None:
                    c.blit(tex, vw, vh, x0, y0, acre, acre)
                else:
                    c.fill_rect(x0, y0, x0 + acre, y0 + acre, COL_FALLBACK_ACRE)
                    c.fill_rect(x0, y0, x0 + acre, y0 + S, self._sep_color)
                    c.fill_rect(x0, y0, x0 + S, y0 + acre, self._sep_color)
        else:
            for n in range(m.cols * m.rows):
                x0, y0 = gx + (n % m.cols) * acre, gy + (n // m.cols) * acre
                c.fill_rect(x0, y0, x0 + acre, y0 + acre, COL_FALLBACK_ACRE)
                c.fill_rect(x0, y0, x0 + acre, y0 + S, self._sep_color)
                c.fill_rect(x0, y0, x0 + S, y0 + acre, self._sep_color)
        # closing separator on the right and bottom edges of the grid
        c.fill_rect(gx + m.cols * acre, gy, gx + iw, gy + ih, self._sep_color)
        c.fill_rect(gx, gy + m.rows * acre, gx + iw, gy + ih, self._sep_color)

        if lay is not None:
            cell_of = {(bx, bz): n for n, (_t, bx, bz) in enumerate(lay.entries) if bx is not None}
            for b in lay.buildings:
                n = cell_of.get(b["block"])
                if n is None or lay.tex_keys[n] is None or lay.tex_keys[n] not in self._textures:
                    self._draw_fallback_building(c, b)
            self._draw_houses(c, lay)
        self._base, self._base_key = c, key
        self._rendered_key = None
        return c

    def _base_content_key(self) -> tuple:
        """Everything the base image depends on (a layout re-read with the same
        content keeps the cached image)."""
        lay, s = self._layout, self._static
        if lay is None:
            return (None, self.scale)
        return (tuple(lay.entries), tuple(lay.tex_keys),
                tuple((b["key"], b["block"]) for b in lay.buildings),
                tuple((h["map"], h["tier"]) for h in lay.houses),
                tuple(sorted(s.tiers.items())) if s else None, s is not None and s.icon is not None,
                self.scale)

    def _draw_fallback_building(self, c: Canvas, b: dict) -> None:
        m, g = self.map, self._geometry()
        S, acre = g["S"], g["acre"]
        bx, bz = b["block"]
        x0 = g["gx"] + (bx - m.first_bx) * acre
        y0 = g["gy"] + (bz - m.first_bz) * acre
        ind: Indicator = b["indicator"]
        side = 8 * S
        sx, sy = x0 + (acre - side) // 2, y0 + 4 * S
        c.fill_rect(sx, sy, sx + side, sy + side, ind.fallback_color)
        c.outline_rect(sx, sy, sx + side, sy + side, max(1, S // 3), COL_TEXT_SHADOW)
        label = ind.fallback_label.upper()
        fk = max(1, S // 3)
        while fk > 1 and text_size(label, fk)[0] > acre - 2 * S:
            fk -= 1
        tw = text_size(label, fk)[0]
        tx, ty = x0 + (acre - tw) // 2, sy + side + S
        c.text(tx + 1, ty + 1, label, COL_TEXT_SHADOW, fk)
        c.text(tx, ty, label, COL_TEXT, fk)

    def _draw_houses(self, c: Canvas, lay: _Layout) -> None:
        m, s, g = self.map, self._static, self._geometry()
        S = g["S"]
        size = m.icon_units * S if m.houses else 0
        tinted: dict[int, bytes] = {}
        for h in lay.houses:      # ascending tier
            prim, _env = self._tier_colors(h["tier"])
            cx, cy = self.map_to_pixel(*h["map"])
            x0, y0 = int(round(cx - size / 2)), int(round(cy - size / 2))
            if s.icon is None:
                c.fill_rect(x0, y0, x0 + size, y0 + size, (*prim, 255))
                continue
            if h["tier"] not in tinted:
                tinted[h["tier"]] = self.tinted_icon(h["tier"])
            c.blit(tinted[h["tier"]], m.icon_w, m.icon_h, x0, y0, size, size)

    def _tier_colors(self, tier: int) -> tuple[tuple, tuple]:
        s = self._static
        return s.tiers.get(tier, ((128, 128, 128), (225, 225, 225))) if s else ((128, 128, 128), (225, 225, 225))

    def tinted_icon(self, tier: int) -> bytes | None:
        """The house icon tinted for ``tier`` as RGBA (icon_w x icon_h): env + (prim - env) * I / 255,
        rounded, alpha = A.  None while the icon is unavailable."""
        s = self._static
        if s is None or s.icon is None:
            return None
        prim, env = self._tier_colors(tier)
        out = bytearray()
        for (i, _i2, _i3, a) in s.icon:
            out += bytes((int(e + (p - e) * i / 255 + 0.5) for p, e in zip(prim, env))) + bytes((a,))
        return bytes(out)

    def render_static(self) -> Canvas:
        """The map without the dynamic overlay (player marker, acre highlight)."""
        if self.map is None:
            raise MapSpecError("the spec has no map section")
        return self._render_base()

    def snapshot(self) -> dict:
        """Decoded map content in client-independent form, for the Python/Android cross-check
        (crosscheck_export.py): per grid cell (type, block_x, block_z, cropped RGBA texels or None),
        building acres, villager houses with their centres in map units, the tinted icon per tier,
        the player marker and the highlighted acre."""
        lay = self._layout
        out: dict = {"acres": [], "buildings": [], "houses": [], "icons": {}, "player": None,
                     "highlight": list(self._highlight["block"]) if self._highlight else None}
        if lay is not None:
            for n, (t, bx, bz) in enumerate(lay.entries):
                key = lay.tex_keys[n]
                out["acres"].append((t, bx, bz, self._textures.get(key) if key else None))
            out["buildings"] = [(b["key"], *b["block"]) for b in lay.buildings]
            out["houses"] = [(h["slot"], *h["block"], h["spot"], h["tier"], *h["map"])
                             for h in sorted(lay.houses, key=lambda h: h["slot"])]
            out["icons"] = {t: self.tinted_icon(t) for t in sorted({h["tier"] for h in lay.houses})}
        p = self._player
        if p and p.get("visible"):
            out["player"] = {"block": list(p["block"]), "map": list(p["map"]), "facing": p["facing"],
                             "dir": list(facing_vector(p["facing"], self.map.facing[2]))}
        return out

    def _draw_player(self, c: Canvas, p: dict) -> None:
        S = self.scale
        cx, cy = self.map_to_pixel(*p["map"])
        dx, dy = facing_vector(p["facing"], self.map.facing[2])
        nx, ny = -dy, dx
        r = 1.5 * S + 1                       # dot radius (px)
        ow = max(1.0, 0.4 * S)                # white outline width
        reach = r + 3.2 * S + 1               # arrow tip distance from the centre
        tip = (cx + dx * reach, cy + dy * reach)
        gap = r + ow + 0.3 * S                # arrowhead starts just outside the dot's outline
        base = (cx + dx * gap, cy + dy * gap)
        hw = 1.4 * S + 0.5
        tri = (tip, (base[0] + nx * hw, base[1] + ny * hw), (base[0] - nx * hw, base[1] - ny * hw))
        pad = ow + 2
        xs = [cx - r, cx + r] + [p[0] for p in tri]
        ys = [cy - r, cy + r] + [p[1] for p in tri]
        bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        c.fill_sdf(bbox, lambda x, y: min(math.hypot(x - cx, y - cy) - r, _tri_sdf(x, y, tri)),
                   [(ow, self.map.marker_outline), (0.0, self.map.marker_color)])

    def png(self) -> bytes:
        return self.render().png()

    def write_png(self, path) -> bool:
        """Write the current map to ``path`` (atomic).  Returns False if unchanged since the last write.
        Raises :class:`ProjectPathError` for a path inside the project (the image holds decoded game art)."""
        check_output_path(path)
        canvas = self.render()
        key = (str(path), self._rendered_key)
        if self._written_key == key:
            return False
        gctex.write_png(path, canvas.width, canvas.height, bytes(canvas.rgba))
        self._written_key = key
        return True


class _PlayerProbe:
    """Follows map.player.chain with speculative reads (last poll's pointers),
    so a steady-state poll needs one request."""

    def __init__(self, m: MapSpec, guess: dict[str, int], indoor: bool):
        self.m = m
        self.guess = guess
        self.indoor = indoor and m.indoor is not None
        f = []
        for i, (off, typ, _eq) in enumerate(m.actor_checks):
            f.append((("check", i), off, acmap.SCALARS[typ][1]))
        psize = acmap.SCALARS[m.pos_type][1]
        f += [(("pos", n), off, psize) for n, off in enumerate(m.pos_offsets)]
        f.append((("facing",), m.facing[0], acmap.SCALARS[m.facing[1]][1]))
        self.actor_fields = f
        self.absolute = []
        if m.field_type:
            self.absolute.append(("field_type", m.field_type["addr"], acmap.SCALARS[m.field_type["type"]][1]))
        if self.indoor:
            self.absolute.append(("next_scene", m.indoor["next_scene"], acmap.SCALARS[m.indoor["next_type"]][1]))
            self.absolute.append(("exit_pos", m.indoor["exit"],
                                  acmap.SCALARS[m.indoor["exit_type"]][1] * m.indoor["exit_count"]))

    def _values(self, fetched: dict) -> dict[str, int]:
        vals: dict[str, int] = {}
        for st in self.m.chain:
            if st.addr is not None:
                addr = st.addr
            elif st.src in vals and is_ptr(vals[st.src]):
                addr = (vals[st.src] + st.offset) & 0xFFFFFFFF
            else:
                continue
            v = _decode(st.type, fetched.get(("chain", st.name, addr), b""))
            if v is not None:
                vals[st.name] = v
        return vals

    def plan(self, fetched: dict) -> list:
        vals = self._values(fetched)
        reads = []
        for st in self.m.chain:
            if st.name in vals:
                continue
            if st.addr is not None:
                addr = st.addr
            else:
                base = vals[st.src] if st.src in vals else self.guess.get(st.src)
                if not is_ptr(base):
                    continue
                addr = (base + st.offset) & 0xFFFFFFFF
            tag = ("chain", st.name, addr)
            if tag not in fetched:
                reads.append((tag, addr, st.size))
        last = self.m.chain[-1].name
        actor = vals[last] if last in vals else self.guess.get(last)
        if is_ptr(actor):
            for key, off, size in self.actor_fields:
                tag = ("actor", key, actor + off)
                if tag not in fetched:
                    reads.append((tag, actor + off, size))
        reads += [(("abs", k), a, n) for k, a, n in self.absolute if ("abs", k) not in fetched]
        return reads

    def result(self, fetched: dict, unstable: set, state: dict) -> tuple[dict, dict | None]:
        m = self.m
        vals = self._values(fetched)
        for st in m.chain:
            if st.type == "ptr" and st.name in vals and is_ptr(vals[st.name]):
                self.guess[st.name] = vals[st.name]
        torn = any(isinstance(t, tuple) and t and t[0] in ("chain", "actor") for t in unstable)

        def hidden(reason, extra=None):
            out = {"visible": False, "reason": reason}
            if extra:
                out.update(extra)
            return out, None

        for st in m.chain:
            if st.name not in vals:
                return hidden("unstable read" if torn else f"player chain: {st.name} unreadable")
            v = vals[st.name]
            if st.in_mem1 and not is_ptr(v):
                self.guess.pop(st.name, None)
                return hidden(f"player chain: {st.name} = 0x{v:08X} is not a MEM1 pointer")
            if st.equals is not None and v != st.equals:
                return hidden(f"player chain: {st.name} = 0x{v & 0xFFFFFFFF:08X} (not in gameplay)")
            if st.min is not None and v < st.min:
                return hidden("no player actor")
        actor = vals[m.chain[-1].name]

        def field_(key, typ):
            for tag_key, off, _size in self.actor_fields:
                if tag_key == key:
                    return _decode(typ, fetched.get(("actor", key, actor + off), b""))
            return None

        for i, (_off, typ, eq) in enumerate(m.actor_checks):
            v = field_(("check", i), typ)
            if v is None:
                return hidden("unstable read" if torn else "player actor unreadable")
            if v != eq:
                return hidden("player actor check failed (not the player)")
        pos = [field_(("pos", n), m.pos_type) for n in range(3)]
        facing = field_(("facing",), m.facing[1])
        if any(v is None for v in pos) or facing is None:
            return hidden("unstable read" if torn else "player position unreadable")
        if not all(math.isfinite(v) for v in pos):
            return hidden("player position is not a number")
        x, y, z = pos
        facing %= m.facing[2]          # s16 -> 0 .. full_turn - 1
        info = {"world": [round(x, 2), round(y, 2), round(z, 2)], "facing": facing,
                "facing_dir": facing_label(facing, m.facing[2])}

        scene = (state.get("globals") or {}).get("scene_no")
        ftype = None
        if m.field_type:
            ftype = _decode(m.field_type["type"], fetched.get(("abs", "field_type"), b""))
        outdoors = scene in m.show_scenes and (m.field_type is None or ftype == m.field_type["equals"])
        if not outdoors:
            labels = self._scene_label(state, scene)
            out, _ = hidden(f"not outdoors ({labels})" if scene not in m.show_scenes
                            else f"not on the town field (field_type {ftype})", info)
            return out, self._indoor_highlight(fetched, ftype)
        place = player_place(m, x, z)
        bx, bz = place["block"]
        if not (m.show_bx[0] <= bx <= m.show_bx[1] and m.show_bz[0] <= bz <= m.show_bz[1]):
            return hidden(f"outside the town map (block {bx},{bz}; island or border)", info)
        info.update(visible=True, reason=None, acre=m.acre_label(bx, bz), block=[bx, bz],
                    tile=list(place["tile"]), map=[round(place["map"][0], 3), round(place["map"][1], 3)])
        return info, {"acre": m.acre_label(bx, bz), "block": (bx, bz), "source": "player"}

    def _scene_label(self, state: dict, scene) -> str:
        label = ((state.get("labels") or {}).get("scene_no"))
        return f"scene {scene}" + (f": {label}" if label else "")

    def _indoor_highlight(self, fetched: dict, ftype) -> dict | None:
        """map.player.indoor_acre: the acre of the building the player is in."""
        m = self.m
        if not self.indoor or ftype is None or ftype == 0:
            return None
        nxt = _decode(m.indoor["next_type"], fetched.get(("abs", "next_scene"), b""))
        raw = fetched.get(("abs", "exit_pos"), b"")
        if not nxt or not raw:
            return None
        fmt = acmap.SCALARS[m.indoor["exit_type"]][0]
        pos = struct.unpack(">" + fmt[1] * m.indoor["exit_count"], raw)
        if len(pos) < 3:
            return None
        bx, bz = int(pos[0] / m.world_per_acre), int(pos[2] / m.world_per_acre)
        if not (m.show_bx[0] <= bx <= m.show_bx[1] and m.show_bz[0] <= bz <= m.show_bz[1]):
            return None
        return {"acre": m.acre_label(bx, bz), "block": (bx, bz), "source": "exit_door"}
