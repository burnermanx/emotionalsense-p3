"""Portable layout: the data folder next to the program, nothing in the home folder."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_i18n  # noqa: E402
import es_portable as P  # noqa: E402


class PortableTest(unittest.TestCase):
    def test_appimage_uses_the_file_folder_not_the_mount(self):
        with mock.patch.dict(os.environ, {"APPIMAGE": "/games/EmotionalSense-P3-0.1.0-linux-x86_64.AppImage"}):
            os.environ.pop("EMOTIONALSENSE_DATA", None)
            self.assertEqual(P.app_dir(), Path("/games"))
            self.assertEqual(P.data_dir(), Path("/games/EmotionalSense-P3-data"))

    def test_env_override_for_developers(self):
        with mock.patch.dict(os.environ, {"EMOTIONALSENSE_DATA": "/tmp/x"}):
            self.assertEqual(P.data_dir(), Path("/tmp/x"))

    def test_not_writable_is_a_clear_error(self):
        es_i18n.set_language("en")
        with tempfile.TemporaryDirectory() as td:
            ro = Path(td) / "ro"
            ro.mkdir()
            os.chmod(ro, 0o500)
            try:
                if os.access(ro, os.W_OK):
                    self.skipTest("running as a user that ignores directory permissions")
                with self.assertRaises(P.PortableError) as cm:
                    P.check_writable(ro / P.DATA_NAME)
                self.assertIn(str(ro), str(cm.exception))
            finally:
                os.chmod(ro, 0o700)

    def test_writable_folder_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            P.check_writable(Path(td) / P.DATA_NAME)
            self.assertTrue((Path(td) / P.DATA_NAME).is_dir())

    def test_cache_env_stays_inside_the_data_folder(self):
        data = Path("/somewhere/EmotionalSense-P3-data")
        for k, v in P.cache_env(data).items():
            if k.startswith("PYTHON"):
                continue
            self.assertTrue(v.startswith(str(data)), (k, v))

    def test_migrate_copies_and_never_deletes(self):
        with tempfile.TemporaryDirectory() as td:
            old, new = Path(td) / "old", Path(td) / "new"
            (old / "ms/PSP").mkdir(parents=True)
            (old / "ms/PSP/SD.BIN").write_bytes(b"save")
            (old / "launcher.json").write_text("{}")
            new.mkdir()
            (new / "launcher.json").write_text('{"keep": 1}')
            n = P.migrate(old, new, log=lambda s: None)
            self.assertEqual(n, 1)                                   # the existing file is not overwritten
            self.assertEqual((new / "launcher.json").read_text(), '{"keep": 1}')
            self.assertEqual((new / "ms/PSP/SD.BIN").read_bytes(), b"save")
            self.assertTrue((old / "ms/PSP/SD.BIN").exists())        # the source stays

    def test_find_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            xdg = Path(td) / "share"
            (xdg / "emotionalsense" / "ms").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg)}):
                with mock.patch.object(sys, "platform", "linux"):
                    self.assertEqual(P.find_legacy(Path(td) / "new"), xdg / "emotionalsense")


if __name__ == "__main__":
    unittest.main()
