"""Read a PSP disc image stored as CSO (CISO v1/v2: the compressed ISO of PPSSPP, maxcso, ciso).

CsoFile looks like a read-only file of the ISO's bytes, like es_chd.ChdFile, so the ISO9660 reader
and the hash use it in place of the ISO. Nothing is written, no temporary ISO.

Layout: a 24-byte header ("CISO", header size, total bytes, block size, version, index shift),
then (total / block + 1) 32-bit index entries and the blocks. An entry's low 31 bits, shifted left
by the index shift, are the offset of its block; bit 31 set means the block is stored as is,
otherwise it is raw deflate. A block ends where the next one starts. ZSO ("ZISO", same header and
index, LZ4 blocks instead of deflate: `maxcso --format=zso`) is read with `lz4_block` below, a
pure Python LZ4 block decompressor (no module to ship).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from es_i18n import t
from es_pipeline import PipelineError

MAGIC = b"CISO"
ZMAGIC = b"ZISO"


class CsoError(PipelineError):
    """kind: invalid, corrupt. The message is already translated."""

    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        key = {"invalid": "err.cso_invalid", "corrupt": "err.cso_corrupt"}[kind]
        super().__init__(t(key, what=detail))


def magic_of(path) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(4)
    except OSError:
        return b""


def is_cso(path) -> bool:
    return magic_of(path) in (MAGIC, ZMAGIC)


def lz4_block(src: bytes, size: int) -> bytes:
    """Decompress one LZ4 block (https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md) that
    holds `size` bytes. A ZSO block may be followed by zero padding up to the index alignment, so
    decoding stops once `size` bytes exist. Raises ValueError on malformed data."""
    out = bytearray()
    ip, n = 0, len(src)
    while ip < n:
        token = src[ip]
        ip += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if ip >= n:
                    raise ValueError("truncated literal length")
                b = src[ip]
                ip += 1
                lit += b
                if b != 255:
                    break
        if ip + lit > n:
            raise ValueError("truncated literals")
        out += src[ip:ip + lit]
        ip += lit
        if len(out) >= size or ip >= n:       # the last sequence has literals only
            break
        if ip + 2 > n:
            raise ValueError("truncated offset")
        off = src[ip] | src[ip + 1] << 8
        ip += 2
        if off == 0 or off > len(out):
            raise ValueError("bad match offset")
        mlen = token & 15
        if mlen == 15:
            while True:
                if ip >= n:
                    raise ValueError("truncated match length")
                b = src[ip]
                ip += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        if off >= mlen:
            s = len(out) - off
            out += out[s:s + mlen]
        else:                                  # overlapping copy repeats the last `off` bytes
            pat = out[-off:]
            out += (pat * (mlen // off + 1))[:mlen]
    if len(out) < size:
        raise ValueError("short block")
    return bytes(out[:size])


class CsoFile:
    def __init__(self, path):
        self.path = Path(path)
        self._f = open(self.path, "rb")
        try:
            self._parse()
        except BaseException:
            self._f.close()
            raise
        self._pos = 0
        self._blk_no = -1
        self._blk = b""

    def _parse(self):
        h = self._f.read(24)
        if len(h) < 24 or h[:4] not in (MAGIC, ZMAGIC):
            raise CsoError("invalid", "header")
        self.zso = h[:4] == ZMAGIC
        hsize, total, bsize, ver, shift = struct.unpack_from("<IQIBB", h, 4)
        if hsize not in (0, 24) or ver > 2 or bsize < 512 or bsize & (bsize - 1) or shift > 8 or total == 0:
            raise CsoError("invalid", f"version {ver}, block {bsize}")
        self.size, self._bsize, self._shift = total, bsize, shift
        self._nblocks = (total + bsize - 1) // bsize
        raw = self._f.read(4 * (self._nblocks + 1))
        if len(raw) != 4 * (self._nblocks + 1):
            raise CsoError("corrupt", "index")
        self._index = struct.unpack(f"<{self._nblocks + 1}I", raw)
        self._file_size = self.path.stat().st_size

    # -- file-like

    def tell(self) -> int:
        return self._pos

    def seek(self, off: int, whence: int = 0) -> int:
        self._pos = off if whence == 0 else self._pos + off if whence == 1 else self.size + off
        return self._pos

    def _load(self, n: int):
        if n == self._blk_no:
            return
        a, b = self._index[n], self._index[n + 1]
        start, end = (a & 0x7FFFFFFF) << self._shift, (b & 0x7FFFFFFF) << self._shift
        if end < start or end > self._file_size:
            raise CsoError("corrupt", f"block {n}")
        self._f.seek(start)
        data = self._f.read(end - start)
        if a & 0x80000000:                       # stored as is
            blk = data[:self._bsize]
        elif self.zso:
            try:
                blk = lz4_block(data, min(self._bsize, self.size - n * self._bsize))
            except ValueError:
                raise CsoError("corrupt", f"block {n}")
        else:
            try:
                blk = zlib.decompressobj(-15).decompress(data)
            except zlib.error:
                raise CsoError("corrupt", f"block {n}")
        want = min(self._bsize, self.size - n * self._bsize)
        if len(blk) < want:
            raise CsoError("corrupt", f"block {n}")
        self._blk, self._blk_no = blk, n

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0 or self._pos + n > self.size:
            n = max(0, self.size - self._pos)
        out = []
        pos, end = self._pos, self._pos + n
        while pos < end:
            bn, o = divmod(pos, self._bsize)
            self._load(bn)
            take = min(self._bsize - o, end - pos)
            out.append(self._blk[o:o + take])
            pos += take
        self._pos = pos
        return b"".join(out)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if getattr(self, "_f", None) is not None:
            self._f.close()
            self._f = None
