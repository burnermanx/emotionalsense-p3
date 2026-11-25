"""Run: python3 -m unittest discover launcher/tests"""
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "release_guard", Path(__file__).resolve().parents[2] / "scripts" / "release_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class Guard(unittest.TestCase):
    def test_clean_launcher_layout(self):
        names = ["EmotionalSense-P3/EmotionalSense-P3", "EmotionalSense-P3/kit/bin/pspdecrypt",
                 "EmotionalSense-P3/kit/runtime/game/host.c", "EmotionalSense-P3/kit/deps/lib/libSDL2-2.0.so.0",
                 "EmotionalSense-P3/_internal/locales/ko.json", "EmotionalSense-P3/licenses/OFL-exo2.txt"]
        self.assertEqual(guard.violations(names), [])

    def test_each_forbidden_kind(self):
        for n in ["gen/djmax_funcs_01.c", "x/djmax_0.c", "a/eboot.elf", "PSP_GAME/SYSDIR/EBOOT.BIN",
                  "game.iso", "ICON0.PNG", "PARAM.SFO", "DATA.TPA", "ms/PSP/SAVEDATA/x/SD.BIN",
                  "game/emotionalsense", "game/emotionalsense.exe", "game/EmotionalSense-P3-game",
                  "game/EmotionalSense-P3-game.exe", "disc/PSP_GAME/x.txt",
                  "module/readme", "prepared.json", "libpsprecomp.a"]:
            self.assertTrue(guard.violations([n]), n)

    def test_a_folder_named_like_the_game_is_not_the_game(self):
        """The Android sources live in io/emotionalsense/: the source tarball must pass, a file of that name must not."""
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as td:
            tgz = Path(td) / "emotionalsense-0.2.0-source.tar.gz"
            with tarfile.open(tgz, "w:gz") as t:
                d = tarfile.TarInfo("src/io/emotionalsense")
                d.type = tarfile.DIRTYPE
                t.addfile(d)
                f = tarfile.TarInfo("src/io/emotionalsense/Main.kt")
                f.size = 2
                t.addfile(f, io.BytesIO(b"ok"))
            self.assertEqual(guard.violations(guard.listing(tgz)), [])
            bad = Path(td) / "bad.tar.gz"
            with tarfile.open(bad, "w:gz") as t:
                f = tarfile.TarInfo("game/emotionalsense")
                f.size = 2
                t.addfile(f, io.BytesIO(b"ok"))
            self.assertTrue(guard.violations(guard.listing(bad)))

    def test_artefact_names(self):
        for ok in ("EmotionalSense-P3-0.1.0-linux-x86_64.AppImage", "EmotionalSense-P3-0.1.0-linux-x86_64.tar.gz",
                   "EmotionalSense-P3-0.1.0-linux-aarch64.AppImage", "EmotionalSense-P3-0.1.0-linux-aarch64.tar.gz",
                   "EmotionalSense-P3-0.1.0-windows-x86_64.zip", "EmotionalSense-P3-0.1.0-windows-arm64.zip",
                   "EmotionalSense-P3-0.2.0-android-arm64.apk", "EmotionalSense-P3-0.2.0-android-x86_64.apk"):
            self.assertIsNone(guard.name_problem(Path("out") / ok), ok)
        self.assertIsNone(guard.name_problem(Path("out/emotionalsense-0.1.0-source.tar.gz")))
        self.assertTrue(guard.name_problem(Path("out/EmotionalSense-P3-0.1.0-x86_64.AppImage")))     # no system
        self.assertTrue(guard.name_problem(Path("out/EmotionalSense-P3-0.1.0-win64.zip")))           # old name
        self.assertTrue(guard.name_problem(Path("out/EmotionalSense-Launcher-x86_64.AppImage")))  # a stale name
        self.assertTrue(guard.name_problem(Path("out/EmotionalSense-P3-0.2.0-android.apk")))       # missing arch
        self.assertTrue(guard.name_problem(Path("out/EmotionalSense-P3-0.2.0-android-aarch64.apk")))  # wrong arch
        self.assertTrue(guard.name_problem(Path("out/bad.apk")))                                   # missing prefix

    def test_help(self):
        self.assertEqual(guard.main(["--help"]), 0)
        self.assertEqual(guard.main(["-h"]), 0)

    def test_zip_listing(self):
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "a.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("ok/readme.txt", "x")
                f.writestr("bad/djmax_funcs_00.c", "x")
            self.assertEqual(len(guard.violations(guard.listing(z))), 1)

    def test_apk_listing(self):
        with tempfile.TemporaryDirectory() as td:
            clean = Path(td) / "EmotionalSense-P3-0.2.0-android-arm64.apk"
            with zipfile.ZipFile(clean, "w") as f:
                f.writestr("AndroidManifest.xml", "<manifest/>")
                f.writestr("lib/arm64-v8a/libSDL2.so", "x")
                f.writestr("lib/arm64-v8a/libpsprecomp.so", "x")
            self.assertEqual(guard.violations(guard.listing(clean)), [])
            self.assertEqual(guard.main([str(clean)]), 0)

            bad = Path(td) / "bad.apk"
            with zipfile.ZipFile(bad, "w") as f:
                f.writestr("AndroidManifest.xml", "<manifest/>")
                f.writestr("lib/arm64-v8a/libemotionalsense_game.so", "x")
                f.writestr("assets/eboot.elf", "x")
            hits = guard.violations(guard.listing(bad))
            self.assertEqual(len(hits), 2)
            self.assertEqual(guard.main([str(bad)]), 1)

            bad_name = Path(td) / "EmotionalSense-P3-0.2.0-android.apk"
            with zipfile.ZipFile(bad_name, "w") as f:
                f.writestr("AndroidManifest.xml", "<manifest/>")
            self.assertEqual(guard.main([str(bad_name)]), 1)


if __name__ == "__main__":
    unittest.main()
