"""Export Python-decoded synthetic images for the Android decoder cross-check.

Builds the mock's synthetic images from the real spec (plus one "town_edge"
image with charmap, trimming, blank-name and out-of-range edge cases), decodes
each with acmap (two polls, so harvested villager names are included), reduces
the GameState to a canonical summary, and writes:

    <out>/cases.json          sparse images + the summaries Python produced
    <out>/map_cases.json      sparse town-map images + what townmap.py made of them:
                              per-acre texel hashes, buildings, houses, tinted icon
                              hashes, the player marker, the highlighted acre and
                              row hashes of the static composition at MAP_SCALE
    <out>/ac_memory_map.json  copy of the spec the summaries were made with

The Android test CrossCheckTest loads the same sparse images into FakeMemory,
decodes them with Decoder.kt / TownMapReader and asserts identical results
(and TownMapRenderer.compose must give the same pixels).

    python crosscheck_export.py [--spec PATH] [--out DIR] [--save-image DIR]

All names in the images are invented (see mock_server); nothing comes from the game.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acmap  # noqa: E402
import townmap  # noqa: E402
from emulink import MemoryImageSource  # noqa: E402
from mock_server import SCENARIOS, add_synthetic_map, build_synthetic_image, gx_tile_order  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "test" / "resources" / "crosscheck"
MAP_CASES_FILE = "map_cases.json"
GAME_HASH = "crosscheck"
EMPTY_SLOT = "—"   # em dash, as both clients show an empty item id


def put(image: bytearray, addr: int, data: bytes) -> None:
    off = addr & 0x01FFFFFF
    image[off:off + len(data)] = data


def edge_image(spec: acmap.Spec) -> bytearray:
    """The town image with decoding edge cases mixed in."""
    image, exp = build_synthetic_image(spec, "town")
    rec = spec.players.record_addr(spec.bases, 0)
    pf = spec.players.field
    # name: 0x00 is a glyph, not a terminator
    put(image, rec + pf("name").offset, b"A\x00a     ")
    # town: empty charmap entry (0x7F), single glyph (0x8D), two-char entry (0xDC),
    # trailing 0xD2 (decodes to ' ') must be trimmed with the ASCII spaces
    put(image, rec + pf("town_name").offset, b"Ma\x7fp\x8d\xdc\xd2 ")
    put(image, rec + pf("wallet").offset, struct.pack(">I", 100000))          # > 99999
    kabu = spec.global_field("kabu_prices")
    put(image, spec.global_addr(kabu) + 4, struct.pack(">H", 2500))            # > 2000
    put(image, spec.global_addr(spec.global_field("rtc_weekday")), b"\x09")   # not 0-6
    # pocket 2's name entry cleared: all-zero bytes mean "no name", not 16 x charmap[0]
    addr, length = spec.name_entry(exp["pockets"][1])
    put(image, addr, bytes(length))
    # an id inside an unknown range, and an invalid villager slot
    pockets = spec.players.record_addr(spec.bases, 0) + pf("pockets").offset
    put(image, pockets + 2 * 9, struct.pack(">H", 0x2104))
    put(image, spec.villagers.record_addr(spec.bases, 5), struct.pack(">H", 0x1234))
    return image


def sparse(image: bytes, gap: int = 32) -> list[dict]:
    """Non-zero runs of the image as [{"addr": "0x8...", "hex": "..."}]."""
    regions, i, n = [], 0, len(image)
    while i < n:
        if image[i] == 0:
            i += 1
            continue
        start = end = i
        while i < n and i - end <= gap:
            if image[i]:
                end = i
            i += 1
        regions.append({"addr": f"0x{0x80000000 + start:08X}", "hex": image[start:end + 1].hex()})
    return regions


def item_text(reader: acmap.ACReader, item_id) -> str | None:
    if not isinstance(item_id, int):
        return None
    if item_id in reader.spec.empty_ids:
        return EMPTY_SLOT
    return reader.item_name(item_id) or f"0x{item_id:04X}"


def summary(state: dict, reader: acmap.ACReader) -> dict:
    """Canonical, client-independent view of a GameState (mirrored in CrossCheckTest.kt)."""
    spec = reader.spec
    phase = {acmap.ST_NO_PLAYER: "not_in_town"}.get(state["status"], state["status"])
    out: dict = {"phase": phase}
    if phase not in (acmap.ST_IN_TOWN, acmap.ST_VISITING):
        return out
    g = state["globals"]

    def label(key, value):
        f = spec.global_field(key)
        return spec.enum_label(f.enum if f else None, value)

    clock_keys = ("rtc_year", "rtc_month", "rtc_day", "rtc_hour", "rtc_min")
    clock = None
    if all(isinstance(g.get(k), int) for k in clock_keys):
        wd = g.get("rtc_weekday") if isinstance(g.get("rtc_weekday"), int) else -1
        clock = [g["rtc_year"], g["rtc_month"], g["rtc_day"], wd, g["rtc_hour"], g["rtc_min"],
                 label("rtc_weekday", wd), label("rtc_month", g["rtc_month"])]

    weather = None
    w, inten = g.get("weather"), g.get("weather_intensity")
    if isinstance(w, int):
        weather = label("weather", w) or str(w)
        if w != 0 and isinstance(inten, int) and inten != 0:
            weather += f" ({label('weather_intensity', inten) or f'level {inten}'})"

    wd_enum = spec.global_field("rtc_weekday").enum or "weekday"
    today = clock[3] if clock else None
    turnips = [[spec.enum_label(wd_enum, i) or f"D{i}", p, i == today]
               for i, p in enumerate(g.get("kabu_prices") or [])]

    out.update(
        town=g.get("town_name"),
        player_no=g.get("player_no"),
        clock=clock,
        weather=weather,
        turnips=turnips,
        villagers=[[v["index"], v["npc_id"], v["name"]] for v in state["villagers"]],
        town_fruit=item_text(reader, g.get("town_fruit")),
    )
    if phase == acmap.ST_IN_TOWN:
        p = state["player"]
        out.update(
            player=[state["player_index"], p.get("name"), p.get("town_name"),
                    p.get("wallet"), p.get("bank"), p.get("loan")],
            pockets=[[s["id"], s["name"], s["empty"], s["condition"]] for s in state["pockets"]],
            equipment=item_text(reader, p.get("equipment")),
            shirt=item_text(reader, p.get("shirt")),
        )
    return out


def decode(spec: acmap.Spec, image: bytes) -> dict:
    reader = acmap.ACReader(MemoryImageSource(bytes(image), game_hash=GAME_HASH), spec)
    reader.poll()
    return summary(reader.poll(), reader)


# --------------------------------------------------------------------------- #
# Town map: decoded acre art, house icons, composition and player marker
# --------------------------------------------------------------------------- #

# Pixels per map unit of the composition compared with Android's TownMapRenderer.compose:
# with 10-unit icons of 16 texels this is 5 px per icon texel, so both composers sample
# the icon the same way (the comparison is about content, placement, tint and blending).
MAP_SCALE = 8


def sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _addr_of(spec: acmap.Spec, d: dict) -> int:
    P = acmap.parse_int
    return P(d["addr"]) if d.get("addr") is not None else spec.bases[d["base"]] + P(d["offset"])


def map_images(spec: acmap.Spec) -> dict[str, bytearray]:
    """Synthetic town-map images (made-up art; see mock_server.add_synthetic_map)."""
    m = spec.raw.get("map")
    if not m:
        return {}
    P = acmap.parse_int
    g = m["grid"]
    images = {"map_town": build_synthetic_image(spec, "town", with_map=True)[0]}

    # Other homes and tiers, the player at a fractional position facing between south and east, and a
    # translucent icon (every IA4 alpha and intensity level) so tinting and blending are compared.
    img = build_synthetic_image(spec, "town")[0]
    add_synthetic_map(img, spec, homes=[(0, 1, 1, 3, 4, 300.0), (1, 3, 5, 12, 7, 150.0), (2, 5, 6, 6, 10, 20.0)],
                      player={"x": 2550.5, "y": 120.0, "z": 1300.25, "facing": 0x3000})
    mk = m["villager_houses"]["marker"]
    w, h = mk["width"], mk["height"]
    vals = [(((x * 7 + y * 3) % 16) << 4) | ((x + 2 * y) % 16) for y in range(h) for x in range(w)]
    put(img, P(mk["addr"]), bytes(gx_tile_order(vals, w, h, 8, 4)))
    images["map_moved"] = img

    # A border type at A-1 in both type sources: the game's acre list skips it, shifts and pads.
    at = m["acre_types"]
    if at.get("skip_types"):
        img = build_synthetic_image(spec, "town", with_map=True)[0]
        i = g["first_block_z"] * g["block_cols"] + g["first_block_x"]
        skip = P(at["skip_types"][0])
        put(img, P(at["table"]["expected_pointer"]) + i, bytes([skip]))
        put(img, _addr_of(spec, at["from_save"]["combi_table"]) + 2 * i, struct.pack(">H", skip << 2))
        images["map_skip"] = img

    # No acre images: the palette pointer table does not hold valid pointers.
    img = build_synthetic_image(spec, "town", with_map=True)[0]
    pt = m["acre_texture"]["palette"]["pointer_table"]
    put(img, P(pt["addr"]), bytes(4 * pt["count"]))
    images["map_no_textures"] = img
    return images


def map_decode(spec: acmap.Spec, image: bytes) -> dict:
    """What the Python client makes of a town-map image, client-independent (CrossCheckTest.kt)."""
    src = MemoryImageSource(bytes(image), game_hash=GAME_HASH)
    state = acmap.ACReader(src, spec).poll()
    tm = townmap.TownMapReader(src, spec, scale=MAP_SCALE)
    st = tm.update(state)
    snap = tm.snapshot()
    out = {
        "status": st["status"],
        "acres": [[t, bx, bz, sha(px)] for t, bx, bz, px in snap["acres"]],
        "buildings": sorted([list(b) for b in snap["buildings"]]),
        "houses": [list(h) for h in snap["houses"]],
        "icons": {str(t): sha(px) for t, px in snap["icons"].items()},
        "player": snap["player"],
        "highlight": snap["highlight"],
        "compose": None,
    }
    if snap["acres"] and all(px is not None for *_x, px in snap["acres"]):
        # The static map (acres + house icons) at MAP_SCALE, grid area only (no labels, frame
        # or closing grid line): one hash per pixel row, so a mismatch names its row.
        c = tm.render_static()
        gx, gy = (int(v) for v in tm.map_to_pixel(0, 0))
        W, H = tm.map.cols * tm.map.units * MAP_SCALE, tm.map.rows * tm.map.units * MAP_SCALE
        rows = []
        for y in range(gy, gy + H):
            o = (y * c.width + gx) * 4
            rows.append(hashlib.sha256(bytes(c.rgba[o:o + W * 4])).hexdigest()[:16])
        out["compose"] = {"scale": MAP_SCALE, "width": W, "height": H, "rows": rows}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--spec", help=f"spec path (default: {acmap.DEFAULT_SPEC_PATH})")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    ap.add_argument("--save-image", metavar="DIR", help="also write each full image as DIR/<case>.raw")
    args = ap.parse_args(argv)

    spec = acmap.load_spec(args.spec)
    if spec.warnings:
        print("spec warnings:", *spec.warnings, sep="\n  ", file=sys.stderr)
    images = {name: build_synthetic_image(spec, name)[0] for name in SCENARIOS}
    images["town_edge"] = edge_image(spec)

    cases = []
    for name, image in images.items():
        result = decode(spec, image)
        cases.append({"name": name, "game_hash": GAME_HASH, "regions": sparse(image), "expected": result})
        if args.save_image:
            Path(args.save_image).mkdir(parents=True, exist_ok=True)
            Path(args.save_image, f"{name}.raw").write_bytes(image)
        print(f"{name:12s} {result['phase']}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "cases.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": 1, "spec_file": "ac_memory_map.json", "cases": cases}, fh,
                  ensure_ascii=True, indent=1)
        fh.write("\n")
    map_cases = []
    for name, image in map_images(spec).items():
        result = map_decode(spec, image)
        map_cases.append({"name": name, "game_hash": GAME_HASH, "regions": sparse(image), "expected": result})
        if args.save_image:
            Path(args.save_image, f"{name}.raw").write_bytes(image)
        print(f"{name:16s} map {result['status']}")
    with open(out / MAP_CASES_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": 1, "spec_file": "ac_memory_map.json", "cases": map_cases}, fh,
                  ensure_ascii=True, indent=1)
        fh.write("\n")
    shutil.copyfile(spec.path, out / "ac_memory_map.json")
    print(f"wrote {out / 'cases.json'}, {out / MAP_CASES_FILE} and {out / 'ac_memory_map.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
