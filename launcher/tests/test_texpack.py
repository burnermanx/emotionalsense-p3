"""AI textures: pinned downloads, safe unpacking, which pack the game gets."""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_texpack as T  # noqa: E402


class TexPackTest(unittest.TestCase):
    def test_every_tool_is_pinned_by_sha256_and_https(self):
        for key, tools in T.TOOLS.items():
            self.assertEqual({t.name for t in tools}, {"waifu2x", "realesrgan"}, key)
            for t in tools:
                self.assertTrue(t.url.startswith("https://github.com/"), t.url)
                self.assertRegex(t.sha256, r"^[0-9a-f]{64}$")

    def test_unsupported_platform_has_no_key(self):
        with mock.patch("platform.machine", return_value="aarch64"):
            self.assertIsNone(T.platform_key())
        with mock.patch("platform.machine", return_value="x86_64"), mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(T.platform_key(), "linux-x86_64")

    def test_unpack_refuses_a_path_outside_the_folder(self):
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "evil.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("../escape.txt", "x")
            with self.assertRaises(T.TexPackError):
                T.unpack(z, Path(td) / "out")
            self.assertFalse((Path(td) / "escape.txt").exists())

    def test_download_refuses_a_wrong_hash(self):
        class R(io.BytesIO):
            headers = {"Content-Length": "3"}
            def __enter__(self): return self
            def __exit__(self, *a): pass
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", return_value=R(b"abc")):
            with self.assertRaises(T.TexPackError):
                T.download("https://example.invalid/x.zip", "0" * 64, Path(td) / "x.zip")
            self.assertFalse((Path(td) / "x.zip").exists())

    def test_pack_env_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(T, "ENABLED", False):
            data = Path(td)
            (data / "textures").mkdir()
            (data / "textures" / T.COMPLETE_MARKER).write_text("{}")
            self.assertEqual(T.pack_env(data, {"texpack": "on"}), {})               # the game picks <data>/textures itself
            self.assertEqual(T.pack_env(data, {"texture_pack": "/x"})["EMOTIONALSENSE_TEX_PACK"], "/x")

    def test_pack_env(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(T, "ENABLED", True):
            data = Path(td)
            self.assertEqual(T.pack_env(data, {"texpack": "on"}), {})               # no pack folder: the game finds none
            (data / "textures").mkdir()
            self.assertIn("textures-disabled", T.pack_env(data, {"texpack": "off"})["EMOTIONALSENSE_TEX_PACK"])
            self.assertIn("textures-disabled", T.pack_env(data, {"texpack": "on"})["EMOTIONALSENSE_TEX_PACK"])  # unfinished
            (data / "textures" / T.COMPLETE_MARKER).write_text("{}")
            self.assertEqual(T.pack_env(data, {"texpack": "on"})["EMOTIONALSENSE_TEX_PACK"], str(data / "textures"))
            self.assertEqual(T.pack_env(data, {"texture_pack": "/x"})["EMOTIONALSENSE_TEX_PACK"], "/x")

    def test_routes_are_listed(self):
        routes = T.read_routes()
        self.assertIn(("boot", 460), routes)
        self.assertTrue(all((T.routes_dir() / (n + ".script")).exists() for n, _ in routes))
        self.assertTrue((T.routes_dir() / "profile.script").exists())


if __name__ == "__main__":
    unittest.main()
