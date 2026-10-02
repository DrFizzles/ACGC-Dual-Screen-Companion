"""The Android texture vectors (texture_vectors_export.py) are complete and current."""

import json
import unittest

import support  # noqa: F401  (puts tools/pc_client on sys.path)
import gctex
import texture_vectors_export as tv

EXPORTED = tv.DEFAULT_OUT / tv.FILE_NAME


class TextureVectorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = tv.texture_vectors()

    def test_every_format_and_tlut_is_covered(self):
        names = [c["name"] for c in self.vectors["cases"]]
        for fmt in gctex.FORMATS:
            self.assertTrue(any(n.startswith(fmt + "_") for n in names), fmt)
        for t in gctex.TLUT_FORMATS:
            self.assertTrue(any(n.startswith("C4_") and n.endswith("_" + t) for n in names), t)

    def test_hand_cases_decode_as_documented(self):
        cases = {c["name"]: c for c in self.vectors["cases"]}
        i4 = cases["hand_I4_blocks"]["expected"]
        self.assertEqual(i4[0:2], ["11111111", "FFFFFFFF"])     # high nibble = left pixel
        self.assertEqual(i4[16], "22222222")                     # row 1 of block 0
        self.assertEqual(i4[8], "FFFFFFFF")                      # block 1 starts at x = 8
        ia4 = cases["hand_IA4"]["expected"]
        self.assertEqual(ia4[0:2], ["FF000000", "00FFFFFF"])
        self.assertEqual(ia4[8], "55AAAAAA")
        self.assertEqual(cases["hand_RGBA8"]["expected"][0], "80112233")
        self.assertEqual(cases["hand_C8_short_palette"]["expected"][2], "00000000")
        self.assertEqual(set(cases["hand_RGB5A3_padding"]["expected"]), {"FF0000FF"})
        self.assertEqual(cases["hand_C14X2"]["expected"][0:2], ["FF00FF00", "FF0000FF"])   # 0x4001 & 0x3FFF = 1

    @unittest.skipUnless(EXPORTED.exists(), f"{EXPORTED} not present")
    def test_exported_file_is_current(self):
        with open(EXPORTED, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), self.vectors,
                             f"{EXPORTED.name} is stale: rerun tools/pc_client/texture_vectors_export.py")


if __name__ == "__main__":
    unittest.main()
