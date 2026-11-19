"""The version is drawn on every launcher screen; without a kit it falls back to the launcher's own."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_gui  # noqa: E402
import es_version  # noqa: E402
from es_gui import W, H  # noqa: E402


class Version(unittest.TestCase):
    def ui(self):
        d = tempfile.mkdtemp()
        return es_gui.Ui(Path(d), Path("/nonexistent-es-kit"))

    def test_matches_cmake(self):
        cm = (Path(__file__).resolve().parents[2] / "CMakeLists.txt").read_text()
        self.assertEqual(re.search(r"project\(emotionalsense\s+VERSION\s+([0-9.]+)", cm)[1], es_version.VERSION)

    def test_fallback_when_no_kit(self):
        self.assertEqual(self.ui()._version(), es_version.VERSION)

    def test_every_screen_draws_it(self):
        ui = self.ui()
        box = (W - 24 - 420, H - 34, W - 24, H - 34 + 24)
        for name in ("setup", "loading", "play", "options", "texpack"):
            ui.screen, ui.prev_img = name, None
            with_mark = ui.compose().crop(box).tobytes()
            orig = ui._version_mark
            ui._version_mark = lambda img: None
            without = ui.compose().crop(box).tobytes()
            ui._version_mark = orig
            self.assertNotEqual(with_mark, without, name)

    def test_shows_the_kit_version_when_there(self):
        ui = self.ui()
        k = Path(tempfile.mkdtemp())
        (k / "manifest.json").write_text('{"version_full": "9.9.9-test"}')
        ui.kit = k
        self.assertEqual(ui._version(), "9.9.9-test")


if __name__ == "__main__":
    unittest.main()
