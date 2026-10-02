"""Shared test helpers: puts tools/pc_client on sys.path and builds test images."""

import array
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE.parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

FIXTURE_SPEC = HERE / "fixtures" / "spec_fixture.json"


def pattern_image(size: int = 1 << 20, game_id: bytes = b"GAFE01") -> bytearray:
    """Image whose every 4-byte word holds its own offset (big-endian)."""
    words = array.array("I", range(0, size, 4))
    if sys.byteorder == "little":
        words.byteswap()
    image = bytearray(words.tobytes())
    image[0:len(game_id)] = game_id
    image[7] = 0
    return image
