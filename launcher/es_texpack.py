"""AI high-resolution textures, built on the player's machine from their own disc.

EXPERIMENTAL and hidden: nothing here is reachable unless `ES_TEXPACK_PROTO=1` is set (no option
on the Options screen, `--build-textures` refuses to run, nothing is downloaded). The hand-made
texture folder (`texture_pack` in launcher.json, or `<data>/textures/`) works without the flag.

Nothing here ships in the packages: the upscaling tools and models are downloaded on demand
from their official releases (URL and sha256 pinned below), into `<data>/tools`, and the
textures are collected from the game and upscaled into `<data>/textures`. Both stay on the
player's machine (they are derived from the disc).

Steps (each one resumable, each one cancellable):
  1. tools    download + verify + unpack waifu2x-ncnn-vulkan and Real-ESRGAN-ncnn-vulkan
  2. dump     run the game headless along the dump routes (EMOTIONALSENSE_TEX_DUMP) on a scratch
              copy of the save, so the real save is never touched
  3. build    texpack/make_tex_pack.py, profile "default": illustrations with waifu2x (cunet,
              noise 0), interface and track with Real-ESRGAN (Vulkan); see the notes there

Licences, shown on the launcher screen and in THIRD_PARTY_NOTICES.md: waifu2x-ncnn-vulkan MIT
(nihui) with the waifu2x cunet models; Real-ESRGAN-ncnn-vulkan MIT with realesrgan-x4plus
BSD-3-Clause (xinntao); 4xTextureDAT2_otf CC BY 4.0 (Philip Hofmann), the model the project's own
reference pack was made with (this launcher path does not use it).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ATTRIBUTION = ("waifu2x-ncnn-vulkan and cunet models (MIT, nihui); Real-ESRGAN-ncnn-vulkan (MIT) with "
               "realesrgan-x4plus (BSD-3-Clause, xinntao); 4xTextureDAT2_otf by Philip Hofmann (CC BY 4.0), "
               "https://creativecommons.org/licenses/by/4.0/ ; https://github.com/Phhofm/models")

COMPLETE_MARKER = ".es-complete"          # inside <data>/textures: the pack finished
DISABLED_DIR = "textures-disabled"        # a folder that does not exist: the game then loads no pack


@dataclass(frozen=True)
class Tool:
    name: str
    url: str
    sha256: str
    size_mb: int
    exe: str                 # path of the program inside the unpacked folder
    models: str              # a folder that must exist after unpacking
    dest: str = ""           # unpack into <tools>/<dest> (for an archive that has no top folder)


_W2X = "https://github.com/nihui/waifu2x-ncnn-vulkan/releases/download/20250915/"
_ESR = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
TOOLS: Dict[str, List[Tool]] = {
    "linux-x86_64": [
        Tool("waifu2x", _W2X + "waifu2x-ncnn-vulkan-20250915-linux.zip",
             "848e0fba55657d34da90b775b8139e9806dc754798b029f95e106ba8850a731f", 35,
             "waifu2x-ncnn-vulkan-20250915-linux/waifu2x-ncnn-vulkan", "waifu2x-ncnn-vulkan-20250915-linux/models-cunet"),
        Tool("realesrgan", _ESR + "realesrgan-ncnn-vulkan-20220424-ubuntu.zip",
             "e5aa6eb131234b87c0c51f82b89390f5e3e642b7b70f2b9bbe95b6a285a40c96", 43,
             "realesrgan-ncnn-vulkan-20220424/realesrgan-ncnn-vulkan", "realesrgan-ncnn-vulkan-20220424/models",
             "realesrgan-ncnn-vulkan-20220424"),
    ],
    "windows-x86_64": [
        Tool("waifu2x", _W2X + "waifu2x-ncnn-vulkan-20250915-windows.zip",
             "7425be94b94e4c8f37a1e433ac0e0100c43790e2c37418f4b65d8235adfbdc87", 34,
             "waifu2x-ncnn-vulkan-20250915-windows/waifu2x-ncnn-vulkan.exe", "waifu2x-ncnn-vulkan-20250915-windows/models-cunet"),
        Tool("realesrgan", _ESR + "realesrgan-ncnn-vulkan-20220424-windows.zip",
             "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d", 42,
             "realesrgan-ncnn-vulkan-20220424/realesrgan-ncnn-vulkan.exe", "realesrgan-ncnn-vulkan-20220424/models",
             "realesrgan-ncnn-vulkan-20220424"),
    ],
}

# Measured on the development machine (RTX 3060, 16 threads); the screen says "about".
ESTIMATE_MINUTES = 35


class TexPackError(RuntimeError):
    pass


class Cancelled(TexPackError):
    pass


# -- what this machine can do -------------------------------------------------------------------

def platform_key() -> Optional[str]:
    """linux-x86_64 / windows-x86_64, or None: the upscaling tools have no arm64 build."""
    m = platform.machine().lower()
    arch = "x86_64" if m in ("x86_64", "amd64") else ("arm64" if m in ("arm64", "aarch64") else m)
    system = "windows" if sys.platform.startswith("win") else "linux" if sys.platform.startswith("linux") else sys.platform
    key = f"{system}-{arch}"
    return key if key in TOOLS else None


def arch_name() -> str:
    return platform.machine().lower() or "this CPU"


def vulkan_present() -> bool:
    """Is there a Vulkan loader? (The tools then fail on their own if there is no device.)"""
    import ctypes
    names = ("vulkan-1.dll",) if sys.platform.startswith("win") else ("libvulkan.so.1", "libvulkan.so")
    for n in names:
        try:
            ctypes.CDLL(n)
            return True
        except OSError:
            continue
    return False


def download_size_mb() -> int:
    k = platform_key()
    return sum(t.size_mb for t in TOOLS[k]) if k else 0


ENABLED = os.environ.get("ES_TEXPACK_PROTO") == "1"


def pack_dir(data: Path) -> Path:
    return data / "textures"


def pack_built(data: Path) -> bool:
    return (pack_dir(data) / COMPLETE_MARKER).exists()


def pack_env(data: Path, cfg: dict) -> Dict[str, str]:
    """Environment for the game: the pack when the option is on and built, else a folder that does not
    exist so that nothing is loaded (the game would otherwise pick <data>/textures by itself)."""
    if cfg.get("texture_pack"):                       # a folder the player chose by hand (launcher.json)
        return {"EMOTIONALSENSE_TEX_PACK": str(cfg["texture_pack"])}
    if not ENABLED:                                   # hidden option: the game itself picks <data>/textures
        return {}
    if cfg.get("texpack", "off") == "on" and pack_built(data):
        return {"EMOTIONALSENSE_TEX_PACK": str(pack_dir(data))}
    return {"EMOTIONALSENSE_TEX_PACK": str(data / DISABLED_DIR)} if pack_dir(data).exists() else {}


# -- download -----------------------------------------------------------------------------------

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def download(url: str, sha256: str, dest: Path, progress: Callable[[float], None] = lambda f: None,
             cancelled: Callable[[], bool] = lambda: False) -> None:
    """Fetch `url` into `dest` (atomic), refusing a file whose sha256 differs from the pinned one."""
    if dest.exists() and sha256_file(dest) == sha256:
        progress(1.0)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "EmotionalSense-P3-launcher"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                blk = r.read(1 << 18)
                if not blk:
                    break
                out.write(blk)
                got += len(blk)
                if total:
                    progress(got / total)
                if cancelled():
                    raise Cancelled("cancelled")
    except (OSError, ValueError) as e:
        part.unlink(missing_ok=True)
        raise TexPackError(f"{url}: {e}")
    except Cancelled:
        part.unlink(missing_ok=True)
        raise
    if sha256_file(part) != sha256:
        part.unlink(missing_ok=True)
        raise TexPackError(f"{url}: the file does not match the pinned sha256")
    os.replace(part, dest)


def unpack(zip_path: Path, dest: Path) -> None:
    """Extract, refusing any member that would land outside `dest`."""
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if root != target and root not in target.parents:
                raise TexPackError(f"{zip_path.name}: unsafe path {m.filename}")
        z.extractall(dest)


def ensure_tools(data: Path, progress: Callable[[float], None] = lambda f: None,
                 cancelled: Callable[[], bool] = lambda: False,
                 log: Callable[[str], None] = lambda s: None) -> Dict[str, Path]:
    """-> {'waifu2x': exe path, 'realesrgan': exe path}, downloading what is missing."""
    key = platform_key()
    if not key:
        raise TexPackError("unsupported platform")
    tools = TOOLS[key]
    base = data / "tools"
    out: Dict[str, Path] = {}
    for i, t in enumerate(tools):
        exe, models = base / t.exe, base / t.models
        if not (exe.exists() and models.exists()):
            log(f"downloading {t.name}")
            z = base / "dl" / Path(t.url).name
            download(t.url, t.sha256, z, lambda f, i=i: progress((i + f * 0.9) / len(tools)), cancelled)
            unpack(z, base / t.dest if t.dest else base)
            z.unlink(missing_ok=True)
        if not sys.platform.startswith("win"):
            exe.chmod(0o755)
        if not (exe.exists() and models.exists()):
            raise TexPackError(f"{t.name}: unexpected archive layout")
        out[t.name] = exe
        progress((i + 1) / len(tools))
    return out


# -- dump ---------------------------------------------------------------------------------------

def routes_dir() -> Path:
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here))
        if (base / "dump-routes").exists():
            return base / "dump-routes"
    return here.parent / "scripts" / "dump"


def texpack_dir() -> Path:
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here))
        if (base / "texpack").exists():
            return base / "texpack"
    return here.parent / "texpack"


def read_routes() -> List[Tuple[str, int]]:
    out = []
    for ln in (routes_dir() / "routes.txt").read_text().splitlines():
        p = ln.split("#")[0].split()
        if len(p) == 2:
            out.append((p[0], int(p[1])))
    return out


def _link_dir(src: Path, dst: Path) -> None:
    """A link to a folder: a symlink, or a junction on Windows (no privilege needed)."""
    if sys.platform.startswith("win"):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True, text=True)
        if r.returncode:
            raise TexPackError("mklink /J: " + (r.stderr or r.stdout).strip())
    else:
        os.symlink(src, dst)


EMPTY = Path("<empty save>")
PROFILE_FRAMES = 2700     # scripts/dump/profile.script: name entry, "Profile registered", save written


def has_save(ms: Path) -> bool:
    return any(ms.glob("PSP/SAVEDATA/*/SD.BIN"))


def scratch_data(data: Path, scratch: Path, ms: Optional[Path] = None) -> Path:
    """A throwaway data folder for the dump runs: the disc and the module linked, the save COPIED
    (from `ms`, default the player's own). The routes finish songs, which makes the game write the
    save: it must be a copy."""
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    _link_dir(data / "disc", scratch / "disc")
    _link_dir(data / "module", scratch / "module")
    ms = ms if ms is not None else data / "ms"
    if ms != EMPTY and ms.exists():
        shutil.copytree(ms, scratch / "ms")
    else:
        (scratch / "ms").mkdir()
    for extra in ("update",):                       # the 1.02 data update, when installed
        if (data / extra).exists():
            _link_dir(data / extra, scratch / extra)
    return scratch


def _run_game(game_exe: Path, scratch: Path, data: Path, cfg: dict, env_extra: dict, frames: int,
              timeout: int, cancelled: Callable[[], bool]) -> int:
    env = dict(os.environ)
    env.update({
        "EMOTIONALSENSE_CONFIRM": "circle", "EMOTIONALSENSE_POLL": "scene", "EMOTIONALSENSE_CLOCK": "virtual",
        "EMOTIONALSENSE_TEX_PACK": str(data / DISABLED_DIR),            # dump the originals
        "EMOTIONALSENSE_UPDATE": "1" if cfg.get("update", True) else "0",
        "EMOTIONALSENSE_CRASH": "0", "SDL_AUDIODRIVER": "dummy", "SDL_VIDEODRIVER": "dummy",
    })
    env.update(env_extra)
    env["LD_LIBRARY_PATH"] = str(game_exe.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    cmd = [str(game_exe), "--data", str(scratch), "--renderer", "vulkan", "--internal-scale", "1",
           "--frames", str(frames)]
    t0 = time.time()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while p.poll() is None:
        if cancelled():
            p.terminate()
            p.wait(10)
            raise Cancelled("cancelled")
        if time.time() - t0 > timeout:
            p.kill()
            break
        time.sleep(0.5)
    return p.returncode


def bootstrap_profile(data: Path, game_exe: Path, cfg: dict, out: Path,
                      cancelled: Callable[[], bool], log: Callable[[str], None]) -> Path:
    """The save the routes start from. With a save of the player's own, that is used. Without one the
    game asks for a player name on its first start, which would block every route: a throwaway
    profile is registered in a scratch folder (never in the player's data) and its `ms` is returned."""
    if has_save(data / "ms"):
        return data / "ms"
    prof = out / ".profile"
    if has_save(prof / "ms"):
        return prof / "ms"
    log("no save yet: registering a throwaway profile for the dump")
    scratch = scratch_data(data, out / ".profile-run", ms=EMPTY)
    try:
        _run_game(game_exe, scratch, data,  cfg, {"EMOTIONALSENSE_SCRIPT": "@" + str(routes_dir() / "profile.script")},
                  PROFILE_FRAMES, 300, cancelled)
        if not has_save(scratch / "ms"):
            raise TexPackError("the game did not register a profile (is there a Vulkan device?)")
        shutil.rmtree(prof, ignore_errors=True)
        shutil.copytree(scratch / "ms", prof / "ms")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return prof / "ms"


def dump_textures(data: Path, game_exe: Path, cfg: dict, out: Path,
                  progress: Callable[[float, int, int], None] = lambda f, i, n: None,
                  cancelled: Callable[[], bool] = lambda: False,
                  log: Callable[[str], None] = lambda s: None, timeout: int = 600) -> int:
    """Run every route; -> number of distinct textures in out/all. Finished routes are kept
    (`<route>/.done`), so a cancelled run continues where it stopped."""
    routes = read_routes()
    out.mkdir(parents=True, exist_ok=True)
    ms = bootstrap_profile(data, game_exe, cfg, out, cancelled, log)
    scratch = scratch_data(data, out / ".scratch", ms=ms)
    try:
        for i, (name, frames) in enumerate(routes):
            progress(i / len(routes), i, len(routes))
            rdir = out / name
            if (rdir / ".done").exists():
                continue
            if cancelled():
                raise Cancelled("cancelled")
            shutil.rmtree(rdir, ignore_errors=True)
            rdir.mkdir(parents=True)
            poke = ""
            pf = routes_dir() / (name + ".poke")
            if pf.exists():
                poke = pf.read_text().strip()
            log(f"route {name} ({frames} frames)")
            rc = _run_game(game_exe, scratch, data, cfg, {
                "EMOTIONALSENSE_POKE": poke, "EMOTIONALSENSE_TEX_DUMP": str(rdir),
                "EMOTIONALSENSE_SCRIPT": "@" + str(routes_dir() / (name + ".script"))}, frames, timeout, cancelled)
            if not any(rdir.glob("*.png")) and name != "boot":
                log(f"route {name}: no textures (exit {rc})")
            (rdir / ".done").write_text("ok\n")
        progress(1.0, len(routes), len(routes))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return merge_dump(out, [n for n, _ in routes])


def merge_dump(out: Path, names: List[str]) -> int:
    """The union of the routes' PNGs in out/all (one file per content key)."""
    allp = out / "all"
    allp.mkdir(exist_ok=True)
    n = 0
    for name in names:
        for f in sorted((out / name).glob("*.png")):
            dst = allp / f.name
            if not dst.exists():
                try:
                    os.link(f, dst)
                except OSError:
                    shutil.copy2(f, dst)
            n += 1
    return sum(1 for _ in allp.glob("*.png"))


# -- build --------------------------------------------------------------------------------------

def load_builder():
    """texpack/make_tex_pack.py as a module (it needs numpy and Pillow)."""
    import importlib.util
    d = texpack_dir()
    sys.path.insert(0, str(d))
    spec = importlib.util.spec_from_file_location("make_tex_pack", d / "make_tex_pack.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_tex_pack"] = mod
    spec.loader.exec_module(mod)
    return mod


def build_pack(data: Path, dump_all: Path, tools: Dict[str, Path],
               progress: Callable[[int, int], None] = lambda i, n: None,
               cancelled: Callable[[], bool] = lambda: False) -> int:
    mk = load_builder()
    out = pack_dir(data)
    out.mkdir(parents=True, exist_ok=True)
    (out / COMPLETE_MARKER).unlink(missing_ok=True)

    class A:                                         # what profile_pack reads from argparse
        pass
    a = A()
    a.dump, a.out, a.tmp = str(dump_all), str(out), str(data / "tmp")
    a.classes = mk.CLASSES_FILE
    a.w2x_tool, a.w2x_models = str(tools["waifu2x"]), mk.W2X_MODELS
    a.tool, a.ncnn_model = str(tools["realesrgan"]), mk.NCNN_MODEL
    a.fast_ncnn = True
    a.model, a.weights, a.providers, a.tile_max, a.fp16, a.device = "texturedat2", None, None, 512, "off", None
    a.progress, a.cancelled = progress, cancelled
    Path(a.tmp).mkdir(parents=True, exist_ok=True)
    names = sorted(n for n in os.listdir(a.dump) if n.endswith(".png"))
    todo = [n for n in names if not (out / n).exists()]
    st = mk.profile_pack(a, todo) if todo else {"done": 0, "failed": []}
    if cancelled():
        raise Cancelled("cancelled")
    if st["failed"]:
        raise TexPackError("failed: " + ", ".join(st["failed"][:5]))
    (out / COMPLETE_MARKER).write_text(json.dumps({"textures": len(names)}) + "\n")
    return len(names)


class Job:
    """tools -> dump -> build, with one progress fraction for the whole thing."""
    WEIGHT = {"tools": 0.05, "dump": 0.60, "build": 0.35}

    def __init__(self, data: Path, game_exe: Path, cfg: dict,
                 progress: Callable[[float], None] = lambda f: None,
                 step: Callable[[str], None] = lambda s: None,
                 cancelled: Callable[[], bool] = lambda: False,
                 log: Callable[[str], None] = lambda s: None):
        self.data, self.game_exe, self.cfg = data, game_exe, cfg
        self.progress, self.step, self.cancelled, self.log = progress, step, cancelled, log

    def _frac(self, name: str, f: float) -> None:
        base = 0.0
        for k, w in self.WEIGHT.items():
            if k == name:
                break
            base += w
        self.progress(base + self.WEIGHT[name] * max(0.0, min(1.0, f)))

    def run(self) -> int:
        self.step("tools")
        tools = ensure_tools(self.data, lambda f: self._frac("tools", f), self.cancelled, self.log)
        self.step("dump")
        dump_root = self.data / "textures-dump"
        n = dump_textures(self.data, self.game_exe, self.cfg, dump_root,
                          lambda f, i, t: self._frac("dump", f), self.cancelled, self.log)
        if n == 0:
            raise TexPackError("the game produced no textures (is there a Vulkan device?)")
        self.log(f"{n} textures collected")
        self.step("build")
        count = build_pack(self.data, dump_root / "all", tools,
                           lambda i, t: self._frac("build", i / max(t, 1)), self.cancelled)
        self.progress(1.0)
        return count
