#!/usr/bin/env python3
"""Fail if a release artefact contains anything derived from the game.

    release_guard.py [-h|--help] <artefact> [<artefact> ...]

An artefact is a directory, a .zip, a .tar/.tar.gz/.tar.xz or an .AppImage
(listed with `unsquashfs -o <offset> -l`, never executed;
needs squashfs-tools). The list of what is inside is
printed, then checked against names that only exist because of the game:
generated C (djmax_*.c), ELF/PRX dumps, game data (TPA, PBP, ISO, CSO,
EBOOT, ICON0.PNG, PARAM.SFO, UMD_DATA.BIN), saves (SD.BIN), the prepared
game binary and its static library, and the data folders the launcher creates
(disc/, module/, ms/, gen/). Exit status 1 on any hit.

A packaged .AppImage, .zip or .tar.gz must also be named
EmotionalSense-P3-<version>-<system>-<arch>.<ext> with system windows|linux and arch
x86_64|arm64|aarch64 (the product's display name); anything else is reported as a failure,
so a stale artefact name cannot reach a release. (The source tarball, ...-src.tar.gz, and
the pspdecrypt source are not "packaged" artefacts and are not passed to this guard by name
rules; they are listed like the rest.)
"""

import fnmatch
import os
import re
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

FORBIDDEN_NAMES = [
    "djmax_*.c", "djmax_*.h", "*.elf", "*.prx", "*.tpa", "*.pbp", "*.iso", "*.cso",
    "eboot.bin", "eboot.pbp", "icon0.png", "icon1.pmf", "pic0.png", "pic1.png",
    "param.sfo", "umd_data.bin", "sd.bin", "data.tpa", "snd0.at3",
    "emotionalsense", "emotionalsense.exe", "emotionalsense-p3-game", "emotionalsense-p3-game.exe", "libpsprecomp.a", "prepared.json",
]
FORBIDDEN_DIRS = ["disc", "module", "ms", "gen", "savedata", "psp_game"]


def appimage_offset(path: Path) -> int:
    """Offset of the squashfs in a type-2 AppImage: the end of its ELF runtime.

    Only the ELF header is parsed (64-bit little-endian, any machine: x86-64,
    aarch64), so the file is never executed and its architecture does not matter."""
    with open(path, "rb") as f:
        hdr = f.read(64)
        if len(hdr) < 64 or hdr[:4] != b"\x7fELF" or hdr[4] != 2 or hdr[5] != 1:
            raise RuntimeError(f"{path}: not a 64-bit little-endian ELF AppImage runtime")
        shoff, = struct.unpack_from("<Q", hdr, 0x28)
        shentsize, shnum = struct.unpack_from("<HH", hdr, 0x3A)
        off = shoff + shentsize * shnum
        f.seek(off)
        if f.read(4) != b"hsqs":
            raise RuntimeError(f"{path}: no squashfs magic at offset {off}")
    return off


def appimage_listing(path: Path):
    """List an AppImage with unsquashfs, never by running it. Raises on any failure."""
    off = appimage_offset(path)
    r = subprocess.run(["unsquashfs", "-o", str(off), "-l", str(path)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"unsquashfs failed on {path} (rc={r.returncode}): {r.stderr.strip()}")
    names = []
    for line in r.stdout.splitlines():
        if line == "squashfs-root" or not line.startswith("squashfs-root/"):
            continue
        names.append(line[len("squashfs-root/"):])
    if not names:
        raise RuntimeError(f"unsquashfs listed nothing for {path}")
    yield from names


def listing(path: Path):
    """Yield every member path of the artefact, '/'-separated, relative to its root."""
    p = str(path)
    if path.is_dir():
        for r, ds, fs in os.walk(path):
            for n in ds + fs:
                yield str((Path(r) / n).relative_to(path)).replace(os.sep, "/")
    elif p.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            yield from z.namelist()
    elif re.search(r"\.(tar|tar\.gz|tgz|tar\.xz|tar\.bz2)$", p):
        with tarfile.open(path) as t:
            yield from t.getnames()
    elif p.endswith(".AppImage"):
        yield from appimage_listing(path)
    else:
        yield path.name


def violations(names):
    bad = []
    for n in names:
        parts = n.strip("/").split("/")
        base = parts[-1].lower()
        for pat in FORBIDDEN_NAMES:
            if fnmatch.fnmatchcase(base, pat):
                bad.append((n, f"name matches {pat}"))
                break
        else:
            for d in parts[:-1]:
                if d.lower() in FORBIDDEN_DIRS:
                    bad.append((n, f"inside a '{d}' folder"))
                    break
    return bad


PRODUCT_PREFIX = "EmotionalSense-P3-"
PACKAGE_NAME = re.compile(r"^EmotionalSense-P3-\d+\.\d+\.\d+-(windows|linux)-(x86_64|arm64|aarch64)\.(AppImage|zip|tar\.gz)$")


def name_problem(path: Path):
    """The release name of a packaged artefact, or None when it is fine."""
    n = path.name
    if n.endswith((".AppImage", ".zip")) and not n.startswith(PRODUCT_PREFIX):
        return f"artefact name must start with {PRODUCT_PREFIX}"
    if n.startswith(PRODUCT_PREFIX) and n.endswith((".AppImage", ".zip", ".tar.gz")) and not n.endswith("-src.tar.gz") \
            and not PACKAGE_NAME.match(n):
        return ("package name must be EmotionalSense-P3-<version>-<windows|linux>-<x86_64|arm64|aarch64>."
                "<AppImage|zip|tar.gz>")
    return None


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    rc = 0
    for a in argv:
        path = Path(a)
        bad_name = name_problem(path)
        if bad_name:
            print(f"{a}: FAILED, {bad_name}")
            rc = 1
            continue
        names = list(listing(path))
        print(f"{a}: {len(names)} entries")
        bad = violations(names)
        for n, why in bad[:50]:
            print(f"  FORBIDDEN {n}  ({why})")
        if bad:
            print(f"{a}: FAILED, {len(bad)} forbidden entries")
            rc = 1
        else:
            print(f"{a}: clean")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
