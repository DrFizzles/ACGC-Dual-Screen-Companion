"""Dashboard rendering and CLI (--once / --json / --from-dump)."""

import contextlib
import io
import json
import os
import tempfile
import unittest

import support
import acmap
import dashboard
from emulink import MemoryImageSource
from mock_server import build_synthetic_image

SPEC = acmap.load_spec(support.FIXTURE_SPEC)


class RenderTest(unittest.TestCase):
    def test_in_town_text(self):
        image, exp = build_synthetic_image(SPEC, "town")
        state = acmap.ACReader(MemoryImageSource(image), SPEC).poll()
        text = dashboard.render_text(state, SPEC)
        for needle in ("Maple", "Ana (player 1)", "Thu 2026-10-01 13:05:22", "Rain (Normal)",
                       "wallet 12,345", "bank 67,890", "loan 0", "[Thu 310]", "Villagers (3)",
                       "E001", dashboard.EMPTY, f"{exp['unknown_id']:04X} (unknown)", "[Wrapped]"):
            self.assertIn(needle, text)
        for item_id in exp["pockets"]:
            if item_id in exp["item_names"]:
                self.assertIn(exp["item_names"][item_id], text)

    def test_not_in_town_text(self):
        image, _ = build_synthetic_image(SPEC, "title")
        state = acmap.ACReader(MemoryImageSource(image), SPEC).poll()
        text = dashboard.render_text(state, SPEC)
        self.assertIn("Not in town", text)
        self.assertNotIn("Maple", text)
        self.assertNotIn("Pockets", text)


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image, _ = build_synthetic_image(SPEC, "town")
        fd, cls.dump = tempfile.mkstemp(suffix=".raw")
        with os.fdopen(fd, "wb") as fh:
            fh.write(image)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.dump)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = dashboard.main(list(args))
        return code, out.getvalue()

    def test_once_json_from_dump(self):
        code, out = self.run_main("--once", "--json", "--from-dump", self.dump,
                                  "--spec", str(support.FIXTURE_SPEC))
        self.assertEqual(code, 0)
        state = json.loads(out)
        self.assertEqual(state["status"], acmap.ST_IN_TOWN)
        self.assertEqual(state["globals"]["town_name"], "Maple")
        self.assertEqual(state["player"]["name"], "Ana")

    def test_once_text_from_dump(self):
        code, out = self.run_main("--once", "--from-dump", self.dump, "--spec", str(support.FIXTURE_SPEC))
        self.assertEqual(code, 0)
        self.assertIn("In town as player 1", out)

    def test_missing_spec(self):
        code, _ = self.run_main("--once", "--from-dump", self.dump, "--spec", self.dump + ".nope.json")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
