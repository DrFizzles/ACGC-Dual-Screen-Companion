"""Text dashboard for the Animal Crossing second-screen proof of concept.

    python dashboard.py                      # dolphin-lnk on this PC, refresh every 0.5 s
    python dashboard.py --host 192.168.1.50  # dolphin-lnk on another device
    python dashboard.py --once --json        # one JSON GameState and exit
    python dashboard.py --from-dump mem1.raw # decode a Dolphin MRAM dump
    python dashboard.py --map %TEMP%/town.png  # also keep a PNG of the town map up to date
                                               # (outside the project: it holds decoded game art)

Read-only: it never writes to emulated memory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acmap  # noqa: E402
import townmap  # noqa: E402
from emulink import DEFAULT_PORT, EmuLinkClient, MemoryImageSource  # noqa: E402

EMPTY = "—"   # em dash for empty pocket slots
WEEKDAY_FALLBACK = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _clean(text) -> str:
    """Make decoded game text safe for a terminal (charmap has \\n etc.)."""
    if text is None:
        return ""
    return "".join(ch if ch.isprintable() else "?" for ch in str(text))


def _money(v) -> str:
    return f"{v:,}" if isinstance(v, int) else "?"


def render_text(state: dict, spec: acmap.Spec) -> str:
    lines = []
    game = state.get("game") or {}
    stamp = time.strftime("%H:%M:%S", time.localtime(state.get("time", time.time())))
    lines.append(f"AC companion  {state.get('source', '')}  [{stamp}]")
    if state.get("connected"):
        h = (game.get("hash") or "")[:8] or "-"
        ok = "OK" if game.get("ok") else "MISMATCH"
        lines.append(f"Game    {game.get('id') or '?'} rev {game.get('revision')}  hash {h}  [{ok}]")
    lines.append(f"Status  {state.get('message') or state.get('status')}")
    for w in state.get("warnings") or []:
        lines.append(f"  ! {w}")
    if not state.get("in_gameplay"):
        if state.get("status") == acmap.ST_DISCONNECTED:
            lines.append("        (is a game running in dolphin-lnk with the EmuLink server enabled?)")
        return "\n".join(lines)

    g = state.get("globals") or {}
    labels = state.get("labels") or {}
    lines.append("-" * 78)

    player = state.get("player")
    who = f"{_clean(player.get('name'))} (player {player['index'] + 1})" if player else "-"
    residents = ", ".join(_clean(p["name"]) + (" (away)" if p.get("travelling") else "")
                          for p in state.get("players") or [])
    lines.append(f"Town    {_clean(g.get('town_name')):<10} Player  {who:<22} Residents: {residents}")
    if labels.get("town_fruit"):
        lines.append(f"Fruit   {_clean(labels['town_fruit'])}")

    wd = labels.get("rtc_weekday") or (WEEKDAY_FALLBACK[g["rtc_weekday"]]
                                       if isinstance(g.get("rtc_weekday"), int) and 0 <= g["rtc_weekday"] < 7
                                       else "?")
    try:
        date = (f"{g['rtc_year']:04d}-{g['rtc_month']:02d}-{g['rtc_day']:02d} "
                f"{g['rtc_hour']:02d}:{g['rtc_min']:02d}:{g['rtc_sec']:02d}")
    except (KeyError, TypeError, ValueError):
        date = "?"
    weather = labels.get("weather") or f"#{g.get('weather')}"
    intensity = labels.get("weather_intensity") or g.get("weather_intensity")
    lines.append(f"Date    {wd} {date}     Weather  {weather} ({intensity})")
    scene = g.get("scene_no")
    if scene is not None:
        lines.append(f"Scene   {labels.get('scene_no') or 'scene'} ({scene})")
    if state.get("map"):
        lines.append("Map     " + map_text(state["map"]))

    if player:
        lines.append(f"Bells   wallet {_money(player.get('wallet'))}   bank {_money(player.get('bank'))}"
                     f"   loan {_money(player.get('loan'))}")
        plabels = player.get("labels") or {}
        worn = [f"{what} {_clean(plabels[key])}" for key, what in (("shirt", "Wearing"), ("equipment", "Holding"))
                if plabels.get(key)]
        if worn:
            lines.append("        " + "   ".join(worn))
        lines.append("Pockets")
        cells = []
        for p in state.get("pockets") or []:
            if p["empty"]:
                cells.append(f"{p['slot']:>3} ---- {EMPTY}")
                continue
            name = _clean(p["name"]) if p["name"] else "(unknown)"
            mark = ""
            if p.get("condition"):
                mark = f" [{p.get('condition_label') or 'cond ' + str(p['condition'])}]"
            cells.append(f"{p['slot']:>3} {p['id']:04X} {name}{mark}")
        half = (len(cells) + 1) // 2
        for left, right in zip(cells[:half], cells[half:] + [""]):
            lines.append(f"{left:<39.39} {right}".rstrip())

    prices = g.get("kabu_prices")
    if isinstance(prices, list):
        today = g.get("rtc_weekday")
        parts = []
        for i, price in enumerate(prices):
            day = (spec.enum_label("weekday", i) or (WEEKDAY_FALLBACK[i] if i < 7 else str(i)))[:3]
            cell = f"{day} {price if price is not None else '?'}"
            parts.append(f"[{cell}]" if i == today else cell)
        lines.append("Turnips " + "  ".join(parts))

    villagers = state.get("villagers") or []
    lines.append(f"Villagers ({len(villagers)})")
    row = []
    for v in villagers:
        label = v["npc_id_hex"][2:]
        if v.get("name"):
            label += f" {_clean(v['name'])}"
        row.append(label)
        if len(row) == 5:
            lines.append("  " + "   ".join(row))
            row = []
    if row:
        lines.append("  " + "   ".join(row))
    return "\n".join(lines)


def map_text(m: dict) -> str:
    """One line about the town map: where the player marker is, or why it is hidden."""
    if m.get("status") not in (townmap.MS_OK, townmap.MS_STALE):
        return f"{m.get('status')}: {m.get('message') or '-'}"
    p = m.get("player") or {}
    if p.get("visible"):
        tx, tz = p["tile"]
        text = f"you are in {p['acre']} (tile {tx},{tz}) facing {p['facing_dir']}"
        if p.get("stale"):
            text += " (last good read)"
    else:
        text = f"marker hidden: {p.get('reason') or 'no player data'}"
        hl = m.get("highlight")
        if hl and hl.get("source") == "exit_door":
            text += f"; building in {hl['acre']}"
    text += f"   [{len(m.get('buildings') or [])} buildings, {len(m.get('villager_houses') or [])} houses]"
    if m.get("status") == townmap.MS_STALE:
        text += " (stale)"
    return text


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Live Animal Crossing (GAFE01) dashboard over dolphin-lnk EmuLink.")
    ap.add_argument("--host", default="127.0.0.1", help="emulator host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"EmuLink UDP port (default {DEFAULT_PORT})")
    ap.add_argument("--interval", type=float, default=0.5, help="refresh interval in seconds (default 0.5)")
    ap.add_argument("--timeout", type=float, default=0.5, help="per-attempt UDP timeout in seconds (default 0.5)")
    ap.add_argument("--once", action="store_true", help="poll once, print, exit")
    ap.add_argument("--json", action="store_true", help="print GameState as JSON (one object per line when looping)")
    ap.add_argument("--from-dump", metavar="PATH", help="read from a MEM1 dump (mem1.raw) instead of the emulator")
    ap.add_argument("--spec", metavar="PATH", help=f"memory map spec (default: {acmap.DEFAULT_SPEC_PATH})")
    ap.add_argument("--map", metavar="PATH",
                    help="write the town map as a PNG to PATH (rewritten whenever it changes while looping); "
                         "PATH must be outside the project, since the image holds art decoded from the game")
    ap.add_argument("--map-scale", type=int, default=4, metavar="N",
                    help="map pixels per map unit; an acre is 22 units (default 4)")
    ap.add_argument("--no-map", action="store_true", help="do not read the town map (no 'map' in --json)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # names may hold non-ASCII glyphs
    except (AttributeError, ValueError):
        pass
    try:
        spec = acmap.load_spec(args.spec)
    except acmap.SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.map and args.no_map:
        print("error: --map and --no-map exclude each other", file=sys.stderr)
        return 2
    map_path = None
    if args.map:
        try:
            map_path = townmap.check_output_path(args.map)   # decoded game art: never into the project
        except townmap.ProjectPathError as exc:
            print(f"error: --map: {exc}", file=sys.stderr)
            return 2

    if args.from_dump:
        try:
            source = MemoryImageSource(args.from_dump)
        except OSError as exc:
            print(f"error: cannot read dump: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            source = EmuLinkClient(args.host, args.port, timeout=args.timeout)
        except OSError as exc:
            print(f"error: cannot open UDP socket to {args.host}:{args.port}: {exc}", file=sys.stderr)
            return 2
    reader = acmap.ACReader(source, spec)

    map_reader = None
    if not args.no_map:
        try:
            map_reader = townmap.TownMapReader(source, spec, scale=max(1, args.map_scale))
        except townmap.MapSpecError as exc:
            if map_path is not None:
                print(f"error: spec map section: {exc}", file=sys.stderr)
                source.close()
                return 2
            print(f"warning: town map disabled: {exc}", file=sys.stderr)
        if map_reader is not None and map_reader.map is None:
            if map_path is not None:
                print(f"error: the spec ({spec.path}) has no map section", file=sys.stderr)
                source.close()
                return 2
            map_reader = None
    map_error = None

    live = not args.once and not args.json and sys.stdout.isatty()
    if live and os.name == "nt":
        os.system("")   # enables ANSI escape handling in the Windows console
    try:
        while True:
            started = time.monotonic()
            state = reader.poll()
            if map_reader is not None:
                state["map"] = map_reader.update(state)
                if map_path is not None:
                    try:
                        map_reader.write_png(map_path)
                        state["map"]["image"]["path"] = str(map_path)
                        map_error = None
                    except OSError as exc:
                        if str(exc) != map_error:
                            print(f"warning: cannot write {map_path}: {exc}", file=sys.stderr)
                        map_error = str(exc)
            if args.json:
                print(json.dumps(state, indent=2 if args.once else None, ensure_ascii=False), flush=True)
            else:
                text = render_text(state, spec)
                if live:
                    sys.stdout.write("\x1b[H\x1b[J" + text + "\n")
                    sys.stdout.flush()
                else:
                    print(text + ("\n" if not args.once else ""), flush=True)
            if args.once:
                return 0 if state["connected"] and state["game"]["ok"] else 1
            time.sleep(max(0.05, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())
