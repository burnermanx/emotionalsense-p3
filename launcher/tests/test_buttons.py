"""Button styles: the table, its composition with the confirm option (5 styles x 2 confirmations),
the controller classification and the style resolution. The game's C table has the same test
(game/tests/test_button_style.c)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_buttons as b  # noqa: E402


class Table(unittest.TestCase):
    def test_letters_by_position(self):
        self.assertEqual([b.face("xbox", p)[0] for p in b.POSITIONS], ["A", "B", "X", "Y"])
        self.assertEqual([b.face("steamdeck", p)[0] for p in b.POSITIONS], ["A", "B", "X", "Y"])
        self.assertEqual([b.face("switch", p)[0] for p in b.POSITIONS], ["B", "A", "Y", "X"])
        self.assertEqual(b.face("playstation", "cross"), (None, None))

    def test_colors(self):
        cols = [b.face("xbox", p)[1] for p in b.POSITIONS]
        self.assertEqual(len(set(cols)), 4)
        for st in ("steamdeck", "switch"):
            self.assertTrue(all(b.face(st, p)[1] == b.WHITE for p in b.POSITIONS))

    def test_confirm_composition_all_ten(self):
        # (style, confirm) -> letters of OK and Back
        want = {
            ("playstation", "circle"): (None, None), ("playstation", "cross"): (None, None),
            ("xbox", "circle"): ("B", "A"), ("xbox", "cross"): ("A", "B"),
            ("steamdeck", "circle"): ("B", "A"), ("steamdeck", "cross"): ("A", "B"),
            ("switch", "circle"): ("A", "B"), ("switch", "cross"): ("B", "A"),
        }
        n = 0
        for style in b.STYLES[1:]:
            for mode in ("circle", "cross"):
                ok = b.action_face(style, "ok", mode)
                back = b.action_face(style, "back", mode)
                self.assertEqual((ok[1], back[1]), want[(style, mode)])
                self.assertEqual(ok[0], "cross" if mode == "cross" else "circle")
                self.assertNotEqual(ok[0], back[0])
                n += 1
        self.assertEqual(n, 8)          # auto is resolved to one of the four before drawing
        for mode in ("circle", "cross"):  # the xbox colours follow the button, not the action
            self.assertEqual(b.action_face("xbox", "ok", mode)[2], b.XBOX_COLORS["A" if mode == "cross" else "B"])

    def test_pills(self):
        # START, SELECT, left, right shoulder per style; no dependence on the confirm option
        want = {
            "playstation": ("", "", "L1", "R1"),
            "xbox": ("M", "V", "LB", "RB"),
            "steamdeck": ("M", "V", "L1", "R1"),
            "switch": ("+", "-", "L", "R"),
        }
        for style, labels in want.items():
            self.assertEqual(tuple(b.pill(style, w) for w in b.PILLS), labels, style)
        self.assertEqual(b.pill("auto", "left"), "L1")        # auto without a pad is PlayStation

    def test_resolve(self):
        self.assertEqual(b.resolve_style("xbox", "switch"), "xbox")
        self.assertEqual(b.resolve_style("auto", "switch"), "switch")
        self.assertEqual(b.resolve_style("auto", None), "playstation")

    def test_classify(self):
        self.assertEqual(b.classify_pad(1), "xbox")                      # Xbox 360
        self.assertEqual(b.classify_pad(2), "xbox")                      # Xbox One
        self.assertEqual(b.classify_pad(0), "xbox")                      # generic
        self.assertEqual([b.classify_pad(x) for x in (3, 4, 7)], ["playstation"] * 3)
        self.assertEqual([b.classify_pad(x) for x in (5, 11, 12, 13)], ["switch"] * 4)
        self.assertEqual(b.classify_pad(1, 0x28DE, 0x1205), "steamdeck")  # the Deck's own pad
        self.assertEqual(b.classify_pad(1, 0x28DE, 0x11FF), "xbox")       # Steam's virtual pad, not on a Deck
        self.assertEqual(b.classify_pad(1, 0x28DE, 0x11FF, deck_env=True), "steamdeck")
        self.assertEqual(b.classify_pad(4, 0x28DE, 0x11FF, deck_env=True), "playstation")   # real type recovered

    def test_names(self):
        self.assertEqual(b.letter_of("switch", "cross"), "B")
        self.assertEqual(b.letter_of("playstation", "cross"), "X")


if __name__ == "__main__":
    unittest.main()
