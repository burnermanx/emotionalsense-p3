"""Portable layout: everything the launcher and the game write lives next to the program.

    <app folder>/EmotionalSense-P3-data/     launcher.json, disc/, module/, gen/, game/, ms/ (the save),
                                             es_scores.json, update/, textures/, tools/, cache/, tmp/,
                                             logs/, crashes/

<app folder> is the folder of the .exe (Windows), the folder of the .AppImage file
($APPIMAGE -- never the mount point) or the folder of the launcher binary (tar.gz).
Nothing is written to the home folder, %APPDATA%, %LOCALAPPDATA%, ~/.cache, ~/.config
or ~/.local, and `apply_env` points the temporary and cache folders of every child
process (compiler, game, graphics drivers) into the data folder as well.

Developers: EMOTIONALSENSE_DATA (or --data) still selects any folder, and the real-data
guard in es_pipeline still applies to the repository's work/data.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

DATA_NAME = "EmotionalSense-P3-data"


class PortableError(RuntimeError):
    pass


def app_dir() -> Path:
    """The folder the user sees: next to the .exe / .AppImage / launcher binary."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return Path(appimage).resolve().parent
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent          # a source checkout


def data_dir() -> Path:
    env = os.environ.get("EMOTIONALSENSE_DATA")
    return Path(env) if env else app_dir() / DATA_NAME


def check_writable(path: Path) -> None:
    """Create `path` and prove it can be written; a clear error otherwise (never a silent
    fall back to the home folder)."""
    from es_i18n import t
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".w-", delete=True):
            pass
    except OSError as e:
        raise PortableError(t("portable.not_writable", path=str(path.parent if path.name == DATA_NAME else path),
                              error=str(e)))


def cache_env(data: Path) -> dict:
    """Environment that keeps temporary files and driver/compiler caches inside `data`."""
    cache, tmp = data / "cache", data / "tmp"
    return {
        "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp),
        "XDG_CACHE_HOME": str(cache),
        "MESA_SHADER_CACHE_DIR": str(cache / "mesa"),
        "MESA_GLSL_CACHE_DIR": str(cache / "mesa"),
        "__GL_SHADER_DISK_CACHE_PATH": str(cache / "gl"),
        "CUDA_CACHE_PATH": str(cache / "cuda"),
        "ZIG_GLOBAL_CACHE_DIR": str(cache / "zig-global"),
        "ZIG_LOCAL_CACHE_DIR": str(cache / "zig-local"),
        "PYTHONPYCACHEPREFIX": str(cache / "pycache"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def apply_env(data: Path) -> None:
    """Set the cache environment for this process (and so for every child it starts)."""
    env = cache_env(data)
    for k, v in env.items():
        os.environ[k] = v
    for sub in ("cache", "tmp", "logs"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = env["TMPDIR"]


# -- data of older versions, in the places they used ---------------------------

LEGACY_MARKERS = ("prepared.json", "launcher.json", "ms", "disc", "es_scores.json")


def legacy_dirs() -> List[Path]:
    """Where earlier versions kept their data (read only, for the migration offer)."""
    out: List[Path] = []
    if sys.platform.startswith("win"):
        for var in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(var)
            if base:
                out += [Path(base) / "EmotionalSense", Path(base) / "emotionalsense"]
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        out += [Path(xdg) / "emotionalsense", Path(xdg) / "emotionalsense-portable"]
    seen, res = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            res.append(p)
    return res


def find_legacy(data: Path) -> Optional[Path]:
    """An old data folder worth copying: it holds something and `data` does not hold it yet."""
    for p in legacy_dirs():
        try:
            if p.resolve() == data.resolve() or not p.is_dir():
                continue
            if any((p / m).exists() and not (data / m).exists() for m in LEGACY_MARKERS):
                return p
        except OSError:
            continue
    return None


def migrate(src: Path, dst: Path, log: Callable[[str], None] = print) -> int:
    """Copy (never move, never delete) what `src` holds into `dst`, skipping what is already
    there. Symlinks are copied as links. Returns the number of files copied."""
    n = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            s, d = Path(root) / f, dst / rel / f
            if d.exists() or d.is_symlink():
                continue
            if s.is_symlink():
                os.symlink(os.readlink(s), d)
            else:
                shutil.copy2(s, d)
            n += 1
    log(f"copied {n} files from {src}")
    return n
