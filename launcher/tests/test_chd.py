"""CHD disc images (es_chd). Needs libchdr (ES_LIBCHDR, or the kit's bin/) and chdman (ES_CHDMAN or
PATH); each test is skipped when one is missing. The disc is a small synthetic ISO9660 made here:
no game data is involved."""
import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_chd  # noqa: E402
import es_pipeline  # noqa: E402

CHDMAN = os.environ.get("ES_CHDMAN") or shutil.which("chdman")


def dirrec(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    n = len(name)
    ln = 33 + n + (1 - n % 2)
    r = bytearray(ln)
    r[0] = ln
    struct.pack_into("<I", r, 2, lba)
    struct.pack_into(">I", r, 6, lba)
    struct.pack_into("<I", r, 10, size)
    struct.pack_into(">I", r, 14, size)
    r[25] = 2 if is_dir else 0
    r[32] = n
    r[33:33 + n] = name
    return bytes(r)


def make_iso(sectors: int = 400) -> bytes:
    rnd = random.Random(7)
    iso = bytearray(rnd.randbytes(2048 * sectors))
    for s in range(16, 22):
        iso[s * 2048:(s + 1) * 2048] = bytes(2048)
    pvd = bytearray(2048)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    pvd[156:156 + 34] = dirrec(b"\0", 18, 2048, True)
    iso[16 * 2048:17 * 2048] = pvd
    root = dirrec(b"\0", 18, 2048, True) + dirrec(b"\1", 18, 2048, True) + dirrec(b"PSP_GAME", 19, 2048, True)
    iso[18 * 2048:18 * 2048 + len(root)] = root
    sub = dirrec(b"\0", 19, 2048, True) + dirrec(b"\1", 18, 2048, True) + dirrec(b"PARAM.SFO;1", 20, 5000, False)
    iso[19 * 2048:19 * 2048 + len(sub)] = sub
    return bytes(iso)


def have_lib() -> bool:
    return es_chd.find_lib() is not None


@unittest.skipUnless(CHDMAN and have_lib(), "needs chdman and libchdr (ES_CHDMAN, ES_LIBCHDR)")
class ChdImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="es-chd-test-"))
        cls.iso_bytes = make_iso()
        cls.iso = cls.tmp / "disc.iso"
        cls.iso.write_bytes(cls.iso_bytes)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def chdman(self, *args):
        subprocess.run([CHDMAN, *args, "-f"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def check(self, chd: Path):
        with es_chd.open_image(chd) as f:
            self.assertEqual(f.size, len(self.iso_bytes))
            self.assertEqual(f.read(), self.iso_bytes)
            rnd = random.Random(3)
            for _ in range(200):
                off = rnd.randrange(0, len(self.iso_bytes) - 1)
                n = rnd.randrange(1, 9000)
                f.seek(off)
                self.assertEqual(f.read(n), self.iso_bytes[off:off + n])
            f.seek(len(self.iso_bytes) - 10)
            self.assertEqual(len(f.read(100)), 10)
        iso = es_pipeline.Iso9660(chd)                # the ISO9660 reader works through the CHD too
        try:
            entries = {e.path: e for e in iso.walk()}
            self.assertEqual(iso.read_file(entries["PSP_GAME/PARAM.SFO"]), self.iso_bytes[20 * 2048:20 * 2048 + 5000])
        finally:
            iso.close()

    def test_dvd_default_and_zstd(self):
        for name, extra in (("dvd", []), ("dvdz", ["-c", "zstd"])):
            out = self.tmp / (name + ".chd")
            self.chdman("createdvd", "-i", str(self.iso), "-o", str(out), *extra)
            self.check(out)

    def test_cd_cooked_mode1(self):
        cue = self.tmp / "m1.cue"
        cue.write_text('FILE "disc.iso" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
        out = self.tmp / "m1.chd"
        self.chdman("createcd", "-i", str(cue), "-o", str(out))
        self.check(out)

    def test_cd_raw_mode1_and_mode2(self):
        sync = b"\0" + b"\xff" * 10 + b"\0"
        for mode, head, cuetype in ((1, sync + b"\0\0\0\x01", "MODE1/2352"), (2, sync + b"\0\0\0\x02" + b"\0\0\x08\0" * 2, "MODE2/2352")):
            raw = bytearray()
            for i in range(len(self.iso_bytes) // 2048):
                sec = self.iso_bytes[i * 2048:(i + 1) * 2048]
                raw += head + sec + bytes(2352 - len(head) - 2048)
            b = self.tmp / f"raw{mode}.bin"
            b.write_bytes(bytes(raw))
            cue = self.tmp / f"raw{mode}.cue"
            cue.write_text(f'FILE "{b.name}" BINARY\n  TRACK 01 {cuetype}\n    INDEX 01 00:00:00\n')
            out = self.tmp / f"raw{mode}.chd"
            self.chdman("createcd", "-i", str(cue), "-o", str(out))
            self.check(out)

    def test_audio_first_track_is_unsupported(self):
        (self.tmp / "a.bin").write_bytes(os.urandom(2352 * 300))
        (self.tmp / "a.cue").write_text('FILE "a.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n')
        self.chdman("createcd", "-i", str(self.tmp / "a.cue"), "-o", str(self.tmp / "a.chd"))
        with self.assertRaises(es_chd.ChdError) as cm:
            es_chd.open_image(self.tmp / "a.chd")
        self.assertEqual(cm.exception.kind, "unsupported")

    def test_truncated_is_corrupt(self):
        out = self.tmp / "t.chd"
        self.chdman("createdvd", "-i", str(self.iso), "-o", str(out), "-c", "none")
        data = out.read_bytes()
        cut = self.tmp / "cut.chd"
        cut.write_bytes(data[:len(data) // 2])
        with self.assertRaises(es_chd.ChdError) as cm:
            with es_chd.open_image(cut) as f:
                f.read()
        self.assertEqual(cm.exception.kind, "corrupt")
        cut.write_bytes(data[:90])
        with self.assertRaises(es_chd.ChdError) as cm:
            es_chd.open_image(cut)
        self.assertIn(cm.exception.kind, ("corrupt", "invalid"))

    def test_hash_equals_iso_hash(self):
        out = self.tmp / "h.chd"
        self.chdman("createdvd", "-i", str(self.iso), "-o", str(out))
        h = hashlib.sha256()
        with es_chd.open_image(out) as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
        self.assertEqual(h.hexdigest(), hashlib.sha256(self.iso_bytes).hexdigest())


@unittest.skipUnless(have_lib(), "needs libchdr")
class NotChd(unittest.TestCase):
    def test_garbage_and_text(self):
        d = Path(tempfile.mkdtemp(prefix="es-chd-test-"))
        try:
            (d / "g.chd").write_bytes(b"MComprHD" + os.urandom(4000))
            (d / "t.chd").write_bytes(b"hello" * 100)
            (d / "e.chd").write_bytes(b"")
            for n in ("g", "t", "e"):
                with self.assertRaises(es_chd.ChdError) as cm:
                    es_chd.open_image(d / (n + ".chd"))
                self.assertEqual(cm.exception.kind, "invalid", n)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_an_iso_is_opened_as_a_plain_file(self):
        d = Path(tempfile.mkdtemp(prefix="es-chd-test-"))
        try:
            (d / "x.iso").write_bytes(b"abc")
            with es_chd.open_image(d / "x.iso") as f:
                self.assertEqual(f.read(), b"abc")
                self.assertEqual(es_chd.image_size(f), 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Messages(unittest.TestCase):
    def test_errors_are_translated_in_every_language(self):
        import es_i18n
        for lang in es_i18n.LANGS:
            es_i18n.set_language(lang)
            for kind in ("nolib", "invalid", "corrupt", "unsupported"):
                msg = str(es_chd.ChdError(kind, "x"))
                self.assertNotIn("err.chd", msg)
                self.assertTrue(msg)
        es_i18n.set_language("en")


if __name__ == "__main__":
    unittest.main()
