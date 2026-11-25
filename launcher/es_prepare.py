"""Prepare the game on the player's machine, with the tools the kit carries.

    ISO --(iso9660)--> <data>/disc/PSP_GAME/...
        --(kit/bin/pspdecrypt)--> <data>/module/eboot.elf
        --(kit/bin/allegrexrecomp emit)--> <data>/gen/djmax_*.c
        --(kit compiler, es_build)--> <data>/game/EmotionalSense-P3-game[.exe]

No git, no CMake, no system compiler, no network. Every step is idempotent;
`prepared.json` records which ISO and which launcher version made the result,
and the whole chain is redone when either changes.

Nothing produced here is ever published: it is derived from the player's own
disc (see .gitignore and the release guard).
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import es_build
import es_i18n
import es_prxreloc
from es_pipeline import GAME_ID, GAME_TITLE, Iso9660, PipelineError, parse_sfo

# sha256 of the dump the project is developed against (a hash is not game
# content). A different dump of the same game only gets a warning.
KNOWN_ISO_SHA256 = "eb2887e6827a5dd8d65c1d4aca38a837cdf7b91516c77d4241d88f4652c630ec"


GAME_EXE = "EmotionalSense-P3-game"      # the game binary (+ .exe on Windows); see scripts/make_kit.py


def user_data_dir() -> Path:
    """<app folder>/EmotionalSense-P3-data (portable; see es_portable)."""
    import es_portable
    return es_portable.data_dir()


# -- the optional 1.02 data update (a DATA.TPA the host lays over the disc's) ---

def tpa_header(path: Path):
    """(count, ok) of a DATA.TPA's plain top-level header: magic 0x0004C0DA, entry
    count in the high 16 bits of the second word, total size - 12."""
    import struct
    size = path.stat().st_size
    with open(path, "rb") as f:
        h = f.read(12)
    if len(h) < 12:
        return 0, False
    magic, w2, total = struct.unpack("<III", h)
    count = w2 >> 16
    return count, magic == 0x0004C0DA and 0 < count < 4096 and total + 12 == size


def tpa_hashes(path: Path, count: int) -> set:
    import struct
    with open(path, "rb") as f:
        f.seek(12)
        tb = f.read(16 * count)
    return {struct.unpack_from("<I", tb, 16 * i)[0] for i in range(len(tb) // 16)}


def find_update_tpa(src: Path) -> Path:
    """The DATA.TPA inside what the user picked: the file itself, or a folder that
    holds PSP/GAME/ULKS46236/DATA.TPA (or ULKS46236/DATA.TPA) at any depth up to 6.
    The patcher .exe that comes with the update is never accepted."""
    src = Path(src).expanduser()
    if src.suffix.lower() == ".exe":
        raise PipelineError(es_i18n.t("update.is_exe"))
    if src.is_file():
        if src.name.upper() != "DATA.TPA":
            raise PipelineError(es_i18n.t("update.not_found"))
        return src
    if not src.is_dir():
        raise PipelineError(es_i18n.t("update.not_found"))
    base = len(src.parts)
    for p in sorted(src.rglob("*")):
        if p.name.upper() == "DATA.TPA" and p.is_file() and len(p.parts) - base <= 6 \
                and p.parent.name.upper() == GAME_ID:
            return p
    raise PipelineError(es_i18n.t("update.not_found"))


def update_file(data: Path) -> Path:
    return data / "update" / "DATA.TPA"


def update_status(data: Path) -> int:
    """Entries the installed update replaces (0 = none installed)."""
    f = update_file(data)
    try:
        count, ok = tpa_header(f)
    except OSError:
        return 0
    return count if ok else 0


def install_update(data: Path, src: Path) -> int:
    """Copy the update's DATA.TPA to <data>/update/ after checking it. The disc and
    the extracted files are not touched. Returns the number of entries it replaces."""
    tpa = find_update_tpa(src)
    count, ok = tpa_header(tpa)
    if not ok:
        raise PipelineError(es_i18n.t("update.bad_file"))
    disc = data / "disc" / "PSP_GAME" / "INSDIR" / "DATA.TPA"
    if disc.exists():
        dcount, dok = tpa_header(disc)
        if dok and not (tpa_hashes(tpa, count) & tpa_hashes(disc, dcount)):
            raise PipelineError(es_i18n.t("update.no_match"))
    dst = update_file(data)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    shutil.copy2(tpa, tmp)
    os.replace(tmp, dst)
    (dst.parent / "DATA.combined.TPA").unlink(missing_ok=True)
    return count


def remove_update(data: Path) -> None:
    shutil.rmtree(data / "update", ignore_errors=True)


def save_dir_name() -> str:
    """The game's own save folder is `ULKS46236<>` (game id + save name). `<` and `>`
    are not valid in a Windows file name, so psprecomp's file layer
    (psp_io_escape_name) stores it as `ULKS46236%3C%3E` there."""
    return "ULKS46236%3C%3E" if sys.platform.startswith("win") else "ULKS46236<>"


def save_file(data: Path) -> Path:
    return data / "ms" / "PSP" / "SAVEDATA" / save_dir_name() / "SD.BIN"


def default_kit() -> Path:
    env = os.environ.get("EMOTIONALSENSE_KIT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here))
        for cand in (base / "kit", Path(sys.executable).resolve().parent / "kit",
                     Path(sys.executable).resolve().parent.parent / "kit"):
            if cand.exists():
                return cand
    return here.parent / "kit"


# -- data and game folders that are already on disk -----------------------------

# The files of the disc the game and the build read (the disc's icons and UMD_DATA.BIN are copied too
# when present, but nothing depends on them).
DISC_REQUIRED = ("PSP_GAME/PARAM.SFO", "PSP_GAME/SYSDIR/EBOOT.BIN", "PSP_GAME/INSDIR/DATA.TPA",
                 "PSP_GAME/INSDIR/STREAMI.TPA", "PSP_GAME/INSDIR/STREAMS.TPA", "PSP_GAME/USRDIR/STREAMV.TPA")


def check_disc_tree(root: Path) -> Optional[str]:
    """None when `root` (the folder that holds PSP_GAME) is the extracted game ULKS46236 with every
    file the build and the game read; else why not (a short English phrase for logs)."""
    for rel in DISC_REQUIRED:
        f = root / rel
        try:
            if not f.is_file() or f.stat().st_size == 0:
                return f"missing or empty: {rel}"
        except OSError as e:
            return f"{rel}: {e}"
    try:
        sfo = parse_sfo((root / "PSP_GAME" / "PARAM.SFO").read_bytes())
    except (OSError, ValueError, KeyError, IndexError):
        return "unreadable PARAM.SFO"
    if sfo.get("DISC_ID") != GAME_ID:
        return f"another game: {sfo.get('DISC_ID', '?')}"
    return None


def find_game_folder(base: Path, skip: tuple = ()) -> Optional[Path]:
    """An extracted game folder next to the program: `base` itself, or a folder inside it (two levels)
    that holds PSP_GAME/PARAM.SFO. Returns the folder that holds PSP_GAME when it passes
    check_disc_tree; a folder with another game or missing files is not offered."""
    def subdirs(d: Path) -> list:
        try:
            return sorted(x for x in d.iterdir() if x.is_dir() and x.name not in skip)
        except OSError:
            return []
    cands = [base]
    for d in subdirs(base):
        cands.append(d)
        cands.extend(subdirs(d))
    for d in cands:
        try:
            if (d / "PSP_GAME" / "PARAM.SFO").is_file() and check_disc_tree(d) is None:
                return d
        except OSError:
            continue
    return None


def data_ready(data: Path) -> bool:
    """The data folder already holds the extracted disc (marker + files): the ISO is not needed
    again, only the steps after the extraction are."""
    try:
        json.loads((data / ".extracted.json").read_text())
    except (OSError, ValueError):
        return False
    return check_disc_tree(data / "disc") is None



@dataclass
class PrepareOptions:
    iso: Optional[Path]          # an image (.iso/.chd/.cso), a folder that holds PSP_GAME, or None = the disc already in <data>/disc
    data_dir: Path = field(default_factory=user_data_dir)
    kit_dir: Path = field(default_factory=default_kit)
    jobs: int = os.cpu_count() or 4
    force: bool = False


STEPS = ["verify", "extract", "decrypt", "recomp", "compile"]
# Rough share of the wall time of each step, for the progress bar.
WEIGHT = {"verify": 0.06, "extract": 0.12, "decrypt": 0.02, "recomp": 0.20, "compile": 0.60}


class Preparer:
    def __init__(self, o: PrepareOptions, log: Callable[[str], None] = print,
                 progress: Callable[[float, str], None] = lambda f, s: None,
                 step: Callable[[str], None] = lambda s: None,
                 cancelled: Callable[[], bool] = lambda: False):
        self.o, self.log, self.progress, self.step_cb, self.cancelled = o, log, progress, step, cancelled
        self.kit: Optional[es_build.Kit] = None
        self.data = o.data_dir
        self.elf = self.data / "module" / "eboot.elf"
        self.gen = self.data / "gen"
        self.game_dir = self.data / "game"
        self._base = 0.0
        self._w = 0.0

    # -- state ---------------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.data / "prepared.json"

    def launcher_stamp(self) -> dict:
        m = self.kit.manifest
        return {"version_full": m["version_full"], "psprecomp": m["psprecomp_rev"],
                "target": self.kit.target["name"]}

    def game_exe(self) -> Path:
        return self.game_dir / (self.kit.target["exe"] if self.kit else GAME_EXE)

    def _legacy_exe(self) -> Path:
        """The game binary under its pre-EmotionalSense-P3-game name (same platform)."""
        return self.game_dir / ("emotionalsense.exe" if self.game_exe().suffix == ".exe" else "emotionalsense")

    def _adopt_legacy(self) -> None:
        """A data folder prepared by an older launcher holds the game as `emotionalsense`:
        rename it (it is the same build) so nobody has to prepare again."""
        old, new = self._legacy_exe(), self.game_exe()
        try:
            if old.exists() and not new.exists():
                os.replace(old, new)
        except OSError:
            pass

    def is_prepared(self, iso: Optional[Path] = None) -> bool:
        try:
            kit = self.kit or es_build.Kit(self.o.kit_dir)
            self.kit = kit
            st = json.loads(self.state_file.read_text())
        except (OSError, ValueError, es_build.BuildError):
            return False
        self._adopt_legacy()
        if st.get("launcher") != self.launcher_stamp() or not self.game_exe().exists():
            return False
        if iso is not None and st.get("iso", {}).get("size") != Path(iso).stat().st_size:
            return False
        return True

    # -- progress ------------------------------------------------------------

    def _frac(self, f: float, what: str):
        self.progress(self._base + self._w * max(0.0, min(1.0, f)), what)

    def _begin(self, name: str):
        self._base = sum(WEIGHT[s] for s in STEPS[:STEPS.index(name)])
        self._w = WEIGHT[name]
        self.step_cb(name)
        self._frac(0.0, "")
        self.log(f"=== {name} ===")

    def _run(self, cmd: list, quiet: bool = True, cwd: Optional[Path] = None, env: Optional[dict] = None) -> str:
        self.log("$ " + " ".join(str(c) for c in cmd))
        import es_env
        e = es_env.child_env()
        if env:
            e.update(env)
        p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace")
        lines = []
        assert p.stdout
        for line in p.stdout:
            lines.append(line.rstrip("\n"))
            if not quiet:
                self.log(lines[-1])
            if self.cancelled():
                p.terminate()
                raise PipelineError(es_i18n.t("err.cancelled"))
        if p.wait() != 0:
            for l in lines[-30:]:
                self.log(l)
            raise PipelineError(f"{Path(str(cmd[0])).name}: exit {p.returncode}")
        return "\n".join(lines)

    # -- steps ---------------------------------------------------------------

    @property
    def source_folder(self) -> Optional[Path]:
        i = self.o.iso
        return Path(i) if i is not None and str(i) != "." and Path(i).is_dir() else None

    def verify(self) -> dict:
        self._begin("verify")
        if self.o.iso is None:                    # the data folder already holds the extracted disc
            reason = None if data_ready(self.data) else "no extracted disc in the data folder"
            if reason:
                raise PipelineError(es_i18n.t("err.not_iso"))
            stamp = json.loads((self.data / ".extracted.json").read_text())
            try:
                old = json.loads(self.state_file.read_text()).get("iso", {})
            except (OSError, ValueError):
                old = {}
            self.log("using the disc already extracted in " + str(self.data / "disc"))
            return {"size": old.get("size", 0), "sha256": stamp.get("sha256", ""), "disc_id": GAME_ID,
                    "title": old.get("title", GAME_TITLE)}
        folder = self.source_folder
        if folder is not None:                    # an extracted game folder: checked, then copied by extract()
            reason = check_disc_tree(folder)
            if reason:
                raise PipelineError(es_i18n.t("err.folder_incomplete", path=str(folder), what=reason))
            files = sorted(p for p in (folder / "PSP_GAME").rglob("*") if p.is_file())
            total = sum(p.stat().st_size for p in files)
            h = hashlib.sha256()
            for p in files:
                h.update(f"{p.relative_to(folder).as_posix()}:{p.stat().st_size}\n".encode())
            sfo = parse_sfo((folder / "PSP_GAME" / "PARAM.SFO").read_bytes())
            self.log(f"folder: {folder}")
            return {"size": total, "sha256": "folder-" + h.hexdigest(), "disc_id": GAME_ID,
                    "title": sfo.get("TITLE", GAME_TITLE)}
        iso = Iso9660(self.o.iso)
        try:
            entries = {e.path: e for e in iso.walk()}
            sfo_e = entries.get("PSP_GAME/PARAM.SFO")
            if not sfo_e or "PSP_GAME/SYSDIR/EBOOT.BIN" not in entries:
                raise PipelineError(es_i18n.t("err.not_iso"))
            sfo = parse_sfo(iso.read_file(sfo_e))
        finally:
            iso.close()
        disc_id, title = sfo.get("DISC_ID", "?"), sfo.get("TITLE", "?")
        self.log(f"ISO: {title} [{disc_id}]")
        if disc_id != GAME_ID and not self.o.force:
            raise PipelineError(es_i18n.t("err.wrong_region", expected=f"{GAME_TITLE} [{GAME_ID}]",
                                          title=title, id=disc_id))
        h = hashlib.sha256()
        done = 0
        import es_chd
        # An ISO is hashed as is; a CHD is hashed over its decoded data sectors, which are
        # the bytes of the ISO it was made from (so the same hash, the same check below).
        with es_chd.open_image(self.o.iso) as f:
            size = es_chd.image_size(f) or 1
            while True:
                b = f.read(8 << 20)
                if not b:
                    break
                h.update(b)
                done += len(b)
                self._frac(done / size, "")
                if self.cancelled():
                    raise PipelineError(es_i18n.t("err.cancelled"))
        digest = h.hexdigest()
        if digest != KNOWN_ISO_SHA256:
            self.log("warning: the hash differs from the image used in development "
                     "(another dump of the same game is accepted)")
        return {"size": size, "sha256": digest, "disc_id": disc_id, "title": title}

    def extract(self, info: dict):
        self._begin("extract")
        disc = self.data / "disc"
        marker = self.data / ".extracted.json"
        stamp = {"sha256": info["sha256"]}
        if marker.exists() and not self.o.force:
            try:
                if json.loads(marker.read_text()) == stamp:
                    self.log(f"dados ja extraidos em {disc}")
                    return
            except ValueError:
                pass
        if self.o.iso is None:
            return
        folder = self.source_folder
        if folder is not None:                    # copy, never move: the user's folder stays as it is
            files = [p for p in sorted(folder.rglob("*")) if p.is_file()
                     and p.relative_to(folder).parts[0] in ("PSP_GAME", "UMD_DATA.BIN")]
            total = sum(p.stat().st_size for p in files) or 1
            done = 0
            for p in files:
                dest = disc / p.relative_to(folder)
                if not (dest.exists() and dest.stat().st_size == p.stat().st_size):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".part")
                    with open(p, "rb") as src, open(tmp, "wb") as out:
                        while True:
                            b = src.read(8 << 20)
                            if not b:
                                break
                            out.write(b)
                            done += len(b)
                            self._frac(done / total, "")
                            if self.cancelled():
                                raise PipelineError(es_i18n.t("err.cancelled"))
                    os.replace(tmp, dest)
                else:
                    done += p.stat().st_size
                    self._frac(done / total, "")
            (self.data / "ms").mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(stamp))
            return
        iso = Iso9660(self.o.iso)
        try:
            entries = [e for e in iso.walk() if not e.is_dir]
            total = sum(e.size for e in entries) or 1
            done = 0

            def prog(n):
                nonlocal done
                done += n
                self._frac(done / total, "")
                if self.cancelled():
                    raise PipelineError(es_i18n.t("err.cancelled"))

            for e in entries:
                dest = disc / e.path
                if dest.exists() and dest.stat().st_size == e.size:
                    prog(e.size)
                    continue
                iso.extract(e, dest, prog)
        finally:
            iso.close()
        (self.data / "ms").mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(stamp))

    def decrypt(self):
        self._begin("decrypt")
        eboot = self.data / "disc" / "PSP_GAME" / "SYSDIR" / "EBOOT.BIN"
        raw = self.elf.with_name("eboot.raw.elf")
        if self.elf.exists() and raw.exists() and not self.o.force:
            return
        self.elf.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run([self.kit.tool("pspdecrypt"), eboot, "-o", raw])
        except PipelineError:
            raise PipelineError(es_i18n.t("err.decrypt_failed"))
        data = raw.read_bytes() if raw.exists() else b""
        if data[:4] != b"\x7fELF":
            raise PipelineError(es_i18n.t("err.decrypt_failed"))
        try:
            out, st = es_prxreloc.relocate(data)
        except es_prxreloc.RelocError as e:
            raise PipelineError(es_i18n.t("err.relocation_failed", error=str(e)))
        self.elf.write_bytes(out)
        self.log(f"relocations: {st['total']} ({st['applied']} applied)")

    def recomp(self):
        self._begin("recomp")
        tool = self.kit.tool("allegrexrecomp")
        work = self.data / "work"
        work.mkdir(parents=True, exist_ok=True)
        try:
            self._run([tool, "funcs", self.elf], cwd=work)
            fresh = self.data / "gen.new"
            if fresh.exists():
                shutil.rmtree(fresh)
            fresh.mkdir(parents=True)
            self._frac(0.3, "")
            out = self._run([tool, "emit", self.elf, fresh, "djmax"], cwd=work)
        except PipelineError:
            raise PipelineError(es_i18n.t("err.recomp_failed"))
        self.gen.mkdir(parents=True, exist_ok=True)
        new_names = {p.name for p in fresh.iterdir()}
        for old in self.gen.iterdir():
            if old.name not in new_names:
                old.unlink()
        for p in fresh.iterdir():                 # unchanged files keep their mtime/hash
            dst = self.gen / p.name
            if not dst.exists() or dst.read_bytes() != p.read_bytes():
                shutil.copy2(p, dst)
        shutil.rmtree(fresh)
        self._frac(0.8, "")
        check = self.kit.path("bin/check_delay_slots.py")
        if check.exists():
            argv, sys.argv = sys.argv, [str(check), str(self.gen)]
            try:
                runpy.run_path(str(check), run_name="__main__")
            except SystemExit as e:
                if e.code not in (0, None):
                    raise PipelineError(es_i18n.t("err.delay_slots"))
            finally:
                sys.argv = argv
        for line in out.splitlines()[-2:]:
            self.log(line)

    def compile(self):
        self._begin("compile")
        try:
            exe = es_build.build_game(
                self.kit, self.gen, self.data / "build", self.o.jobs,
                progress=lambda f, s: self._frac(f, s), log=self.log, cancelled=self.cancelled)
        except es_build.BuildError as e:
            raise PipelineError(str(e))
        self.game_dir.mkdir(parents=True, exist_ok=True)
        for f in exe.parent.iterdir():
            if f.name in ("obj", "generated", "libpsprecomp.a"):
                continue
            dst = self.game_dir / f.name
            if f.is_symlink():
                if dst.is_symlink() or dst.exists():
                    dst.unlink()
                os.symlink(os.readlink(f), dst)
            elif f.is_file():
                shutil.copy2(f, dst)
        try:
            self._legacy_exe().unlink()           # the old name, now replaced
        except OSError:
            pass
        return self.game_dir / exe.name

    # -- driver --------------------------------------------------------------

    def run(self) -> Path:
        try:
            self.kit = es_build.Kit(self.o.kit_dir)
        except es_build.BuildError:
            raise PipelineError(es_i18n.t("err.kit_missing", path=self.o.kit_dir))
        self.data.mkdir(parents=True, exist_ok=True)
        old = None
        try:
            old = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            pass
        info = self.verify()
        # A rebuild that is interrupted must never look finished: the marker goes first and is written last.
        self.state_file.unlink(missing_ok=True)
        if old and old.get("launcher") != self.launcher_stamp() or \
                (old and old.get("iso", {}).get("sha256") != info["sha256"]):
            self.log(es_i18n.t("prepare.rebuild_notice"))
            self.o.force = True                       # new runtime or new ISO: redo it all
            shutil.rmtree(self.gen, ignore_errors=True)
        self.extract(info)
        self.decrypt()
        self.recomp()
        exe = self.compile()
        self.state_file.write_text(json.dumps({"launcher": self.launcher_stamp(), "iso": info}, indent=1))
        self.progress(1.0, "")
        self.step_cb("done")
        return exe


def selftest(kit_dir: Path, jobs: int = 4, log: Callable[[str], None] = print) -> None:
    """Build the runtime plus test_raster with the kit's compiler and run it. Needs no
    game, so CI can run it on every push."""
    import tempfile
    kit = es_build.Kit(kit_dir)
    with tempfile.TemporaryDirectory(prefix="es-selftest-") as td:
        out = Path(td)
        exe = es_build.build_game(kit, out / "gen", out, jobs, log=log, only_runtime=True)
        import es_env
        env = es_env.child_env()
        env["LD_LIBRARY_PATH"] = os.pathsep.join(d for d in (str(out), env.get("LD_LIBRARY_PATH", "")) if d)
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=env, timeout=120)
        log(r.stdout[-400:])
        if r.returncode != 0:
            raise PipelineError(f"test_raster exit {r.returncode}\n{r.stderr[-400:]}")
