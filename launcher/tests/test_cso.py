"""CSO disc images (es_cso): CISO v1/v2 read by sector like the ISO, byte for byte. The disc is a
small synthetic ISO9660 made here (see test_chd.make_iso): no game data is involved. Set
ES_REAL_ISO_COPY to a COPY of the game's ISO to also check a full-size CSO (never the original)."""
import hashlib
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import es_chd  # noqa: E402
import es_cso  # noqa: E402
import es_i18n  # noqa: E402
import es_pipeline  # noqa: E402
from test_chd import make_iso  # noqa: E402


def write_cso(data: bytes, path: Path, block: int = 2048, version: int = 1, shift: int = 0, magic: bytes = b"CISO",
              level: int = 9) -> None:
    """A minimal CISO writer for the tests: raw deflate per block, stored as is when that is not smaller."""
    n = (len(data) + block - 1) // block
    body = bytearray()
    index = []
    base = 24 + 4 * (n + 1)
    for i in range(n):
        blk = data[i * block:(i + 1) * block].ljust(block, b"\0")
        while (base + len(body)) % (1 << shift):
            body.append(0)
        off = (base + len(body)) >> shift
        c = zlib.compressobj(level, zlib.DEFLATED, -15)
        z = c.compress(blk) + c.flush()
        if len(z) < len(blk):
            index.append(off)
            body += z
        else:
            index.append(off | 0x80000000)
            body += blk
    while (base + len(body)) % (1 << shift):
        body.append(0)
    index.append((base + len(body)) >> shift)
    path.write_bytes(magic + struct.pack("<IQIBB2x", 24, len(data), block, version, shift) +
                     struct.pack(f"<{n + 1}I", *index) + bytes(body))


class CsoImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="es-cso-test-"))
        cls.iso_bytes = make_iso()                 # random sectors: many blocks are incompressible (stored)
        # and a compressible stretch, so both kinds of block are present
        b = bytearray(cls.iso_bytes)
        b[100 * 2048:140 * 2048] = bytes(40 * 2048)
        cls.iso_bytes = bytes(b)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, path: Path):
        with es_chd.open_image(path) as f:
            self.assertIsInstance(f, es_cso.CsoFile)
            self.assertEqual(es_chd.image_size(f), len(self.iso_bytes))
            self.assertEqual(f.read(), self.iso_bytes)
            rnd = random.Random(5)
            for _ in range(300):
                off = rnd.randrange(0, len(self.iso_bytes) - 1)
                n = rnd.randrange(1, 9000)
                f.seek(off)
                self.assertEqual(f.read(n), self.iso_bytes[off:off + n])
            f.seek(-10, 2)
            self.assertEqual(f.read(100), self.iso_bytes[-10:])
            f.seek(len(self.iso_bytes) + 50)
            self.assertEqual(f.read(10), b"")

    def test_v1(self):
        p = self.tmp / "v1.cso"
        write_cso(self.iso_bytes, p)
        self.check(p)

    def test_v2_with_index_shift(self):
        p = self.tmp / "v2.cso"
        write_cso(self.iso_bytes, p, version=2, shift=2)
        self.check(p)

    def test_other_block_size_and_an_odd_length(self):
        p = self.tmp / "b.cso"
        data = self.iso_bytes[:-777]
        write_cso(data, p, block=4096, shift=1)
        with es_cso.CsoFile(p) as f:
            self.assertEqual(f.size, len(data))
            self.assertEqual(f.read(), data)

    def test_detected_by_content_not_by_name(self):
        p = self.tmp / "noext.bin"
        write_cso(self.iso_bytes, p)
        self.assertTrue(es_cso.is_cso(p))
        with es_chd.open_image(p) as f:
            self.assertIsInstance(f, es_cso.CsoFile)

    def test_iso9660_reads_through_it(self):
        p = self.tmp / "iso.cso"
        write_cso(self.iso_bytes, p)
        iso = es_pipeline.Iso9660(p)
        try:
            self.assertIn("PSP_GAME/PARAM.SFO", {e.path for e in iso.walk()})
        finally:
            iso.close()

    def test_extraction_and_hash_match_the_iso(self):
        p = self.tmp / "h.cso"
        write_cso(self.iso_bytes, p)
        h = hashlib.sha256()
        with es_chd.open_image(p) as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
        self.assertEqual(h.hexdigest(), hashlib.sha256(self.iso_bytes).hexdigest())

    def test_a_plain_iso_is_still_a_file(self):
        p = self.tmp / "plain.iso"
        p.write_bytes(self.iso_bytes)
        with es_chd.open_image(p) as f:
            self.assertFalse(isinstance(f, es_cso.CsoFile))
            self.assertEqual(es_chd.image_size(f), len(self.iso_bytes))

    def test_damaged_files_give_a_clear_error(self):
        p = self.tmp / "bad.cso"
        write_cso(self.iso_bytes, p)
        raw = p.read_bytes()
        (self.tmp / "trunc.cso").write_bytes(raw[:len(raw) // 2])
        with self.assertRaises(es_cso.CsoError) as cm:
            with es_cso.CsoFile(self.tmp / "trunc.cso") as f:
                f.read()
        self.assertEqual(cm.exception.kind, "corrupt")
        (self.tmp / "hdr.cso").write_bytes(b"CISO" + b"\xff" * 20)
        with self.assertRaises(es_cso.CsoError) as cm:
            es_cso.CsoFile(self.tmp / "hdr.cso")
        self.assertEqual(cm.exception.kind, "invalid")
        # a block that is not deflate
        bad = bytearray(raw)
        idx0 = struct.unpack_from("<I", bad, 24)[0]
        if not idx0 & 0x80000000:
            bad[idx0:idx0 + 8] = b"\xff" * 8
            (self.tmp / "z.cso").write_bytes(bytes(bad))
            with self.assertRaises(es_cso.CsoError):
                with es_cso.CsoFile(self.tmp / "z.cso") as f:
                    f.read(2048)

    def test_zso_is_refused_with_a_translated_message(self):
        p = self.tmp / "game.zso"
        write_cso(self.iso_bytes, p, magic=b"ZISO")
        if es_cso._lz4() is not None:
            self.skipTest("lz4 is installed here")
        for lang in es_i18n.LANGS:
            es_i18n.set_language(lang)
            with self.assertRaises(es_cso.CsoError) as cm:
                es_cso.CsoFile(p)
            self.assertEqual(cm.exception.kind, "zso")
            self.assertIn("ZSO", str(cm.exception), lang)
        es_i18n.set_language("en")

    @unittest.skipUnless(os.environ.get("ES_REAL_ISO_COPY"), "set ES_REAL_ISO_COPY to a copy of the ISO")
    def test_full_size_copy(self):
        src = Path(os.environ["ES_REAL_ISO_COPY"])
        p = self.tmp / "full.cso"
        data = src.read_bytes()
        write_cso(data, p, level=1)
        h1, h2 = hashlib.sha256(data), hashlib.sha256()
        with es_chd.open_image(p) as f:
            while True:
                b = f.read(8 << 20)
                if not b:
                    break
                h2.update(b)
        self.assertEqual(h1.hexdigest(), h2.hexdigest())


if __name__ == "__main__":
    unittest.main()
