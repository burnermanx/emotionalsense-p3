"""The environment handed to every program the launcher starts (the game, the kit compiler,
pspdecrypt, the file manager).

A PyInstaller launcher is started with its own `_internal` folder first in LD_LIBRARY_PATH (the
bootloader keeps the system value in LD_LIBRARY_PATH_ORIG), and its Tk/Python runtime hooks set
TCL_LIBRARY and friends. None of that belongs to the programs we start: with `_internal` (the
build machine's libstdc++, libz, libzstd, libexpat, ...) in front, the system's own libraries
are shadowed inside the game, so a Vulkan driver or an implicit layer (the Steam Deck's Gamescope
WSI layer) that loads there can fail, and the layer aborts the whole process. Children get the
environment the user's system gave the launcher.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Optional

PATH_VARS = ("LD_LIBRARY_PATH", "LIBPATH", "DYLD_LIBRARY_PATH", "SHLIB_PATH")
# set by the PyInstaller runtime hooks / bootloader for the frozen process only
RUNTIME_VARS = ("TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH", "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE")


def _roots(env: Mapping[str, str]) -> list:
    """Folders that belong to the frozen launcher itself."""
    roots = []
    for r in (getattr(sys, "_MEIPASS", None), env.get("_MEIPASS2"), env.get("_PYI_APPLICATION_HOME_DIR")):
        if r:
            roots.append(os.path.normpath(r))
    if getattr(sys, "frozen", False):
        roots.append(os.path.normpath(os.path.dirname(sys.executable)))
    return roots


def _inside(path: str, roots: list) -> bool:
    p = os.path.normpath(path)
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def child_env(base: Optional[Mapping[str, str]] = None) -> dict:
    """A copy of `base` (default: os.environ) as the system gave it to the launcher."""
    env = dict(os.environ if base is None else base)
    roots = _roots(env)
    for var in PATH_VARS:
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:                       # the bootloader saved the system value (maybe empty)
            if orig:
                env[var] = orig
            else:
                env.pop(var, None)
        elif var in env:                           # not frozen by us, or no saved value: drop our folders
            kept = [d for d in env[var].split(os.pathsep) if d and not _inside(d, roots)]
            if kept:
                env[var] = os.pathsep.join(kept)
            else:
                env.pop(var, None)
    for var in RUNTIME_VARS:
        if var in env and (not roots or any(_inside(d, roots) for d in env[var].split(os.pathsep) if d)):
            env.pop(var)
    for var in [v for v in env if v.startswith("_PYI_") or v == "_MEIPASS2"]:
        env.pop(var)
    return env


# Folders of the Steam client and its Linux runtime. Steam hands them to every program it starts
# (a non-Steam shortcut in Game Mode too): LD_LIBRARY_PATH carries the scout runtime and the
# Steam folders, LD_PRELOAD the overlay. The game needs none of it: its own libraries come from its
# folder and the system's Vulkan driver, loader and layers must be the system's. Steam's folders
# first would put Steam's SDL3, the runtime's libstdc++/libdrm/... in front of what the driver was
# built against.
STEAM_DIRS = frozenset(("steam-runtime", "ubuntu12_32", "ubuntu12_64", "steamrt32", "steamrt64", "steamrt",
                        "pinned_libs_32", "pinned_libs_64", "libcurl_compat_32", "libcurl_compat_64"))


def _steam_path(path: str) -> bool:
    return any(c in STEAM_DIRS for c in path.replace("\\", "/").split("/"))


def _steam_preload(entry: str) -> bool:
    return os.path.basename(entry.replace("\\", "/")).startswith("gameoverlayrenderer")


def game_env(base: Optional[Mapping[str, str]] = None) -> dict:
    """The environment of the game itself: `child_env`, without the Steam client's own library
    folders in LD_LIBRARY_PATH and without its overlay in LD_PRELOAD. EMOTIONALSENSE_KEEP_STEAM_ENV=1
    leaves them (to compare)."""
    env = child_env(base)
    if env.get("EMOTIONALSENSE_KEEP_STEAM_ENV", "") not in ("", "0"):
        return env
    for var in PATH_VARS:
        if var in env:
            kept = [d for d in env[var].split(os.pathsep) if d and not _steam_path(d)]
            if kept:
                env[var] = os.pathsep.join(kept)
            else:
                env.pop(var)
    if "LD_PRELOAD" in env:
        kept = [e for e in env["LD_PRELOAD"].replace(":", " ").split() if not _steam_preload(e)]
        if kept:
            env["LD_PRELOAD"] = " ".join(kept)
        else:
            env.pop("LD_PRELOAD")
    return env
