"""The Android town-map cross-check cases (crosscheck_export.py, map_cases.json) are meaningful and current."""

import json
import unittest

import support  # noqa: F401  (puts tools/pc_client/ on sys.path)
import acmap
import crosscheck_export as xc

EXPORTED = xc.DEFAULT_OUT / xc.MAP_CASES_FILE


class MapCrossCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = acmap.load_spec()
        cls.images = xc.map_images(cls.spec)
        cls.results = {name: xc.map_decode(cls.spec, img) for name, img in cls.images.items()}

    def test_cases_cover_the_map_paths(self):
        r = self.results
        self.assertEqual(set(r), {"map_town", "map_moved", "map_skip", "map_no_textures"})
        for name, e in r.items():
            self.assertEqual(e["status"], "ok", name)
            self.assertEqual(len(e["acres"]), 30, name)
            self.assertEqual(len(e["houses"]), 3, name)
            self.assertIsNotNone(e["player"], name)
            self.assertEqual(e["highlight"], e["player"]["block"], name)
        # every case but the no-texture one compares the composition
        self.assertTrue(all(r[n]["compose"] for n in ("map_town", "map_moved", "map_skip")))
        self.assertIsNone(r["map_no_textures"]["compose"])
        self.assertTrue(all(a[3] is None for a in r["map_no_textures"]["acres"]))
        # the skipped border type shifts the acre list and pads the last cell
        town, skip = r["map_town"]["acres"], r["map_skip"]["acres"]
        self.assertEqual([a[:3] for a in skip[:-1]], [a[:3] for a in town[1:]])
        self.assertEqual(skip[-1][1:3], [None, None])
        # the moved case: another acre, a non-cardinal facing, a translucent icon tinted per tier
        moved = r["map_moved"]
        self.assertNotEqual(moved["player"]["block"], r["map_town"]["player"]["block"])
        self.assertNotEqual(moved["player"]["facing"] % 0x4000, 0)
        self.assertEqual(sorted(moved["icons"]), ["0", "1", "2"])
        self.assertNotEqual(moved["icons"], r["map_town"]["icons"])

    @unittest.skipUnless(EXPORTED.exists(), f"{EXPORTED} not present")
    def test_exported_file_is_current(self):
        with open(EXPORTED, encoding="utf-8") as fh:
            exported = {c["name"]: c for c in json.load(fh)["cases"]}
        self.assertEqual(set(exported), set(self.results))
        for name, image in self.images.items():
            fresh = json.loads(json.dumps({"regions": xc.sparse(image), "expected": self.results[name]}))
            self.assertEqual(exported[name]["regions"], fresh["regions"],
                             f"{name}: {EXPORTED.name} is stale: rerun tools/pc_client/crosscheck_export.py")
            self.assertEqual(exported[name]["expected"], fresh["expected"],
                             f"{name}: {EXPORTED.name} is stale: rerun tools/pc_client/crosscheck_export.py")


if __name__ == "__main__":
    unittest.main()
