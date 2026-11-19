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
