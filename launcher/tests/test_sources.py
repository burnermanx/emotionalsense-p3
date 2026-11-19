"""F1-F3: the game is recognised as soon as the file dialog returns; a data folder that already holds
the extracted disc is reused without an image; an extracted game folder next to the program is a
source (copied, never moved); incomplete or wrong folders are refused. Synthetic data only."""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_gui  # noqa: E402
import es_prepare  # noqa: E402
from es_pipeline import PipelineError  # noqa: E402


def sfo(disc_id: str, title: str = "TEST DISC") -> bytes:
    keys = b"DISC_ID\0TITLE\0"
    vals = [disc_id.encode().ljust(16, b"\0"), title.encode().ljust(16, b"\0")]
    kt = 20 + 16 * 2
    dt = kt + len(keys)
    ent = struct.pack("<HHIII", 0, 0x0204, len(disc_id) + 1, 16, 0) + struct.pack("<HHIII", 8, 0x0204, len(title) + 1, 16, 16)
    return b"\x00PSF" + struct.pack("<IIII", 0x101, kt, dt, 2) + ent + keys + b"".join(vals)


def make_tree(root: Path, disc_id: str = "ULKS46236", skip: str = "") -> Path:
    for rel in es_prepare.DISC_REQUIRED:
        if rel == skip:
            continue
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(sfo(disc_id) if rel.endswith("PARAM.SFO") else (rel.encode() * 50))
    return root


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="es-src-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class DialogReturn(Fixture):
    def test_result_is_drawn_without_any_input_event(self):
        ui = es_gui.Ui(self.tmp / "d", Path("/nonexistent-es-kit"))

        class Host:
            def ask_iso(host):
                ui.dirty = False                 # the launcher redrew under the dialog: nothing pending
                return str(self.tmp / "not-a-disc.iso")

        (self.tmp / "not-a-disc.iso").write_bytes(b"x" * 100)
        ui.screen, ui.sel["setup"] = "setup", 0
        ui.key("ok", Host())
        self.assertTrue(ui.dirty)                # set_iso ran and what it found must be painted
        self.assertEqual(ui.iso, self.tmp / "not-a-disc.iso")
        self.assertEqual(ui.iso_status[0], "err")

    def test_cancelled_dialog_changes_nothing(self):
        ui = es_gui.Ui(self.tmp / "d", Path("/nonexistent-es-kit"))

        class Host:
            def ask_iso(host):
                return ""
        ui.screen, ui.sel["setup"] = "setup", 0
        ui.key("ok", Host())
        self.assertIsNone(ui.iso)


class ExtractedDisc(Fixture):
    def test_tree_checks(self):
        self.assertIsNone(es_prepare.check_disc_tree(make_tree(self.tmp / "ok")))
        self.assertIn("EBOOT.BIN", es_prepare.check_disc_tree(make_tree(self.tmp / "a", skip="PSP_GAME/SYSDIR/EBOOT.BIN")))
        self.assertIn("another game", es_prepare.check_disc_tree(make_tree(self.tmp / "b", disc_id="ULUS10001")))
        empty = make_tree(self.tmp / "c")
        (empty / "PSP_GAME/USRDIR/STREAMV.TPA").write_bytes(b"")
        self.assertIn("STREAMV.TPA", es_prepare.check_disc_tree(empty))

    def test_data_folder_with_the_disc_needs_no_image(self):
        data = self.tmp / "EmotionalSense-P3-data"
        make_tree(data / "disc")
        self.assertFalse(es_prepare.data_ready(data))                 # no marker yet
        (data / ".extracted.json").write_text(json.dumps({"sha256": "abc"}))
        self.assertTrue(es_prepare.data_ready(data))
        p = es_prepare.Preparer(es_prepare.PrepareOptions(iso=None, data_dir=data, kit_dir=Path("/x")), log=lambda s: None)
        before = {f: f.stat().st_mtime_ns for f in (data / "disc").rglob("*") if f.is_file()}
        info = p.verify()
        p.extract(info)
        self.assertEqual(info["sha256"], "abc")                       # the marker's, nothing hashed or read
        self.assertEqual(before, {f: f.stat().st_mtime_ns for f in (data / "disc").rglob("*") if f.is_file()})

    def test_data_folder_with_a_broken_disc_is_not_ready(self):
        data = self.tmp / "d"
        make_tree(data / "disc", skip="PSP_GAME/INSDIR/DATA.TPA")
        (data / ".extracted.json").write_text("{}")
        self.assertFalse(es_prepare.data_ready(data))
        with self.assertRaises(PipelineError):
            es_prepare.Preparer(es_prepare.PrepareOptions(iso=None, data_dir=data, kit_dir=Path("/x")), log=lambda s: None).verify()

    def test_new_launcher_version_rebuilds_without_asking_for_the_iso(self):
        data = self.tmp / "d"
        make_tree(data / "disc")
        (data / ".extracted.json").write_text(json.dumps({"sha256": "abc"}))
        (data / "prepared.json").write_text(json.dumps({"launcher": {"version_full": "0.0.9"},
                                                        "iso": {"size": 5, "sha256": "abc", "title": "T"}}))
        ui = es_gui.Ui(data, Path("/nonexistent-es-kit"))
        self.assertEqual(ui.discover_sources(base=self.tmp / "none"), "data")


class GameFolder(Fixture):
    def test_found_next_to_the_program_and_copied_never_moved(self):
        app = self.tmp / "app"
        src = make_tree(app / "DJMAX extracted")
        (src / "PSP_GAME" / "extra.txt").write_text("keep me")
        data = app / "EmotionalSense-P3-data"
        found = es_prepare.find_game_folder(app, skip=("EmotionalSense-P3-data",))
        self.assertEqual(found, src)
        p = es_prepare.Preparer(es_prepare.PrepareOptions(iso=found, data_dir=data, kit_dir=Path("/x")), log=lambda s: None)
        info = p.verify()
        self.assertTrue(info["sha256"].startswith("folder-"))
        p.extract(info)
        for rel in es_prepare.DISC_REQUIRED:
            self.assertEqual((data / "disc" / rel).read_bytes(), (src / rel).read_bytes())
            self.assertTrue((src / rel).exists())                      # still there: copied, not moved
        self.assertTrue((src / "PSP_GAME" / "extra.txt").exists())
        self.assertTrue(es_prepare.data_ready(data))                  # and now the data folder stands alone
        self.assertEqual(info, es_prepare.Preparer(es_prepare.PrepareOptions(iso=found, data_dir=data, kit_dir=Path("/x")), log=lambda s: None).verify())

    def test_found_one_level_down_and_the_data_folder_is_not_a_source(self):
        app = self.tmp / "app"
        make_tree(app / "EmotionalSense-P3-data" / "disc")
        self.assertIsNone(es_prepare.find_game_folder(app, skip=("EmotionalSense-P3-data",)))
        src = make_tree(app / "Games" / "djmax")
        self.assertEqual(es_prepare.find_game_folder(app, skip=("EmotionalSense-P3-data",)), src)

    def test_incomplete_or_wrong_folders_are_refused(self):
        app = self.tmp / "app"
        make_tree(app / "half", skip="PSP_GAME/USRDIR/STREAMV.TPA")
        make_tree(app / "other", disc_id="ULUS10001")
        self.assertIsNone(es_prepare.find_game_folder(app))
        p = es_prepare.Preparer(es_prepare.PrepareOptions(iso=app / "half", data_dir=self.tmp / "d", kit_dir=Path("/x")), log=lambda s: None)
        with self.assertRaises(PipelineError) as cm:
            p.verify()
        self.assertIn("STREAMV.TPA", str(cm.exception))
        self.assertFalse((self.tmp / "d" / "disc").exists())           # nothing copied

    def test_ui_offers_it_on_the_setup_screen(self):
        app = self.tmp / "app"
        src = make_tree(app / "game")
        ui = es_gui.Ui(app / "EmotionalSense-P3-data", Path("/nonexistent-es-kit"))
        self.assertEqual(ui.discover_sources(base=app), "folder")
        self.assertEqual(ui.iso, src)
        self.assertEqual(ui.iso_status[0], "ok")
        started = []

        class Host:
            def start_prepare(h, iso):
                started.append(iso)
        ui.screen, ui.sel["setup"] = "setup", 2
        ui.key("ok", Host())
        self.assertEqual(started, [src])

    def test_nothing_found_means_the_normal_setup(self):
        ui = es_gui.Ui(self.tmp / "d", Path("/nonexistent-es-kit"))
        self.assertIsNone(ui.discover_sources(base=self.tmp))
        self.assertIsNone(ui.iso)


if __name__ == "__main__":
    unittest.main()
