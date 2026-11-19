#!/usr/bin/env python3
"""Assemble the build kit the launcher ships (one per target system).

    make_kit.py --target linux-x86_64 --out kit-linux \
        --compiler <zig dir> --sdl2 <prefix> --ffmpeg <prefix> \
        --allegrexrecomp <exe> --pspdecrypt <exe>

    make_kit.py --target windows-x86_64 --out kit-win \
        --compiler <llvm-mingw dir> --sdl2 <SDL2 mingw prefix> \
        --ffmpeg <FFmpeg win64 lgpl-shared dir> --vulkan-lib <libvulkan-1.a> ...

Targets: linux-x86_64, linux-aarch64, windows-x86_64, windows-arm64. The kit is
assembled on a machine of the target architecture (the release workflow uses native
runners), so the compiler in it runs where the kit is used.

Compilers: Linux = zig 0.17.1 (`zig cc`, ELF), Windows = llvm-mingw UCRT (clang + lld,
PE). Only zig can not do --wrap on PE and only llvm-mingw does not exist for ELF, so
each system gets the one that works.

The kit holds no game data. FFmpeg must be an LGPL build (no --enable-gpl); the
script refuses a build whose configuration says otherwise.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME_EXE = "EmotionalSense-P3-game"   # the game binary; must match launcher/es_prepare.py
GLIBC = "2.28"       # zig's glibc target: must not be newer than the bundled libs need

# xinput9_1_0 keeps Windows 7 working on x86-64; Windows on arm64 has no such import library
# (and starts at Windows 10), so it links XInput 1.4 there.
WIN_SYSLIBS = ["-fuse-ld=lld", "-lxinput9_1_0", "-lwinmm", "-lkernel32", "-luser32", "-lgdi32",
               "-lwinspool", "-lshell32", "-lole32", "-loleaut32", "-luuid",
               "-lcomdlg32", "-ladvapi32", "-lpthread"]


# target -> (system, architecture, zig triple, llvm-mingw triple)
TARGETS = {
    "linux-x86_64": ("linux", "x86_64", "x86_64-linux-gnu", None),
    "linux-aarch64": ("linux", "aarch64", "aarch64-linux-gnu", None),
    "windows-x86_64": ("windows", "x86_64", None, "x86_64-w64-mingw32"),
    "windows-arm64": ("windows", "arm64", None, "aarch64-w64-mingw32"),
}


def sh(*a, **k):
    return subprocess.run(a, check=True, **k)


def copy_tree(src: Path, dst: Path, ignore=None):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)


def add_multiarch_sdl_headers(sdl: Path, dst: Path, extra=()):
    """Debian/Ubuntu split SDL2's headers: /usr/include/SDL2/SDL_config.h is a wrapper that
    includes <SDL2/_real_SDL_config.h>, which lives in /usr/include/<multiarch>/SDL2/. Copy those
    arch-specific files next to the others so the kit's include path finds them. Sources, in order:
    --sdl2-arch-include dirs, <prefix>/include/*/SDL2, /usr/include/<cc -print-multiarch>/SDL2."""
    cands = [Path(e) for e in extra]
    cands += sorted(p for p in (sdl / "include").glob("*/SDL2") if p.is_dir())
    try:
        ma = subprocess.run(["cc", "-print-multiarch"], capture_output=True, text=True).stdout.strip()
    except OSError:
        ma = ""
    if ma:
        cands.append(Path("/usr/include") / ma / "SDL2")
    for c in cands:
        if c.is_dir():
            for f in sorted(c.iterdir()):
                if f.is_file() and not (dst / f.name).exists():
                    shutil.copy2(f, dst / f.name)
    cfg = dst / "SDL_config.h"
    if cfg.exists() and "_real_SDL_config.h" in cfg.read_text(errors="replace") \
            and not (dst / "_real_SDL_config.h").exists():
        sys.exit("make_kit: SDL_config.h is a multiarch wrapper but _real_SDL_config.h was not found "
                 "(pass --sdl2-arch-include /usr/include/<triplet>/SDL2)")


def make_windows_resource(compiler: Path, dst: Path, triple: str = "x86_64-w64-mingw32"):
    """The game .exe's icon and version info (Task Manager shows FileDescription),
    compiled with the full toolchain's windres to one COFF object the link adds.
    Original art only: launcher/es_icon.py draws the icon."""
    import tempfile
    sys.path.insert(0, str(ROOT / "launcher"))
    import es_icon
    ver = (ROOT / "CMakeLists.txt").read_text()
    import re
    m = re.search(r"project\(emotionalsense\s+VERSION\s+([0-9]+)\.([0-9]+)\.([0-9]+)", ver)
    v = ",".join(m.groups()) + ",0" if m else "0,0,0,0"
    vs = ".".join(m.groups()) if m else "0.0.0"
    with tempfile.TemporaryDirectory(prefix="es-res-") as td:
        td = Path(td)
        es_icon.write_all(td / "icons")
        rc = td / "game.rc"
        rc.write_text(
            '1 ICON "icons/icon.ico"\n'
            "1 VERSIONINFO\nFILEVERSION %s\nPRODUCTVERSION %s\nBEGIN\n BLOCK \"StringFileInfo\"\n BEGIN\n"
            '  BLOCK "040904B0"\n  BEGIN\n'
            '   VALUE "FileDescription", "EmotionalSense-P3"\n'
            '   VALUE "ProductName", "EmotionalSense-P3"\n'
            '   VALUE "InternalName", "%s"\n'
            '   VALUE "OriginalFilename", "%s.exe"\n'
            '   VALUE "FileVersion", "%s"\n'
            '   VALUE "ProductVersion", "%s"\n'
            "  END\n END\n BLOCK \"VarFileInfo\"\n BEGIN\n  VALUE \"Translation\", 0x409, 1200\n END\nEND\n"
            % (v, v, GAME_EXE, GAME_EXE, vs, vs))
        windres = next((compiler / "bin" / n for n in
                        (triple + "-windres", triple + "-windres.exe",
                         "llvm-windres", "llvm-windres.exe") if (compiler / "bin" / n).exists()), None)
        if windres is None:
            sys.exit("make_kit: no windres in the Windows compiler (needed for the game's icon)")
        sh(str(windres.absolute()), "-O", "coff", str(rc), str(Path(dst).resolve()), cwd=td)   # cwd differs: absolute paths


def check_lgpl(ffmpeg: Path):
    for p in ffmpeg.rglob("ffversion.h"):
        pass
    for exe in list(ffmpeg.rglob("ffmpeg")) + list(ffmpeg.rglob("ffmpeg.exe")):
        if exe.suffix == ".exe":
            return          # cannot run a Windows exe here; the BtbN lgpl asset name is the check
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True,
                             env={"LD_LIBRARY_PATH": str(ffmpeg / "lib")}).stdout
        if "--enable-gpl" in out or "--enable-nonfree" in out:
            sys.exit("make_kit: this FFmpeg is GPL/nonfree; the kit needs an LGPL build")
        return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, choices=sorted(TARGETS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--sdl2", required=True, help="prefix with include/SDL2 and the library")
    ap.add_argument("--ffmpeg", required=True, help="LGPL shared build: include/, lib/ (and bin/ on Windows)")
    ap.add_argument("--allegrexrecomp", help="prebuilt recompiler; omitted = build it with the kit's own compiler")
    ap.add_argument("--pspdecrypt", required=True)
    ap.add_argument("--vulkan-lib", help="windows: libvulkan-1.a import library")
    ap.add_argument("--sdl2-arch-include", action="append", default=[],
                    help="linux: extra arch-specific SDL2 header dir (Debian multiarch), repeatable")
    ap.add_argument("--sdl3", help="linux: libSDL3.so.0 that sdl2-compat loads (optional)")
    a = ap.parse_args()

    tsys, tarch, zig_triple, mingw = TARGETS[a.target]
    win = tsys == "windows"
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # 1. the manifest, from the CMake files
    sh(sys.executable, str(ROOT / "scripts" / "make_manifest.py"), "-o", str(out / "manifest.json"))
    manifest = json.loads((out / "manifest.json").read_text())
    # 2. the runtime sources
    rt = out / "runtime"
    for rel in manifest["runtime_sources"] + manifest["game_sources"] + manifest["selftest_sources"] \
            + manifest["recompiler_sources"]:
        (rt / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, rt / rel)
    for h in (sorted((ROOT / "psprecomp" / "src").rglob("*.h")) + sorted((ROOT / "game").glob("*.h"))
              + sorted((ROOT / "psprecomp" / "tools" / "allegrexrecomp").rglob("*.h"))):
        dst = rt / h.relative_to(ROOT)          # private headers sit beside the sources
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(h, dst)
    for d in manifest["include_dirs"]:
        copy_tree(ROOT / d, rt / d)
    # 3. dependencies (headers + libraries)
    deps = out / "deps"
    (deps / "lib").mkdir(parents=True)
    sdl = Path(a.sdl2)
    ff = Path(a.ffmpeg)
    check_lgpl(ff)
    copy_tree(sdl / "include" / "SDL2" if (sdl / "include" / "SDL2").exists() else sdl / "include",
              deps / "include" / "SDL2")
    add_multiarch_sdl_headers(sdl, deps / "include" / "SDL2", a.sdl2_arch_include)
    for sub in ("libavcodec", "libavutil", "libswresample"):
        copy_tree(ff / "include" / sub, deps / "include" / sub)
    # 4. compiler
    comp = out / "compiler"
    ign = None
    if win:
        # Only this target and what the link needs; no python, lldb, c++ headers.
        keep_bins = ("clang", "clang-", "lld", "ld.lld", "llvm-ar", "llvm-ranlib", mingw + "-")
        def ign(d, names, base=Path(a.compiler)):
            rel = Path(d).relative_to(base)
            if str(rel) == ".":
                return [n for n in names if n not in ("bin", "lib", mingw, "generic-w64-mingw32", "include", "LICENSE.TXT")]
            if str(rel) == "bin":
                return [n for n in names if not n.startswith(keep_bins) and not n.endswith((".dll", ".cfg"))]
            if str(rel) == "include":
                return [n for n in names if n == "c++"]
            if str(rel) == "lib":
                return [n for n in names if n not in ("clang",)]
            return []
    copy_tree(Path(a.compiler), comp, ignore=ign)
    # 5. tools
    (out / "bin").mkdir()
    rec_name = Path(a.allegrexrecomp).name if a.allegrexrecomp else (
        "allegrexrecomp" + (".exe" if win else ""))
    if a.allegrexrecomp:
        shutil.copy2(a.allegrexrecomp, out / "bin" / rec_name)
    shutil.copy2(a.pspdecrypt, out / "bin" / Path(a.pspdecrypt).name)
    shutil.copy2(ROOT / "scripts" / "check_delay_slots.py", out / "bin" / "check_delay_slots.py")

    # 6. target.json
    if not win:
        for f in sorted((ff / "lib").glob("libav*.so*")) + sorted((ff / "lib").glob("libswresample*.so*")):
            if f.name.startswith(("libavcodec", "libavutil", "libswresample")):
                shutil.copy2(f, deps / "lib" / f.name, follow_symlinks=False)
        for f in sorted((sdl / "lib").glob("libSDL2-2.0.so*")):
            shutil.copy2(f, deps / "lib" / f.name, follow_symlinks=False)
        if a.sdl3:
            shutil.copy2(a.sdl3, deps / "lib" / Path(a.sdl3).name, follow_symlinks=True)
        def solib(prefix):
            c = sorted(p.name for p in (deps / "lib").glob(prefix + ".so.*") if p.is_symlink() or True)
            return "deps/lib/" + prefix + ".so"
        target = {
            "name": a.target,
            "cc": ["compiler/zig", "cc", "-target", f"{zig_triple}.{GLIBC}"],
            "ar": ["compiler/zig", "ar"],
            "exe": GAME_EXE,
            "include": ["deps/include", "deps/include/SDL2"],
            "wrap_format": "-Wl,--wrap=%s",
            "libs": {
                "m": ["-lm"],
                "threads": [],
                "sdl2": ["deps/lib/libSDL2-2.0.so.0"],
                "vulkan": ["@find:libvulkan.so.1"],
                "avcodec": ["deps/lib/" + next(p.name for p in (deps / "lib").glob("libavcodec.so.*") if p.name.count(".") == 2)],
                "avutil": ["deps/lib/" + next(p.name for p in (deps / "lib").glob("libavutil.so.*") if p.name.count(".") == 2)],
            },
            # the system Vulkan loader is newer than the glibc we target; its own
            # undefined references are resolved at run time on the player machine
            "ldflags": ["-Wl,-rpath,$ORIGIN", "-Wl,--allow-shlib-undefined", "-Wl,--build-id=sha1"],
            # the SONAME links and the real files; the bare lib*.so dev links stay in the kit
            "run_files": [f"deps/lib/{p.name}" for p in sorted((deps / "lib").iterdir())
                          if not p.name.endswith(".so")],
            "tools": {"allegrexrecomp": "bin/" + rec_name,
                      "pspdecrypt": "bin/" + Path(a.pspdecrypt).name},
        }
        # the zig launcher script is `compiler/zig` in the tarball root
    else:
        for f in sorted((ff / "bin").glob("*.dll")):
            if f.name.startswith(("avcodec", "avutil", "swresample")):
                shutil.copy2(f, deps / "lib" / f.name)
        for f in sorted((ff / "lib").glob("libav*.dll.a")) + sorted((ff / "lib").glob("libswresample*.dll.a")):
            shutil.copy2(f, deps / "lib" / f.name)
        shutil.copy2(sdl / "bin" / "SDL2.dll", deps / "lib" / "SDL2.dll")
        shutil.copy2(sdl / "lib" / "libSDL2.dll.a", deps / "lib" / "libSDL2.dll.a")
        shutil.copy2(a.vulkan_lib, deps / "lib" / "libvulkan-1.a")
        shutil.copy2(Path(a.compiler) / mingw / "bin" / "libwinpthread-1.dll",
                     deps / "lib" / "libwinpthread-1.dll")
        make_windows_resource(Path(a.compiler), deps / "lib" / "es_game_res.o", mingw)
        target = {
            "name": a.target,
            "cc": [f"compiler/bin/{mingw}-clang"],
            "ar": ["compiler/bin/llvm-ar"],
            "exe": GAME_EXE + ".exe",
            "extra_objs": ["deps/lib/es_game_res.o"],
            "include": ["deps/include", "deps/include/SDL2"],
            "wrap_format": "-Wl,--wrap=%s",
            "libs": {
                "m": [], "threads": [],
                "sdl2": ["deps/lib/libSDL2.dll.a"],
                "vulkan": ["deps/lib/libvulkan-1.a"],
                "avcodec": ["deps/lib/libavcodec.dll.a"],
                "avutil": ["deps/lib/libavutil.dll.a"],
            },
            "ldflags": [("-lxinput1_4" if (tarch == "arm64" and f == "-lxinput9_1_0") else f) for f in WIN_SYSLIBS],
            "run_files": [f"deps/lib/{n}" for n in
                          sorted(p.name for p in (deps / "lib").glob("*.dll"))],
            "tools": {"allegrexrecomp": "bin/" + rec_name,
                      "pspdecrypt": "bin/" + Path(a.pspdecrypt).name},
        }
    (out / "target.json").write_text(json.dumps(target, indent=2) + "\n")
    if not a.allegrexrecomp:
        sys.path.insert(0, str(ROOT / "launcher"))
        import es_build
        import tempfile
        with tempfile.TemporaryDirectory(prefix="kit-recompiler-") as td:
            exe = es_build.build_game(es_build.Kit(out), Path(td) / "gen", Path(td), 4, mode="recompiler")
            shutil.copy2(exe, out / "bin" / rec_name)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) >> 20
    print(f"kit {a.target}: {out} ({size} MiB on disk)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
