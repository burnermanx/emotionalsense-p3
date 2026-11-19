"""emotionalsense build pipeline: DJMAX Portable 3 ISO -> native game executable.

Used by both the GTK launcher and the command line. Every step is idempotent,
so an interrupted run can simply be started again.

    ISO --(iso9660)--> <data>/disc/PSP_GAME/...
        --(pspdecrypt)--> <data>/module/eboot.elf
        --(allegrexrecomp emit)--> build/gen/djmax_*.c
        --(cmake + cc)--> build/game/EmotionalSense-P3-game

Nothing produced here may be committed or redistributed: it is all derived
from your own copy of the game (see .gitignore).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import es_prxreloc

REPO = Path(__file__).resolve().parent.parent
GAME_ID = "ULKS46236"
GAME_TITLE = "DJMAX PORTABLE 3"
APP_NAME = "EmotionalSense-P3"


def _project_version() -> str:
    """The version is written once, in project(... VERSION x.y.z)."""
    try:
        text = (REPO / "CMakeLists.txt").read_text(encoding="utf-8")
    except OSError:                       # the packaged launcher has no source tree
        return "0.0.0"
    m = re.search(r"project\(emotionalsense\s+VERSION\s+([0-9.]+)", text)
    return m.group(1) if m else "0.0.0"


VERSION = _project_version()

# pspdecrypt (GPL-3.0) is fetched and run as a separate process -- never
# linked or vendored -- so this repository stays MIT. Pinned for repeatability.
PSPDECRYPT_URL = "https://github.com/John-K/pspdecrypt.git"
PSPDECRYPT_REV = "c156627db7634d395c380c0a9589130f603307fc"

SECTOR = 2048


def default_data_dir() -> Path:
    import es_portable
    return es_portable.data_dir()


class PipelineError(RuntimeError):
    pass


def is_repo_work_data(path) -> bool:
    """True for a repository checkout's work/data -- where the developer's real
    save lives. A directory called `data` inside one called `work`."""
    p = Path(os.path.abspath(os.path.expanduser(str(path)))).resolve()
    return p.name == "data" and p.parent.name == "work"


def refuse_real_data(path) -> None:
    """Exit unless EMOTIONALSENSE_ALLOW_REAL_DATA=1 when `path` is work/data."""
    if is_repo_work_data(path) and os.environ.get("EMOTIONALSENSE_ALLOW_REAL_DATA") != "1":
        raise SystemExit(
            f"EmotionalSense-P3: refusing to use {path} (the repository's work/data, "
            "which holds the real save). Use a copy, or set EMOTIONALSENSE_ALLOW_REAL_DATA=1 on purpose.")


# ---------------------------------------------------------------------------
# ISO9660 -- a small reader. bsdtar/libarchive list nothing on PSP UMD images,
# and psprecomp's own extractor flattens paths, so we walk the tree ourselves.
# ---------------------------------------------------------------------------

@dataclass
class IsoEntry:
    path: str          # "PSP_GAME/SYSDIR/EBOOT.BIN"
    lba: int
    size: int
    is_dir: bool


class Iso9660:
    def __init__(self, path: Path):
        self.path = Path(path)
        import es_chd
        self.f = es_chd.open_image(self.path)        # the ISO itself, or a CHD read sector by sector
        pvd = self._read(16 * SECTOR, SECTOR)
        if pvd[1:6] != b"CD001" or pvd[0] != 1:
            raise PipelineError(f"{self.path} is not an ISO9660 image")
        root = pvd[156:156 + 34]
        self.root_lba = struct.unpack_from("<I", root, 2)[0]
        self.root_size = struct.unpack_from("<I", root, 10)[0]

    def close(self):
        self.f.close()

    def _read(self, off: int, n: int) -> bytes:
        self.f.seek(off)
        return self.f.read(n)

    def walk(self) -> list[IsoEntry]:
        out: list[IsoEntry] = []
        seen: set[int] = set()

        def rec(lba: int, size: int, prefix: str):
            if lba in seen:
                return
            seen.add(lba)
            data = self._read(lba * SECTOR, size)
            pos = 0
            while pos < len(data):
                ln = data[pos]
                if ln == 0:                       # padding to the next sector
                    pos = (pos // SECTOR + 1) * SECTOR
                    continue
                r = data[pos:pos + ln]
                pos += ln
                e_lba = struct.unpack_from("<I", r, 2)[0]
                e_size = struct.unpack_from("<I", r, 10)[0]
                flags = r[25]
                nlen = r[32]
                name = r[33:33 + nlen]
                if name in (b"\x00", b"\x01"):
                    continue
                name = name.decode("ascii", "replace").split(";")[0]
                path = f"{prefix}{name}"
                is_dir = bool(flags & 2)
                out.append(IsoEntry(path, e_lba, e_size, is_dir))
                if is_dir:
                    rec(e_lba, e_size, path + "/")

        rec(self.root_lba, self.root_size, "")
        return out

    def read_file(self, e: IsoEntry) -> bytes:
        return self._read(e.lba * SECTOR, e.size)

    def extract(self, e: IsoEntry, dest: Path, progress: Callable[[int], None]):
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        self.f.seek(e.lba * SECTOR)
        left = e.size
        with open(tmp, "wb") as o:
            while left:
                chunk = self.f.read(min(left, 8 << 20))
                if not chunk:
                    raise PipelineError(f"short read extracting {e.path}")
                o.write(chunk)
                left -= len(chunk)
                progress(len(chunk))
        tmp.replace(dest)


def parse_sfo(b: bytes) -> dict:
    if b[:4] != b"\x00PSF":
        return {}
    key_tab, data_tab, n = struct.unpack_from("<III", b, 8)
    out = {}
    for i in range(n):
        ko, fmt, ln, mx, do = struct.unpack_from("<HHIII", b, 20 + i * 16)
        k = b[key_tab + ko:b.index(b"\0", key_tab + ko)].decode()
        v = b[data_tab + do:data_tab + do + ln]
        out[k] = struct.unpack("<I", v[:4])[0] if fmt == 0x0404 else v.rstrip(b"\0").decode("utf-8", "replace")
    return out


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

LogFn = Callable[[str], None]
ProgressFn = Callable[[float, str], None]


@dataclass
class Options:
    iso: Path
    data_dir: Path = field(default_factory=default_data_dir)
    build_dir: Path = REPO / "build"
    work_dir: Path = REPO / "work"
    dist_dir: Path = REPO / "dist"
    force: bool = False           # accept an ISO that is not ULKS46236
    jobs: int = os.cpu_count() or 4


class Pipeline:
    STEPS = [
        ("check",    "Checking tools"),
        ("verify",   "Checking the ISO"),
        ("extract",  "Extracting the disc data"),
        ("decrypt",  "Decrypting EBOOT.BIN"),
        ("toolkit",  "Building the recompiler"),
        ("recomp",   "Recompiling the game code to C"),
        ("compile",  "Compiling the native executable"),
    ]

    def __init__(self, opts: Options, log: LogFn = print,
                 progress: ProgressFn = lambda f, s: None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.o = opts
        self.log = log
        self.progress = progress
        self.cancelled = cancelled
        self.tools = self.o.work_dir / "tools"
        self.elf = self.o.data_dir / "module" / "eboot.elf"
        self.gen = self.o.build_dir / "gen"
        self._step_i = 0

    # -- helpers -------------------------------------------------------------

    def _frac(self, f: float, what: str):
        n = len(self.STEPS)
        self.progress((self._step_i + max(0.0, min(1.0, f))) / n, what)

    def run_cmd(self, cmd: list, cwd: Optional[Path] = None, env: Optional[dict] = None,
                quiet: bool = False) -> str:
        self.log("$ " + " ".join(str(c) for c in cmd))
        import es_env
        full_env = es_env.child_env()
        if env:
            full_env.update(env)
        p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                             env=full_env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, errors="replace")
        lines = []
        assert p.stdout
        for line in p.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if not quiet:
                self.log(line)
            if self.cancelled():
                p.terminate()
                raise PipelineError("cancelled")
        rc = p.wait()
        if rc != 0:
            if quiet:
                for l in lines[-40:]:
                    self.log(l)
            raise PipelineError(f"command failed ({rc}): {cmd[0]}")
        return "\n".join(lines)

    # -- steps ---------------------------------------------------------------

    def step_check(self):
        need = {
            "cmake": "cmake", "git": "git", "make": "make",
            "cc": "gcc or clang", "c++": "gcc or clang",
        }
        missing = [f"{b} ({pkg})" for b, pkg in need.items() if not shutil.which(b)]
        if missing:
            raise PipelineError("missing: " + ", ".join(missing) +
                                "\nRun ./scripts/install-deps-arch.sh")
        if not (REPO / "psprecomp" / "CMakeLists.txt").exists():
            self.run_cmd(["git", "submodule", "update", "--init", "--recursive"], cwd=REPO)
        sdl = subprocess.run(["pkg-config", "--exists", "sdl2"]).returncode == 0 \
            if shutil.which("pkg-config") else False
        if not sdl:
            raise PipelineError("SDL2 not found (package sdl2-compat).\n"
                                "Run ./scripts/install-deps-arch.sh")
        ff = subprocess.run(["pkg-config", "--exists", "libavcodec", "libavutil"]).returncode == 0 \
            if shutil.which("pkg-config") else False
        if not ff:
            raise PipelineError("FFmpeg (libavcodec) not found.\n"
                                "Run ./scripts/install-deps-arch.sh")
        self.log("tools OK")

    def step_verify(self):
        iso = Iso9660(self.o.iso)
        try:
            entries = {e.path: e for e in iso.walk()}
            sfo_e = entries.get("PSP_GAME/PARAM.SFO")
            if not sfo_e or "PSP_GAME/SYSDIR/EBOOT.BIN" not in entries:
                raise PipelineError("this does not look like a PSP game ISO")
            sfo = parse_sfo(iso.read_file(sfo_e))
        finally:
            iso.close()
        disc_id, title = sfo.get("DISC_ID", "?"), sfo.get("TITLE", "?")
        self.log(f"ISO: {title} [{disc_id}] version {sfo.get('DISC_VERSION', '?')}")
        if disc_id != GAME_ID:
            msg = f"expected {GAME_TITLE} [{GAME_ID}], found {title} [{disc_id}]"
            if not self.o.force:
                raise PipelineError(msg + " (use --force to try anyway)")
            self.log("AVISO: " + msg)

    def step_extract(self):
        disc = self.o.data_dir / "disc"
        marker = self.o.data_dir / ".extracted.json"
        st = self.o.iso.stat()
        stamp = {"iso_size": st.st_size, "iso_name": self.o.iso.name}
        if marker.exists() and not self.o.force:
            try:
                if json.loads(marker.read_text()) == stamp:
                    self.log(f"data already extracted in {disc}")
                    return
            except Exception:
                pass
        iso = Iso9660(self.o.iso)
        try:
            entries = [e for e in iso.walk() if not e.is_dir]
            total = sum(e.size for e in entries) or 1
            done = 0

            def prog(n):
                nonlocal done
                done += n
                self._frac(done / total, f"Extracting... {done >> 20} / {total >> 20} MiB")
                if self.cancelled():
                    raise PipelineError("cancelled")

            for e in entries:
                dest = disc / e.path
                if dest.exists() and dest.stat().st_size == e.size:
                    prog(e.size)
                    continue
                self.log(f"  {e.path} ({e.size:,} bytes)")
                iso.extract(e, dest, prog)
        finally:
            iso.close()
        (self.o.data_dir / "ms").mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(stamp))
        self.log(f"extracted to {disc}")

    def _build_pspdecrypt(self) -> Path:
        src = self.tools / "pspdecrypt"
        exe = src / "pspdecrypt"
        if exe.exists():
            return exe
        self.tools.mkdir(parents=True, exist_ok=True)
        if not (src / ".git").exists():
            self.run_cmd(["git", "clone", PSPDECRYPT_URL, src])
        self.run_cmd(["git", "-C", src, "checkout", "-q", PSPDECRYPT_REV])
        self.run_cmd(["git", "-C", src, "submodule", "update", "--init", "--recursive"])
        cc, cxx = ("clang", "clang++") if shutil.which("clang") else ("cc", "c++")
        self.run_cmd(["make", f"-j{self.o.jobs}", f"CC={cc}", f"CXX={cxx}"], cwd=src, quiet=True)
        if not exe.exists():
            raise PipelineError("pspdecrypt was not built")
        return exe

    def step_decrypt(self):
        eboot = self.o.data_dir / "disc" / "PSP_GAME" / "SYSDIR" / "EBOOT.BIN"
        raw = self.elf.with_name("eboot.raw.elf")
        if self.elf.exists() and raw.exists() and not self.o.force:
            self.log(f"module already decrypted: {self.elf}")
            return
        tool = self._build_pspdecrypt()
        self.elf.parent.mkdir(parents=True, exist_ok=True)
        self.run_cmd([tool, eboot, "-o", raw])
        data = raw.read_bytes() if raw.exists() else b""
        if data[:4] != b"\x7fELF":
            raise PipelineError("decryption did not produce a valid ELF")
        # Apply the segment-relative PRX relocations now: the recompiler bakes
        # addresses into C, so .data references must already be correct.
        try:
            out, st = es_prxreloc.relocate(data)
        except es_prxreloc.RelocError as e:
            raise PipelineError(f"relocation failed: {e}")
        self.elf.write_bytes(out)
        self.log(f"relocations: {st['total']} ({st['applied']} applied)")
        self.log(f"ELF: {self.elf} ({self.elf.stat().st_size:,} bytes)")

    def _configure(self):
        gen = ["-G", "Ninja"] if shutil.which("ninja") else []
        self.run_cmd(["cmake", "-S", REPO, "-B", self.o.build_dir, *gen,
                      "-DCMAKE_BUILD_TYPE=Release",
                      f"-DEMOTIONALSENSE_GEN_DIR={self.gen}"], quiet=True)

    def step_toolkit(self):
        self._configure()
        self.run_cmd(["cmake", "--build", self.o.build_dir, "--target", "allegrexrecomp",
                      "-j", str(self.o.jobs)], quiet=True)

    def _allegrexrecomp(self) -> Path:
        for p in (self.o.build_dir / "psprecomp" / "tools" / "allegrexrecomp").rglob("allegrexrecomp"):
            if p.is_file() and os.access(p, os.X_OK):
                return p
        raise PipelineError("allegrexrecomp not found in the build")

    def step_recomp(self):
        tool = self._allegrexrecomp()
        self.gen.mkdir(parents=True, exist_ok=True)
        self.run_cmd([tool, "funcs", self.elf], quiet=True)
        # Emit into a scratch dir, then sync: unchanged files keep their mtime
        # (incremental rebuilds stay fast) and files from an older emit that
        # no longer exist are removed (they would otherwise link twice).
        fresh = self.o.build_dir / "gen.new"
        if fresh.exists():
            shutil.rmtree(fresh)
        fresh.mkdir(parents=True)
        out = self.run_cmd([tool, "emit", self.elf, fresh, "djmax"], quiet=True)
        new_names = {p.name for p in fresh.iterdir()}
        for old in self.gen.iterdir():
            if old.name not in new_names:
                old.unlink()
        changed = 0
        for p in fresh.iterdir():
            dst = self.gen / p.name
            if not dst.exists() or dst.read_bytes() != p.read_bytes():
                shutil.copy2(p, dst)
                changed += 1
        shutil.rmtree(fresh)
        # A transfer whose delay slot landed in another function is silently
        # wrong at runtime; refuse to hand the generated code to the compiler.
        self.run_cmd([sys.executable,
                      str(REPO / "scripts" / "check_delay_slots.py"),
                      str(self.gen)], quiet=True)
        for line in out.splitlines()[-3:]:
            self.log(line)
        self.log(f"{len(new_names)} files generated, {changed} changed")

    def step_compile(self):
        self._configure()                     # pick up the generated sources
        self.run_cmd(["cmake", "--build", self.o.build_dir, "-j", str(self.o.jobs)], quiet=True)
        exe = self.o.build_dir / "game" / "EmotionalSense-P3-game"
        if not exe.exists():
            raise PipelineError("the game executable was not built")
        self.log(f"executable: {exe}")

    # -- driver --------------------------------------------------------------

    def run(self, only: Optional[list[str]] = None) -> Path:
        for i, (name, title) in enumerate(self.STEPS):
            if only and name not in only:
                continue
            if self.cancelled():
                raise PipelineError("cancelled")
            self._step_i = i
            self._frac(0.0, title)
            self.log(f"\n=== [{i + 1}/{len(self.STEPS)}] {title} ===")
            getattr(self, f"step_{name}")()
        self.progress(1.0, "Done")
        return self.o.build_dir / "game" / "EmotionalSense-P3-game"
