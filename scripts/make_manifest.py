#!/usr/bin/env python3
"""Write kit/manifest.json: what the launcher compiles on the player's machine.

The source lists and the link wraps come from the CMake files themselves
(psprecomp/CMakeLists.txt and game/CMakeLists.txt), so a file added there is
picked up here without a second list to keep in step. The compile definitions
are the ones the top-level CMakeLists.txt forces on (real threads, SDL2,
Vulkan). With --check <build-dir> the result is compared with the build that
CMake itself produced (compile_commands.json, or `ninja -t compdb`), and the
script fails if the two disagree; CI runs that.

    make_manifest.py [-o manifest.json] [--check build]

Nothing here depends on the game: the generated djmax_*.c files are added by
the launcher at the player's machine.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSP = ROOT / "psprecomp"


def cmake_sources(text: str, start_pat: str) -> list:
    m = re.search(start_pat + r"(.*?)\)", text, re.S)
    if not m:
        sys.exit(f"manifest: {start_pat} not found")
    return re.findall(r"[\w/]+\.c\b", m.group(1))


def version() -> str:
    m = re.search(r"project\(emotionalsense\s+VERSION\s+([0-9.]+)",
                  (ROOT / "CMakeLists.txt").read_text())
    s = re.search(r'set\(EMOTIONALSENSE_VERSION_SUFFIX\s+"([^"]*)"\)', (ROOT / "CMakeLists.txt").read_text())
    return (m.group(1) if m else "0.0.0") + (s.group(1) if s else "")


def git(*args, cwd=ROOT) -> str:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def describe_full() -> str:
    """Same rule as cmake/version.cmake: git describe, unless the tree's version is ahead of the
    nearest tag (0.2.0-dev after v0.1.0), then <version>-<commits>-g<hash>[-dirty]."""
    base = version().split("-")[0]
    d = git("describe", "--tags", "--dirty").lstrip("v")
    if d and d.startswith(base):
        return d
    head, count = git("rev-parse", "--short", "HEAD"), git("rev-list", "--count", "HEAD")
    if not head:
        return version()
    dirty = "-dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
    return f"{version()}-{count}-g{head}{dirty}"


def build_manifest() -> dict:
    ptxt = (PSP / "CMakeLists.txt").read_text()
    gtxt = (ROOT / "game" / "CMakeLists.txt").read_text()
    top = (ROOT / "CMakeLists.txt").read_text()
    runtime = ["psprecomp/" + s for s in
               cmake_sources(ptxt, r"add_library\(psprecomp STATIC")]
    game = ["game/" + s for s in
            (cmake_sources(gtxt, r"set\(_game_srcs")
             or cmake_sources(gtxt, r"add_executable\(emotionalsense"))]
    rtxt = (PSP / "tools" / "allegrexrecomp" / "CMakeLists.txt").read_text()
    recompiler = ["psprecomp/tools/allegrexrecomp/" + x for x in
                  cmake_sources(rtxt, r"add_library\(allegrex_core STATIC")
                  + cmake_sources(rtxt, r"add_executable\(allegrexrecomp")]
    wrap = re.findall(r"--wrap=(\w+)", gtxt)
    defs = []
    if re.search(r"set\(PSPRECOMP_REAL_THREADS ON", top):
        defs.append("PSPRECOMP_REAL_THREADS=1")
    if re.search(r"set\(PSPRECOMP_SDL2 ON", top):
        defs.append("PSPRECOMP_SDL2=1")
    if re.search(r"set\(PSPRECOMP_VULKAN ON", top):
        defs.append("PSPRECOMP_VULKAN=1")
    if not (runtime and game and wrap and len(defs) == 3):
        sys.exit("manifest: could not read the CMake files")
    return {
        "format": 1,
        "version": version(),
        # What --version and the window title show; the same strings
        # cmake/version.cmake would put in version.h.
        "version_full": describe_full(),
        "psprecomp_rev": git("rev-parse", "--short", "HEAD", cwd=PSP) or "unknown",
        "runtime_sources": runtime,
        "game_sources": game,
        # Built instead of game/ by `launcher --selftest`: the runtime plus a
        # program that exercises it without needing the game.
        "selftest_sources": ["psprecomp/tests/test_raster.c"],
        "recompiler_sources": recompiler,
        "defines": defs,
        "include_dirs": ["psprecomp/include"],
        "cflags": ["-O3", "-DNDEBUG", "-std=gnu11"],
        "runtime_warnings": ["-Wall", "-Wextra", "-Wno-unused-parameter"],
        "generated_cflags": ["-w"],
        "generated_glob": "djmax_*.c",
        "wrap": wrap,
        # Link libraries by role; the launcher maps each role to the file the
        # kit carries for the target (kit/targets/<name>/target.json).
        "link_roles": ["m", "sdl2", "vulkan", "avcodec", "avutil", "threads"],
    }


def compdb(build: Path) -> list:
    f = build / "compile_commands.json"
    if f.exists():
        return json.loads(f.read_text())
    out = subprocess.run(["ninja", "-C", str(build), "-t", "compdb"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def check(m: dict, build: Path) -> int:
    db = compdb(build)
    seen = {}
    for e in db:
        f = e["file"]
        if not f.endswith(".c") or "/djmax_" in f:
            continue
        try:
            rel = str(Path(f).resolve().relative_to(ROOT))
        except ValueError:
            continue
        seen[rel] = [t[2:] for t in e["command"].split() if t.startswith("-D")]
    want = set(m["runtime_sources"]) | set(m["game_sources"])
    got = {p for p in seen if p.startswith(("psprecomp/src/", "game/"))
           and not p.startswith(("psprecomp/src/tools", "game/gen", "game/tests/"))}
    # game/tests/ is deliberately out of the kit: the kit is what a player builds the
    # game with, and the unit tests (built by CMake even without a game target) are
    # not part of that. Left in `got` they would also look like a game target on a
    # clean checkout and make every game/*.c read as missing.
    if not any(p.startswith("game/") for p in got):
        # game/CMakeLists.txt adds the game target only once generated C exists, which a
        # fresh checkout (CI, the release workflow) never has: check the runtime alone.
        print("manifest check: no game target configured (no generated C); "
              "checking the runtime sources only", file=sys.stderr)
        want = set(m["runtime_sources"])
    problems = []
    for p in sorted(want - got):
        problems.append(f"in the manifest, not built by CMake: {p}")
    for p in sorted(got - want):
        problems.append(f"built by CMake, not in the manifest: {p}")
    for p in sorted(want & got):
        for d in m["defines"]:
            if d not in seen[p]:
                problems.append(f"{p}: CMake does not define {d}")
    for p in problems:
        print("manifest check:", p, file=sys.stderr)
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="-")
    ap.add_argument("--check", metavar="BUILD_DIR")
    a = ap.parse_args()
    m = build_manifest()
    if a.check:
        rc = check(m, Path(a.check))
        if rc == 0:
            print(f"manifest check: ok ({len(m['runtime_sources'])} runtime + "
                  f"{len(m['game_sources'])} game sources)", file=sys.stderr)
        return rc
    text = json.dumps(m, indent=2) + "\n"
    if a.out == "-":
        sys.stdout.write(text)
    else:
        Path(a.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
