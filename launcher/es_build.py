"""Compile the game with the compiler that travels in the kit -- no CMake, no
system compiler, no -dev packages.

The kit is a directory (see scripts/make_kit.py):

    kit/manifest.json         sources, defines, wraps (scripts/make_manifest.py)
    kit/target.json           compiler command, include dirs, libraries, exe name
    kit/runtime/...           psprecomp and game sources
    kit/compiler/, deps/, bin/

Compilation is parallel and incremental: an object is rebuilt only when the
hash of its source, of the command line, or of the headers it can see changed.
Nothing here knows what the generated code is; it only globs
``djmax_*.c`` in the directory it is given.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional


class BuildError(RuntimeError):
    pass


class Kit:
    def __init__(self, root: Path):
        self.root = Path(root)
        try:
            self.manifest = json.loads((self.root / "manifest.json").read_text())
            self.target = json.loads((self.root / "target.json").read_text())
        except OSError as e:
            raise BuildError(f"incomplete kit in {self.root}: {e}")

    def path(self, rel: str) -> Path:
        return self.root / rel

    def cmd(self, key: str) -> list:
        """A command from target.json, with kit-relative paths resolved."""
        out = []
        for i, a in enumerate(self.target[key]):
            p = self.root / a
            if i == 0 and not p.exists() and (p.parent / (p.name + ".exe")).exists():
                p = p.parent / (p.name + ".exe")          # Windows kit
            out.append(str(p) if i == 0 and p.exists() else a)
        return out

    def tool(self, name: str) -> Path:
        exe = self.root / self.target["tools"][name]
        if not exe.exists():
            raise BuildError(f"tool missing from the kit: {exe}")
        return exe


def find_system_lib(name: str) -> Path:
    """A library the player's system provides (the Vulkan loader): the kit does
    not carry it, a stub would hide a missing driver."""
    dirs = [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(":") if d]
    dirs += ["/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib",
             "/lib/x86_64-linux-gnu", "/lib/aarch64-linux-gnu",
             "/lib64", "/lib", "/usr/local/lib"]
    for d in dirs:
        p = Path(d) / name
        if p.exists():
            return p
    raise BuildError(f"{name} not found: install the graphics driver / libvulkan")


def _digest(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(len(p).to_bytes(8, "little"))
        h.update(p)
    return h.hexdigest()


def _headers_digest(kit: Kit) -> bytes:
    """One hash for every header the runtime sources can include (the guest
    memory layout lives there, so a change must rebuild everything)."""
    h = hashlib.sha256()
    for d in kit.manifest["include_dirs"]:
        base = kit.path("runtime") / d
        for f in sorted(base.rglob("*.h")):
            h.update(str(f.relative_to(base)).encode())
            h.update(f.read_bytes())
    return h.digest()


def write_version_header(kit: Kit, out_dir: Path) -> Path:
    inc = out_dir / "generated"
    inc.mkdir(parents=True, exist_ok=True)
    m = kit.manifest
    text = (
        "/* Written by the launcher from the kit manifest. */\n"
        "#ifndef EMOTIONALSENSE_VERSION_H\n#define EMOTIONALSENSE_VERSION_H\n"
        f'#define EMOTIONALSENSE_VERSION      "{m["version"]}"\n'
        f'#define EMOTIONALSENSE_VERSION_FULL "{m["version_full"]}"\n'
        f'#define EMOTIONALSENSE_PSPRECOMP    "{m["psprecomp_rev"]}"\n'
        "#endif\n")
    f = inc / "version.h"
    if not f.exists() or f.read_text() != text:
        f.write_text(text)
    return inc


def build_game(kit: Kit, gen_dir: Path, out_dir: Path, jobs: int,
               progress: Callable[[float, str], None] = lambda f, s: None,
               log: Callable[[str], None] = print,
               cancelled: Callable[[], bool] = lambda: False,
               only_runtime: bool = False, mode: str = "") -> Path:
    """Compile and link. ``gen_dir`` holds djmax_*.c. mode "selftest" (or
    only_runtime=True) builds the runtime plus a test program and needs no game;
    mode "recompiler" builds allegrexrecomp itself with the kit compiler (how the
    release builds the Windows tool)."""
    mode = mode or ("selftest" if only_runtime else "game")
    only_runtime = mode != "game"
    m, t = kit.manifest, kit.target
    out_dir = Path(out_dir)
    obj_dir = out_dir / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    gen = sorted(Path(gen_dir).glob(m["generated_glob"])) if not only_runtime else []
    if not gen and not only_runtime:
        raise BuildError(f"no djmax_*.c in {gen_dir}")
    cc = kit.cmd("cc")
    inc_generated = write_version_header(kit, out_dir)
    rt = kit.path("runtime")
    defs = [f"-D{d}" for d in m["defines"]]
    incs = ["-I" + str(rt / d) for d in m["include_dirs"]]
    incs += ["-I" + str(inc_generated), "-I" + str(Path(gen_dir))]
    if mode == "recompiler":
        incs += ["-I" + str(rt / "psprecomp" / "tools" / "allegrexrecomp")]
    incs += ["-isystem" + str(kit.path(d)) for d in t["include"]]
    base = cc + defs + incs + m["cflags"] + t.get("cflags", [])
    hdr = _headers_digest(kit)
    runtime_set = {rt / s for s in m["runtime_sources"]}

    jobsl = []
    srcs = m["runtime_sources"] + {"game": m["game_sources"], "selftest": m["selftest_sources"],
                                   "recompiler": m["recompiler_sources"]}[mode]
    for src in [rt / s for s in srcs] + gen:
        extra = (m["generated_cflags"] if src in gen else
                 m["runtime_warnings"] if src in runtime_set else [])
        obj = obj_dir / (src.name + ".o") if src in gen else \
            obj_dir / (src.relative_to(rt).as_posix().replace("/", "_") + ".o")
        cmd = base + extra + ["-o", str(obj), "-c", str(src)]
        stamp = _digest(src.read_bytes(), " ".join(cmd).encode(),
                        b"" if src in gen else hdr)
        jobsl.append((src, obj, cmd, stamp))

    pending = []
    for src, obj, cmd, stamp in jobsl:
        sf = obj.with_suffix(".o.sha")
        if obj.exists() and sf.exists() and sf.read_text() == stamp:
            continue
        pending.append((src, obj, cmd, stamp))
    total = len(jobsl)
    log(f"compiling {len(pending)} of {total} files ({jobs} in parallel)")
    done = total - len(pending)
    t0 = time.time()
    errors = []

    def run(job):
        src, obj, cmd, stamp = job
        if cancelled():
            return None
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            return (src, (r.stderr or r.stdout)[-1500:])
        obj.with_suffix(".o.sha").write_text(stamp)
        return None

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for res in ex.map(run, pending):
            done += 1
            progress(0.9 * done / max(total, 1), f"{done} / {total}")
            if res:
                errors.append(res)
                ex.shutdown(cancel_futures=True)
                break
    if cancelled():
        raise BuildError("cancelled")
    if errors:
        src, msg = errors[0]
        raise BuildError(f"failed to compile {src.name}:\n{msg}")
    log(f"objects ready in {time.time() - t0:.1f} s")

    # Link: the runtime as a static archive (so the linker only takes what the
    # game uses, as CMake's psprecomp target does), then everything else.
    objs = [o for _, o, _, _ in jobsl]
    rt_objs = [o for s, o, _, _ in jobsl if s in runtime_set]
    other = [o for s, o, _, _ in jobsl if s not in runtime_set]
    lib = out_dir / "libpsprecomp.a"
    if lib.exists():
        lib.unlink()
    r = subprocess.run(kit.cmd("ar") + ["qc", str(lib)] + [str(o) for o in rt_objs],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError("ar failed:\n" + r.stderr[-800:])
    libs: list = []
    for role in m["link_roles"]:
        for a in t["libs"].get(role, []):
            if a.startswith("@find:"):
                a = str(find_system_lib(a[6:]))
            p = kit.path(a)
            libs.append(str(p) if p.exists() else a)
    wrap = [t["wrap_format"] % w for w in m["wrap"]] if not only_runtime else []
    exe = out_dir / ((mode if mode == "selftest" else "allegrexrecomp") + Path(t["exe"]).suffix
                     if mode != "game" else t["exe"])
    # Prebuilt resource objects (the game's Windows icon and version info).
    extra = [str(kit.path(x)) for x in t.get("extra_objs", [])] if mode == "game" else []
    link = (cc + ["-O3"] + wrap + [str(o) for o in other] + extra + ["-o", str(exe), str(lib)]
            + libs + t.get("ldflags", []))
    progress(0.95, "link")
    r = subprocess.run(link, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise BuildError("link failed:\n" + (r.stderr or r.stdout)[-1500:])
    # Shared libraries the executable needs beside it (SDL2, FFmpeg, winpthread).
    for f in t.get("run_files", []):
        src = kit.path(f)
        dst = out_dir / src.name
        if src.is_symlink():
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(os.readlink(src), dst)
        elif src.exists() and (not dst.exists() or dst.stat().st_size != src.stat().st_size):
            shutil.copy2(src, dst)
    progress(1.0, "ok")
    log(f"executable: {exe} ({exe.stat().st_size >> 20} MiB)")
    return exe
