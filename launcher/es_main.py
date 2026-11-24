#!/usr/bin/env python3
"""EmotionalSense launcher: window by default, command line for scripts and CI.

    EmotionalSense-P3                         window (Tk)
    EmotionalSense-P3 --iso game.iso --prepare    prepare, no window
    EmotionalSense-P3 --run [-- game args]    run the prepared game
    EmotionalSense-P3 --selftest              build and run the runtime test
    options: --data DIR  --kit DIR  --lang auto|en|pt-BR|es|ja|ko  --jobs N
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import es_i18n  # noqa: E402
from es_i18n import t  # noqa: E402

CONFIG_DEFAULTS = {
    "language": "auto",
    "scale": "auto",          # internal resolution: auto, 1, 2, 3, 4
    "unlock": True,
    "unlock_items": True,
    "key_trim": "default",    # default or frames
    "texture_pack": "",       # a folder chosen by hand (launcher.json only); wins over "texpack"
    "texpack": "off",         # AI high-resolution textures built by the launcher (es_texpack): off or on
    "renderer": "auto",       # auto, vulkan, software
    "confirm": "auto",        # auto (by language), circle, cross
    "refresh": "native",      # full screen: native (borderless, desktop rate), auto (60, else 120 exclusive), 60, 120
    "fever": "respect",       # Fever mode: respect (automatic, lane speed kept) or original (PSP)
    "score": "respect",       # scoring display: respect (0..1,000,000 from the judgment %) or original (PSP)
    "speed": "respect",       # speed system: respect (Auto Velocity, steps of 0.1, 1.0x-7.0x) or original (PSP, 0.5x-7.0x)
    "button_style": "auto",   # button symbols: auto (follows the controller), playstation, xbox, steamdeck, switch
    "triggers_2t": "stick",   # LT/RT in 3.2T, 4.2T and 6.2T songs: stick (the side notes) or lr (L/R, the speed)
    "start_mode": "last",     # start in: last (remembered by the game), window, fullscreen (borderless)
    "bga_smooth": "0",        # background video: 0 (as the game draws it) or blend
    "update": True,           # use <data>/update/DATA.TPA when it exists
}

REFRESH_VALUES = ("native", "auto", "60", "120")
FEVER_VALUES = ("respect", "original")
SCORE_VALUES = ("respect", "original")
SPEED_VALUES = ("respect", "original")
BUTTON_STYLE_VALUES = ("auto", "playstation", "xbox", "steamdeck", "switch")
TRIGGERS_2T_VALUES = ("stick", "lr")
START_MODE_VALUES = ("last", "window", "fullscreen")
BGA_VALUES = ("0", "blend")

CONFIRM_VALUES = ("auto", "circle", "cross")
CIRCLE_LANGS = ("ja", "ko")   # Asian languages confirm with circle; every other language with cross


def resolve_confirm(value: str, lang: str) -> str:
    """'auto' follows the launcher language: ja and ko -> circle, anything else
    (pt-BR, en, es and any language added later) -> cross."""
    if value in ("circle", "cross"):
        return value
    return "circle" if lang in CIRCLE_LANGS else "cross"


def load_config(data: Path) -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        cfg.update({k: v for k, v in json.loads((data / "launcher.json").read_text()).items()
                    if k in CONFIG_DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(data: Path, cfg: dict) -> None:
    data.mkdir(parents=True, exist_ok=True)
    (data / "launcher.json").write_text(json.dumps(cfg, indent=1))


# The sync-collection mode (`--sync-log`, or EMOTIONALSENSE_SYNC_LOG=1 in the environment, which is the
# way to turn it on from Steam's launch options): the game's diagnostic log with the clock, vblank and audio
# traces, written to <data>/logs/sync-<time>.log. Only cheap traces: the per-frame picture hash of
# TRACE_FPS would load a small CPU like the Deck's and disturb what is being measured.
SYNC_ENV = {"EMOTIONALSENSE_LOG": "1", "EMOTIONALSENSE_TRACE_CLOCK": "1", "EMOTIONALSENSE_TRACE_AUDIO": "1"}


def sync_log_wanted(flag: bool = False) -> bool:
    v = os.environ.get("EMOTIONALSENSE_SYNC_LOG", "")
    return bool(flag or (v and v != "0"))


def sync_header(data: Path, cmd, env: dict) -> str:
    """What a reader needs to interpret the log, with no personal data (paths are scrubbed later)."""
    import platform
    keys = ("SteamDeck", "SteamOS", "SteamGameId", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY",
            "DISPLAY", "ENABLE_GAMESCOPE_WSI", "GAMESCOPE_WAYLAND_DISPLAY", "SDL_AUDIODRIVER", "SDL_VIDEODRIVER",
            "PULSE_LATENCY_MSEC", "PIPEWIRE_LATENCY", "APPIMAGE", "LD_PRELOAD", "DXVK_FRAME_RATE")
    cpu = ""
    try:
        for ln in Path("/proc/cpuinfo").read_text().splitlines():
            if ln.startswith("model name"):
                cpu = ln.split(":", 1)[1].strip()
                break
    except OSError:
        cpu = platform.processor()
    try:
        ver = json.loads((Path(cmd[0]).parent.parent / "manifest.json").read_text()).get("version_full", "?")
    except (OSError, ValueError, IndexError):
        ver = "?"
    lines = ["# EmotionalSense-P3 sync log", f"# time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"# platform: {platform.platform()} cpu: {cpu} cores: {os.cpu_count()}",
             f"# argv: {' '.join(str(c) for c in cmd[1:])}"]
    lines += [f"# env {k}={env[k]}" for k in keys if k in env]
    return "\n".join(lines) + "\n"


def spawn_game(cmd, env: dict, data: Path, sync_log: bool = False):
    """Start the game. With the sync log the game's output goes to <data>/logs/sync-<time>.log
    (never to the terminal); finish_game() scrubs the user's name out of it afterwards."""
    if not sync_log_wanted(sync_log):
        return subprocess.Popen(cmd, env=env)
    env = dict(env)
    env.update(SYNC_ENV)
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / time.strftime("sync-%Y%m%d-%H%M%S.log")
    f = open(path, "w", encoding="utf-8")
    f.write(sync_header(data, cmd, env))
    f.flush()
    p = subprocess.Popen(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
    f.close()
    p.sync_log = path                                   # type: ignore[attr-defined]
    return p


def finish_game(p) -> None:
    """After the game ended: scrub the sync log, if there is one."""
    path = getattr(p, "sync_log", None)
    if path:
        try:
            path.write_text(scrub_paths(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
            print(f"sync log: {path}", file=sys.stderr)
        except OSError:
            pass


def game_command(exe: Path, data: Path, cfg: dict, extra=(), confirm: str = "", verbose: bool = False) -> tuple:
    """(argv, env) for the prepared game, from the launcher options. `confirm`
    overrides the option (circle, cross or auto). `verbose` turns the game's diagnostic log on
    (it prints nothing by default)."""
    import es_env
    env = es_env.game_env()
    env["EMOTIONALSENSE_DATA"] = str(data)
    if verbose:
        env["EMOTIONALSENSE_LOG"] = "1"
    # The system's own environment first (never the launcher's `_internal`, see es_env), then
    # what the game ships with: its folder (SDL2/FFmpeg of the kit).
    env["LD_LIBRARY_PATH"] = os.pathsep.join(d for d in (str(exe.parent), env.get("LD_LIBRARY_PATH", "")) if d)
    argv = [str(exe), "--data", str(data)]
    if cfg.get("scale", "auto") != "auto":
        argv += ["--internal-scale", str(cfg["scale"])]
    if cfg.get("renderer", "auto") != "auto":
        argv += ["--renderer", cfg["renderer"]]
    mode = resolve_confirm(confirm or cfg.get("confirm", "auto"), es_i18n.language())
    argv += ["--confirm", mode]
    env["EMOTIONALSENSE_CONFIRM"] = mode
    # No rate chosen ("native"): the game's full screen is a borderless window.
    if cfg.get("refresh", "native") != "native":
        env["EMOTIONALSENSE_REFRESH"] = str(cfg["refresh"])
    fever = cfg.get("fever", "respect")
    env["EMOTIONALSENSE_FEVER"] = fever if fever in FEVER_VALUES else "respect"
    score = cfg.get("score", "respect")
    env["EMOTIONALSENSE_SCORE"] = score if score in SCORE_VALUES else "respect"
    speed = cfg.get("speed", "respect")
    env["EMOTIONALSENSE_SPEED"] = speed if speed in SPEED_VALUES else "respect"
    style = cfg.get("button_style", "auto")
    env["EMOTIONALSENSE_BUTTONS"] = style if style in BUTTON_STYLE_VALUES else "auto"
    trig = cfg.get("triggers_2t", "stick")
    env["EMOTIONALSENSE_TRIGGERS_2T"] = trig if trig in TRIGGERS_2T_VALUES else "stick"
    env["EMOTIONALSENSE_FULLSCREEN"] = {"window": "0", "fullscreen": "1"}.get(str(cfg.get("start_mode", "last")), "auto")
    # The game's own prompts (exit confirmation) follow the launcher language.
    env["EMOTIONALSENSE_LANG"] = es_i18n.language()
    env["EMOTIONALSENSE_BGA_SMOOTH"] = "blend" if str(cfg.get("bga_smooth", "0")) == "blend" else "0"
    env["EMOTIONALSENSE_UPDATE"] = "1" if cfg.get("update", True) else "0"
    env["EMOTIONALSENSE_UNLOCK"] = "1" if cfg.get("unlock", True) else "0"
    env["EMOTIONALSENSE_UNLOCK_ITEMS"] = "1" if cfg.get("unlock_items", True) else "0"
    if cfg.get("key_trim", "default") != "default":
        env["EMOTIONALSENSE_KEY_TRIM"] = str(cfg["key_trim"])
    import es_texpack
    env.update(es_texpack.pack_env(data, cfg))
    return argv + list(extra), env


ISSUES_URL = "https://github.com/burnermanx/emotionalsense-p3/issues/new"


def crash_reports(data: Path) -> list:
    """The crash/hang reports the game wrote, oldest first."""
    return sorted((data / "crashes").glob("crash-*.txt"))


_USER_DIR = re.compile(r"(?i)((?:^|[\\/])(?:home|users)[\\/])[^\\/]+")


def scrub_paths(text: str) -> str:
    """Hide the user name in paths (/home/<n>, /Users/<n>, C:\\Users\\<n>, Z:/home/<n>) and the
    home folder itself, so nothing personal reaches a public issue. Same rule as the game's
    crash reports."""
    home = str(Path.home())
    if len(home) >= 3:
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return _USER_DIR.sub(lambda m: m.group(1).replace("\\", "/") + "~", text)


def crash_summary(path: Path) -> dict:
    """The header fields of a report (kind, version, os, renderer, gpu, scene...)."""
    info: dict = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("[") or not line.strip():
                if info:
                    break
                continue
            k, _, v = line.partition(": ")
            if v and k not in info:
                info[k] = scrub_paths(v)
    except OSError:
        pass
    return info


def issue_url(path: Path) -> str:
    """A new-issue URL (crash.yml form) with a short summary filled in. The report itself is
    attached by the user; nothing is sent from here."""
    from urllib.parse import quote
    i = crash_summary(path)
    kind = i.get("kind", "crash")
    summary = (f"Kind: {kind} ({i.get('detail', '')})\nVersion: {i.get('version', '?')}\n"
               f"OS: {i.get('os', '?')}\nRenderer: {i.get('renderer', '?')}\nGPU: {i.get('gpu', '?')}\n"
               f"Report file: {path.name} (attached below)")
    return (f"{ISSUES_URL}?template=crash.yml&title={quote('[crash] ' + kind + ' ' + i.get('version', ''))}"
            f"&summary={quote(summary)}")


def renderer_report(data: Path, since: float = 0.0):
    """After a game run: (path, result) when the game wrote <data>/logs/renderer.log in this run
    (newer than `since`) and Vulkan did not start -- the game ran the software renderer (or, with
    --renderer vulkan, did not run); else None."""
    path = data / "logs" / "renderer.log"
    try:
        if path.stat().st_mtime < since - 2:
            return None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]:
            if line.startswith("result: "):
                result = line[8:].strip()
                return (path, result) if result.startswith(("software-fallback", "failed")) else None
    except OSError:
        pass
    return None


def renderer_issue_url(path: Path, result: str) -> str:
    """A new-issue URL for a Vulkan that did not start (no form: a plain title and summary). The
    report is attached by the user; nothing is sent from here."""
    from urllib.parse import quote
    info = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:6]:
            k, _, v = line.partition(": ")
            if v:
                info[k] = scrub_paths(v)
    except OSError:
        pass
    summary = (f"Vulkan did not start ({result}).\nVersion: {info.get('version', '?')}\n"
               f"Report file: {path.name} (attach it below)\n")
    return f"{ISSUES_URL}?title={quote('[vulkan] did not start ' + info.get('version', ''))}&body={quote(summary)}"


def backup_save(data: Path) -> Path:
    import es_prepare
    src = es_prepare.save_file(data)
    if not src.exists():
        raise FileNotFoundError(t("main.backup_none"))
    dst_dir = data / "save-backups"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"SD-{time.strftime('%Y%m%d-%H%M%S')}.BIN"
    shutil.copy2(src, dst)
    # The Respect records (<data>/es_scores.json) are part of the save: back them up together.
    scores = data / "es_scores.json"
    if scores.exists():
        shutil.copy2(scores, dst_dir / (dst.stem.replace("SD-", "ES-scores-") + ".json"))
    return dst


def _show_error(msg: str) -> None:
    """A window-less launch has no console: show the message in a dialog."""
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(t("app.title"), msg)
        root.destroy()
    except Exception:                                # noqa: BLE001
        pass


class ProgressLines:
    """`--progress-lines`: the preparation as `progress <0..1> <step>` lines on stdout, the protocol of
    RecompLauncher's window-less setup step. A line is printed when the step changes or the fraction
    moves by half a percent, so a long compile still prints now and then without flooding."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.name = ""
        self.last = -1.0

    def step(self, name: str) -> None:
        if name != self.name:
            self.name = name
            self.last = -1.0

    def progress(self, f: float, _what: str = "") -> None:
        f = max(0.0, min(1.0, f))
        if f - self.last >= 0.005 or f >= 1.0 > self.last:
            self.emit(f, t("prepare.step." + self.name) if self.name else "")

    def emit(self, f: float, text: str) -> None:
        self.last = f
        print(f"progress {f:.3f} {text}".rstrip(), file=self.out, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="EmotionalSense-P3", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", type=Path, help="the game image: an .iso, .chd (chdman createdvd/createcd) or .cso, or a folder that holds the extracted PSP_GAME")
    ap.add_argument("--prepare", action="store_true", help="prepare the game (needs --iso the first time)")
    ap.add_argument("--run", action="store_true", help="run the prepared game")
    ap.add_argument("--backup-save", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gui-smoke", action="store_true", help="open the window briefly, press keys, exit")
    ap.add_argument("--data", type=Path)
    ap.add_argument("--kit", type=Path)
    ap.add_argument("--lang", default=None)
    ap.add_argument("--confirm", choices=CONFIRM_VALUES, default="",
                    help="which button confirms in the game's menus (default: the saved option; auto follows the language)")
    ap.add_argument("--update", type=Path, metavar="FOLDER",
                    help="install the optional 1.02 data update (the folder that holds PSP/GAME/ULKS46236/DATA.TPA)")
    ap.add_argument("--remove-update", action="store_true", help="remove the installed data update")
    ap.add_argument("--build-textures", action="store_true", help=argparse.SUPPRESS)  # experimental, needs ES_TEXPACK_PROTO=1
    ap.add_argument("--migrate-legacy", action="store_true",
                    help="copy (never move) data of older versions from their old folders into the data folder")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print progress and the game's diagnostic log (nothing is printed by default but errors)")
    ap.add_argument("--sync-log", action="store_true",
                    help="with --run: write the clock/vblank/audio diagnostic log to <data>/logs/sync-<time>.log "
                         "(also on when EMOTIONALSENSE_SYNC_LOG=1)")
    ap.add_argument("--progress-lines", action="store_true",
                    help="with --prepare: print 'progress <0..1> <step>' lines on stdout for a launcher that runs "
                         "the preparation without a window (RecompLauncher); an already prepared game prints "
                         "'progress 1 ...' and exits at once")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("game_args", nargs="*")
    a = ap.parse_args(argv)
    vlog = (lambda s: print(s, file=sys.stderr)) if a.verbose else (lambda s: None)

    import es_prepare
    import es_portable
    data = a.data or es_prepare.user_data_dir()
    from es_pipeline import refuse_real_data
    refuse_real_data(data)
    cfg = load_config(data)
    es_i18n.set_language(a.lang or cfg["language"])
    if not a.version:
        try:
            es_portable.check_writable(data)
            es_portable.apply_env(data)
        except es_portable.PortableError as e:
            print(str(e), file=sys.stderr)
            if not (a.iso or a.prepare or a.run or a.backup_save or a.selftest or a.update or a.remove_update
                    or a.migrate_legacy):
                _show_error(str(e))
            return 1
    if a.migrate_legacy:
        old = es_portable.find_legacy(data)
        if not old:
            print(t("portable.migrate_none"))
            return 0
        n = es_portable.migrate(old, data, log=vlog)
        print(t("portable.migrated", count=n, path=old))
        return 0
    kit = a.kit or es_prepare.default_kit()
    import es_chd
    es_chd.configure(kit)

    if a.version:
        try:
            print("EmotionalSense-P3", json.loads((kit / "manifest.json").read_text())["version_full"])
        except OSError:
            import es_version
            print("EmotionalSense-P3", es_version.VERSION)
        return 0
    if a.selftest:
        try:
            es_prepare.selftest(kit, a.jobs)
        except Exception as e:                       # noqa: BLE001
            print(t("cli.selftest_fail", error=str(e)), file=sys.stderr)
            return 1
        print(t("cli.selftest_ok"))
        return 0
    if a.backup_save:
        try:
            print(t("cli.save_backup", path=backup_save(data)))
            return 0
        except OSError as e:
            print(t("main.backup_failed", error=str(e)), file=sys.stderr)
            return 1
    if a.build_textures:
        import es_texpack
        if not es_texpack.ENABLED:
            print("--build-textures is experimental: set ES_TEXPACK_PROTO=1", file=sys.stderr)
            return 2
        prep = es_prepare.Preparer(es_prepare.PrepareOptions(iso=Path("."), data_dir=data, kit_dir=kit))
        if not prep.is_prepared():
            print(t("texpack.not_ready"), file=sys.stderr)
            return 2
        if not es_texpack.platform_key():
            print(t("texpack.unsupported", arch=es_texpack.arch_name()), file=sys.stderr)
            return 1
        if not es_texpack.vulkan_present():
            print(t("texpack.no_vulkan"), file=sys.stderr)
            return 1
        job = es_texpack.Job(data, prep.game_exe(), cfg,
                             progress=lambda f: print(f"\r{f * 100:5.1f}%", end="", file=sys.stderr, flush=True),
                             step=lambda s: print("\n" + t("texpack.step." + s), file=sys.stderr),
                             log=lambda s: print(s, file=sys.stderr))
        try:
            n = job.run()
        except es_texpack.Cancelled:
            print("\n" + t("texpack.cancelled"), file=sys.stderr)
            return 1
        except Exception as e:                        # noqa: BLE001
            print("\n" + t("texpack.failed", error=str(e)), file=sys.stderr)
            return 1
        cfg["texpack"] = "on"
        save_config(data, cfg)
        print("\n" + t("texpack.done", count=n))
        return 0
    if a.update or a.remove_update:
        from es_pipeline import PipelineError
        if a.remove_update:
            es_prepare.remove_update(data)
            print(t("update.removed"))
            return 0
        try:
            n = es_prepare.install_update(data, a.update)
        except PipelineError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(t("update.installed", count=n))
        return 0
    if a.prepare or a.run:
        lines = ProgressLines() if a.progress_lines and a.prepare else None
        prep = es_prepare.Preparer(es_prepare.PrepareOptions(
            iso=a.iso or Path("."), data_dir=data, kit_dir=kit, jobs=a.jobs, force=a.force),
            log=vlog,
            progress=lines.progress if lines else
            (lambda f, s: print(f"\r{f * 100:5.1f}%", end="", file=sys.stderr, flush=True)) if a.verbose
            else (lambda f, s: None),
            step=lines.step if lines else (lambda s: None))
        if a.prepare:
            if lines and prep.is_prepared():
                lines.emit(1.0, t("prepare.step.done"))
                return 0
            if not a.iso:
                if prep.is_prepared():
                    vlog(t("cli.prepared", path=prep.game_exe()))
                    return 0
                if es_prepare.data_ready(data):          # the disc is already extracted here: no image needed
                    prep.o.iso = None
                else:
                    import es_portable
                    folder = es_prepare.find_game_folder(es_portable.app_dir(), skip=(es_portable.DATA_NAME,))
                    if folder is None:
                        print(t("cli.iso_required"), file=sys.stderr)
                        return 2
                    prep.o.iso = folder                  # an extracted game folder next to the program (copied)
            t0 = time.time()
            try:
                exe = prep.run()
            except Exception as e:                   # noqa: BLE001
                print(("\n" if a.verbose else "") + t("prepare.failed", error=str(e)), file=sys.stderr)
                return 1
            vlog(f"\n{t('cli.prepared', path=exe)} ({time.time() - t0:.0f} s)")
        if a.run:
            if not prep.is_prepared():
                print(t("cli.not_prepared"), file=sys.stderr)
                return 2
            cmd, env = game_command(prep.game_exe(), data, cfg, a.game_args, a.confirm, a.verbose)
            t_start = time.time()
            p = spawn_game(cmd, env, data, a.sync_log)
            code = p.wait()
            finish_game(p)
            found = renderer_report(data, t_start)
            if found:
                print(f"{t('renderer.title')}: {scrub_paths(str(found[0]))}", file=sys.stderr)
            return code
        return 0
    import es_gui
    if a.gui_smoke:
        return es_gui.smoke(data, kit)
    return es_gui.run_gui(data, kit, a.jobs)


if __name__ == "__main__":
    sys.exit(main())
