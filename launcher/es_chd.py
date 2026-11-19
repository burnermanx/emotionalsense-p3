"""Read the 2048-byte sectors of a PSP disc image stored as CHD (MAME's compressed hunks).

libchdr (BSD-3, the library PPSSPP, RetroArch and DuckStation use; it carries zlib, lzma, zstd
and flac decoders) is loaded with ctypes from the kit (bin/libchdr.so, bin/chdr.dll). Nothing
is written: the CHD is only read, hunk by hunk, and ChdFile looks like a read-only file of the
disc's data sectors, so the ISO9660 reader and the hash use it in place of the ISO. No
temporary ISO.

  DVD CHD (chdman createdvd): hunks of whole 2048-byte sectors.
  CD  CHD (chdman createcd): frames of 2352+96 bytes; the data of the first track (mode 1,
          or mode 2 form 1, cooked or raw) is the 2048 bytes of each frame.
Audio first tracks, GD-ROM and HD CHDs, and CHDs that need a parent are not disc images of
this kind and are reported as unsupported.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import struct
import sys
from pathlib import Path
from typing import Optional

import es_i18n
from es_i18n import t
from es_pipeline import PipelineError

SECTOR = 2048
CD_FRAME = 2352 + 96            # a CD CHD frame: sector + subcode
MAGIC = b"MComprHD"


class ChdError(PipelineError):
    """kind: nolib, invalid, corrupt, unsupported. The message is already translated."""

    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.detail = detail
        key = {"nolib": "err.chd_nolib", "invalid": "err.chd_invalid", "corrupt": "err.chd_corrupt",
               "unsupported": "err.chd_unsupported"}[kind]
        super().__init__(t(key, what=detail))


def is_chd(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == MAGIC
    except OSError:
        return False


def is_chd_name(path) -> bool:
    return str(path).lower().endswith(".chd")


# ---- libchdr through ctypes -------------------------------------------------------------

_U8P = ctypes.POINTER(ctypes.c_uint8)


class _Header(ctypes.Structure):          # struct _chd_header of libchdr (the fields used)
    _fields_ = [("length", ctypes.c_uint32), ("version", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("compression", ctypes.c_uint32 * 4), ("hunkbytes", ctypes.c_uint32),
                ("totalhunks", ctypes.c_uint32), ("logicalbytes", ctypes.c_uint64),
                ("metaoffset", ctypes.c_uint64), ("mapoffset", ctypes.c_uint64),
                ("md5", ctypes.c_uint8 * 16), ("parentmd5", ctypes.c_uint8 * 16),
                ("sha1", ctypes.c_uint8 * 20), ("rawsha1", ctypes.c_uint8 * 20),
                ("parentsha1", ctypes.c_uint8 * 20), ("unitbytes", ctypes.c_uint32),
                ("unitcount", ctypes.c_uint64)]


_FSIZE = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_void_p)
_FREAD = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p)
_FCLOSE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_FSEEK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int)


class _Callbacks(ctypes.Structure):
    _fields_ = [("fsize", _FSIZE), ("fread", _FREAD), ("fclose", _FCLOSE), ("fseek", _FSEEK)]


# chd_error values worth telling apart
ERR_NONE, ERR_INVALID_FILE, ERR_INVALID_DATA, ERR_REQUIRES_PARENT = 0, 3, 5, 7
ERR_NOT_SUPPORTED, ERR_UNSUPPORTED_VERSION = 18, 21
TAG_CHT2, TAG_CHTR, TAG_CHGT, TAG_CHGD, TAG_DVD = (struct.unpack(">I", x)[0] for x in
                                                   (b"CHT2", b"CHTR", b"CHGT", b"CHGD", b"DVD "))
TAG_HARD_DISK = struct.unpack(">I", b"GDDD")[0]

_lib = None


def lib_names():
    return ["chdr.dll", "libchdr.dll"] if sys.platform.startswith("win") else (
        ["libchdr.dylib"] if sys.platform == "darwin" else ["libchdr.so", "libchdr.so.0"])


_kit: Optional[Path] = None


def configure(kit: Optional[Path]) -> None:
    """The kit whose bin/ holds libchdr (the launcher sets it once)."""
    global _kit
    _kit = Path(kit) if kit else None


def find_lib(kit: Optional[Path] = None) -> Optional[str]:
    env = os.environ.get("ES_LIBCHDR")
    if env and Path(env).exists():
        return env
    kit = kit or _kit
    if kit is None:
        try:
            import es_prepare
            kit = es_prepare.default_kit()
        except Exception:                          # noqa: BLE001
            kit = None
    if kit is not None:
        for sub in ("bin", "deps/lib", "deps/bin"):
            for n in lib_names():
                if (Path(kit) / sub / n).exists():
                    return str(Path(kit) / sub / n)
    return ctypes.util.find_library("chdr")


def load(kit: Optional[Path] = None):
    global _lib
    if _lib is not None:
        return _lib
    path = find_lib(kit)
    if not path:
        raise ChdError("nolib")
    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        raise ChdError("nolib", str(e)) from e
    lib.chd_open_core_file_callbacks.argtypes = [ctypes.POINTER(_Callbacks), ctypes.c_void_p, ctypes.c_int,
                                                 ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.chd_open_core_file_callbacks.restype = ctypes.c_int
    lib.chd_close.argtypes = [ctypes.c_void_p]
    lib.chd_close.restype = None
    lib.chd_get_header.argtypes = [ctypes.c_void_p]
    lib.chd_get_header.restype = ctypes.POINTER(_Header)
    lib.chd_read.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    lib.chd_read.restype = ctypes.c_int
    lib.chd_get_metadata.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                     ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint8)]
    lib.chd_get_metadata.restype = ctypes.c_int
    lib.chd_error_string.argtypes = [ctypes.c_int]
    lib.chd_error_string.restype = ctypes.c_char_p
    _lib = lib
    return lib


# offset of the 2048 user bytes inside a frame, by the CHD track type (None = not data)
_DATA_OFFSET = {"MODE1": 0, "MODE1_RAW": 16, "MODE2_FORM1": 0, "MODE2_RAW": 24, "MODE2": 8,
                "MODE2_FORM_MIX": 8}


class ChdFile:
    """A read-only file of the disc's 2048-byte data sectors: .size, seek, read, tell, close."""

    def __init__(self, path, kit: Optional[Path] = None):
        self.path = Path(path)
        self._lib = load(kit)
        self._f = open(self.path, "rb")
        self._chd = None
        self._pos = 0
        self._hunk_no = -1
        self._hunk = b""
        self._cb = None
        head = self._f.read(8)
        self._f.seek(0)
        if head != MAGIC:
            self._f.close()
            raise ChdError("invalid")
        self._open_chd()
        try:
            self._setup()
        except Exception:
            self.close()
            raise

    # -- libchdr plumbing

    def _open_chd(self):
        f = self._f

        def fsize(_):
            cur = f.tell()
            f.seek(0, 2)
            n = f.tell()
            f.seek(cur)
            return n

        def fread(buf, size, n, _):
            try:
                data = f.read(size * n)
            except OSError:
                return 0
            ctypes.memmove(buf, data, len(data))
            return len(data) // size if size else 0

        def fclose(_):
            return 0                     # closed by close()

        def fseek(_, off, whence):
            try:
                f.seek(off, whence)
                return 0
            except OSError:
                return -1
        self._keep = (_FSIZE(fsize), _FREAD(fread), _FCLOSE(fclose), _FSEEK(fseek))
        self._cb = _Callbacks(*self._keep)
        out = ctypes.c_void_p()
        err = self._lib.chd_open_core_file_callbacks(ctypes.byref(self._cb), None, 1, None, ctypes.byref(out))
        if err != ERR_NONE:
            self._f.close()
            raise self._map_error(err)
        self._chd = out

    def _map_error(self, err: int) -> ChdError:
        if err in (ERR_REQUIRES_PARENT,):
            return ChdError("unsupported", "parent CHD")
        if err == ERR_NOT_SUPPORTED:
            return ChdError("unsupported", self._lib.chd_error_string(err).decode("ascii", "replace"))
        if err in (ERR_INVALID_FILE, ERR_UNSUPPORTED_VERSION):      # not a header libchdr knows: not a CHD
            return ChdError("invalid", self._lib.chd_error_string(err).decode("ascii", "replace"))
        return ChdError("corrupt", self._lib.chd_error_string(err).decode("ascii", "replace"))

    def _meta(self, tag: int, index: int = 0) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(512)
        n = ctypes.c_uint32()
        err = self._lib.chd_get_metadata(self._chd, tag, index, buf, 512, ctypes.byref(n), None, None)
        return buf.raw[:n.value] if err == ERR_NONE else None

    def _setup(self):
        h = self._lib.chd_get_header(self._chd).contents
        self._hunkbytes, self._totalhunks, self._unit = h.hunkbytes, h.totalhunks, h.unitbytes
        if self._meta(TAG_DVD) is not None:
            if self._unit != SECTOR or self._hunkbytes % SECTOR:
                raise ChdError("unsupported", "DVD")
            self.kind = "dvd"
            self.size = h.logicalbytes
            return
        if self._meta(TAG_CHGT) is not None or self._meta(TAG_CHGD) is not None:
            raise ChdError("unsupported", "GD-ROM")
        track = self._meta(TAG_CHT2) or self._meta(TAG_CHTR)
        if track is None:
            raise ChdError("unsupported", "not a disc image")
        text = track.rstrip(b"\0").decode("ascii", "replace")
        m = re.match(r"TRACK:(\d+) TYPE:(\S+) SUBTYPE:(\S+) FRAMES:(\d+)(?: PREGAP:(\d+) PGTYPE:(\S+) PGSUB:(\S+) POSTGAP:(\d+))?", text)
        if not m:
            raise ChdError("invalid", "track metadata")
        ttype, frames = m.group(2), int(m.group(4))
        if ttype not in _DATA_OFFSET:
            raise ChdError("unsupported", "first track is " + ttype)
        if self._unit != CD_FRAME or self._hunkbytes % CD_FRAME:
            raise ChdError("unsupported", "CD frame size")
        pregap = int(m.group(5) or 0)
        pgtype = m.group(6) or ""
        self.kind = "cd"
        self._off = _DATA_OFFSET[ttype]
        self._first = pregap if pgtype.startswith("V") else 0      # frames stored for a V pregap
        self._frames = frames
        self.size = frames * SECTOR
        if (self._first + frames) * CD_FRAME > self._totalhunks * self._hunkbytes:
            raise ChdError("corrupt", "track longer than the file")

    # -- file-like

    def tell(self) -> int:
        return self._pos

    def seek(self, off: int, whence: int = 0) -> int:
        self._pos = off if whence == 0 else self._pos + off if whence == 1 else self.size + off
        return self._pos

    def _load_hunk(self, n: int):
        if n == self._hunk_no:
            return
        buf = ctypes.create_string_buffer(self._hunkbytes)
        err = self._lib.chd_read(self._chd, n, buf)
        if err != ERR_NONE:
            self._hunk_no = -1
            raise ChdError("corrupt", f"hunk {n}: " + self._lib.chd_error_string(err).decode("ascii", "replace"))
        self._hunk, self._hunk_no = buf.raw, n

    def _sector(self, lba: int) -> bytes:
        """The 2048 data bytes of sector `lba`."""
        if self.kind == "dvd":
            per = self._hunkbytes // SECTOR
            self._load_hunk(lba // per)
            o = (lba % per) * SECTOR
            return self._hunk[o:o + SECTOR]
        frame = self._first + lba
        per = self._hunkbytes // CD_FRAME
        self._load_hunk(frame // per)
        o = (frame % per) * CD_FRAME + self._off
        return self._hunk[o:o + SECTOR]

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0 or self._pos + n > self.size:
            n = max(0, self.size - self._pos)
        out = []
        pos, end = self._pos, self._pos + n
        while pos < end:
            lba, o = divmod(pos, SECTOR)
            sec = self._sector(lba)
            take = min(SECTOR - o, end - pos)
            out.append(sec[o:o + take] if (o or take != SECTOR) else sec)
            pos += take
        self._pos = pos
        return b"".join(out)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._chd is not None:
            self._lib.chd_close(self._chd)
            self._chd = None
        if getattr(self, "_f", None) is not None:
            self._f.close()
            self._f = None


def open_image(path, kit: Optional[Path] = None):
    """The disc image as a binary read-only file: the file itself for an ISO, a ChdFile for a CHD
    (detected by content, not only by name). `.size` of the CHD is the data size; for a plain file
    use image_size()."""
    if is_chd(path) or is_chd_name(path):
        return ChdFile(path, kit)
    return open(path, "rb")


def image_size(f) -> int:
    if isinstance(f, ChdFile):
        return f.size
    cur = f.tell()
    f.seek(0, 2)
    n = f.tell()
    f.seek(cur)
    return n
