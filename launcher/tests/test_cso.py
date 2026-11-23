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

def lz4_compress(src: bytes) -> bytes:
    """A small greedy LZ4 block encoder for the tests (real matches, overlapping ones included)."""
    n = len(src)
    out = bytearray()
    table = {}
    i = anchor = 0

    def emit(lits: bytes, off: int = 0, mlen: int = 0):
        tok = min(len(lits), 15) << 4 | (min(mlen - 4, 15) if mlen else 0)
        out.append(tok)
        r = len(lits) - 15
        if len(lits) >= 15:
            while r >= 255:
                out.append(255)
                r -= 255
            out.append(r)
        out.extend(lits)
        if mlen:
            out.extend(struct.pack("<H", off))
            r = mlen - 4 - 15
            if mlen - 4 >= 15:
                while r >= 255:
                    out.append(255)
                    r -= 255
                out.append(r)

    while i + 12 <= n:                      # the last 5 bytes are literals, a match ends 12 before the end
        key = src[i:i + 4]
        j = table.get(key, -1)
        table[key] = i
        if j >= 0 and i - j <= 65535:
            m = 4
            while i + m < n - 5 and src[j + m] == src[i + m]:
                m += 1
            emit(src[anchor:i], i - j, m)
            i += m
            anchor = i
        else:
            i += 1
    emit(src[anchor:])
    return bytes(out)


def ref_lz4():
    """The system liblz4's LZ4_compress_HC as a compress function (the reference compressor), or None."""
    import ctypes
    import ctypes.util
    name = ctypes.util.find_library("lz4")
    if not name:
        return None
    lib = ctypes.CDLL(name)

    def comp(src: bytes) -> bytes:
        dst = ctypes.create_string_buffer(len(src) + len(src) // 255 + 32)
        n = lib.LZ4_compress_HC(src, dst, len(src), len(dst), 9)
        return dst.raw[:n]
    return comp


def write_zso(data: bytes, path: Path, block: int = 2048, version: int = 1, shift: int = 0,
              compress=None) -> None:
    """Like write_cso, with LZ4 blocks (what maxcso --format=zso writes)."""
    n = (len(data) + block - 1) // block
    body = bytearray()
    index = []
    base = 24 + 4 * (n + 1)
    for i in range(n):
        blk = data[i * block:(i + 1) * block].ljust(block, b"\0")
        while (base + len(body)) % (1 << shift):
            body.append(0)
        off = (base + len(body)) >> shift
        z = (compress or lz4_compress)(blk)
        if len(z) < len(blk):
            index.append(off)
            body += z
        else:
            index.append(off | 0x80000000)
            body += blk
    while (base + len(body)) % (1 << shift):
        body.append(0)
    index.append((base + len(body)) >> shift)
    path.write_bytes(b"ZISO" + struct.pack("<IQIBB2x", 24, len(data), block, version, shift) +
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

    def test_zso(self):
        p = self.tmp / "game.zso"
        write_zso(self.iso_bytes, p)
        self.check(p)

    def test_zso_v2_shift_and_odd_length(self):
        p = self.tmp / "g2.zso"
        data = self.iso_bytes[:-777]
        write_zso(data, p, block=4096, shift=2)
        with es_cso.CsoFile(p) as f:
            self.assertTrue(f.zso)
            self.assertEqual(f.read(), data)

    def test_zso_damaged_block(self):
        p = self.tmp / "gz.zso"
        write_zso(self.iso_bytes, p)
        bad = bytearray(p.read_bytes())
        k = 110                                    # inside the zeroed stretch: a compressed block
        idx = struct.unpack_from("<I", bad, 24 + 4 * k)[0]
        self.assertFalse(idx & 0x80000000)
        bad[idx:idx + 4] = b"\x0f\xff\xff\xff"          # literal length far past the block
        (self.tmp / "gzbad.zso").write_bytes(bytes(bad))
        with self.assertRaises(es_cso.CsoError):
            with es_cso.CsoFile(self.tmp / "gzbad.zso") as f:
                f.seek(k * 2048)
                f.read(2048)

    def test_lz4_block_cases(self):
        lz = es_cso.lz4_block
        self.assertEqual(lz(b"\x50hello", 5), b"hello")                       # literals only
        self.assertEqual(lz(b"\x14a\x01\x00\x50bcdef", 14), b"a" * 9 + b"bcdef")    # overlap, offset 1
        self.assertEqual(lz(b"\x50hello\x00\x00\x00", 5), b"hello")         # zero padding after the end
        for bad in (b"\xf0", b"\x10a", b"\x11a\x00\x00x", b"\x11a\x05\x00x"):
            with self.assertRaises(ValueError):
                lz(bad, 50)

    @unittest.skipUnless(shutil.which("lz4"), "the lz4 command is not installed")
    def test_lz4_block_against_the_reference_compressor(self):
        """Blocks made by the reference `lz4` tool (frame, one block per file) decode to the input."""
        import subprocess
        rnd = random.Random(11)
        samples = [bytes(2048), self.iso_bytes[100 * 2048:101 * 2048],
                   bytes(rnd.choice(b"abcde ") for _ in range(2048)),
                   (b"DJMAX PORTABLE 3 " * 200)[:2048], bytes(range(256)) * 8]
        for s in samples:
            fr = subprocess.run(["lz4", "-c", "-9", "-B4", "-BI", "--no-frame-crc", "-"], input=s,
                                capture_output=True, check=True).stdout
            self.assertEqual(fr[:4], bytes.fromhex("04224d18"))
            pos = 4 + 2 + (8 if fr[4] & 8 else 0) + 1     # magic, FLG+BD, optional content size, HC
            size = struct.unpack_from("<I", fr, pos)[0]
            blk = fr[pos + 4:pos + 4 + (size & 0x7FFFFFFF)]
            self.assertEqual(blk if size & 0x80000000 else es_cso.lz4_block(blk, len(s)), s)

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

    @unittest.skipUnless(os.environ.get("ES_REAL_ISO_COPY") and ref_lz4(),
                         "set ES_REAL_ISO_COPY to a copy of the ISO (and have liblz4)")
    def test_full_size_copy_zso(self):
        """The whole disc as ZSO, blocks made by liblz4 (the reference compressor), byte for byte."""
        src = Path(os.environ["ES_REAL_ISO_COPY"])
        p = self.tmp / "full.zso"
        data = src.read_bytes()
        write_zso(data, p, shift=2, compress=ref_lz4())
        h1, h2 = hashlib.sha256(data), hashlib.sha256()
        with es_chd.open_image(p) as f:
            self.assertTrue(f.zso)
            while True:
                b = f.read(8 << 20)
                if not b:
                    break
                h2.update(b)
        self.assertEqual(h1.hexdigest(), h2.hexdigest())


if __name__ == "__main__":
    unittest.main()
