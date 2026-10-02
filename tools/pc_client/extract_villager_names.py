"""Build the app's villager-name list from your own Animal Crossing disc.

The game keeps villager names in npc_name_str_table.bin (8 bytes per name id, game charset),
inside forest_1st.arc / forest_2nd.arc on the disc, and only copies them to ARAM at runtime,
where EmuLink cannot read. Extract the archives with DolphinTool first:

    DolphinTool extract -i "Animal Crossing (USA).rvz" -o <dir> -s forest_2nd.arc
    DolphinTool extract -i "Animal Crossing (USA).rvz" -o <dir> -s forest_1st.arc
    python extract_villager_names.py <dir>/files/forest_1st.arc <dir>/files/forest_2nd.arc

It writes android/app/src/main/assets/villager_names.json: {"E000": "name", ...} for every
villager npc id (0xE000 | name id), decoded through the spec's charmap.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec" / "ac_memory_map.json"
OUT = ROOT / "android" / "app" / "src" / "main" / "assets" / "villager_names.json"
TABLE = "npc_name_str_table.bin"
NAME_LEN = 8


def rarc_files(data: bytes) -> dict[str, bytes]:
    """Every file in an uncompressed RARC archive, by name."""
    if data[:4] != b"RARC":
        raise ValueError("not a RARC archive (Yaz0-compressed archives are not supported)")
    data_header = struct.unpack_from(">I", data, 0x8)[0]
    file_data = struct.unpack_from(">I", data, 0xC)[0] + data_header
    info = data_header
    _nodes, _node_off, n_entries, entry_off, _str_len, str_off = struct.unpack_from(">6I", data, info)
    entries = info + entry_off
    strings = info + str_off
    out = {}
    for i in range(n_entries):
        _id, _hash, kind, name_off, off, size = struct.unpack_from(">HHHHII", data, entries + 0x14 * i)
        if kind & 0x0200:          # directory (or "." / "..")
            continue
        end = data.index(b"\0", strings + name_off)
        name = data[strings + name_off:end].decode("ascii", "replace")
        out[name] = data[file_data + off:file_data + off + size]
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    table = None
    for path in argv[1:]:
        files = rarc_files(Path(path).read_bytes())
        if TABLE in files:
            table = files[TABLE]
            print(f"{TABLE}: {len(table)} bytes in {path}")
            break
    if table is None:
        print(f"{TABLE} not found in {argv[1:]}")
        return 1
    charmap = json.loads(SPEC.read_text(encoding="utf-8"))["charmap"]
    names = {}
    for name_id in range(min(len(table) // NAME_LEN, 0xFF)):
        raw = table[name_id * NAME_LEN:(name_id + 1) * NAME_LEN]
        name = "".join(charmap[b] for b in raw).rstrip(" ")
        if name:
            names["%04X" % (0xE000 | name_id)] = name
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(names, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} names to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
