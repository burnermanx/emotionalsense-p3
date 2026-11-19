#!/usr/bin/env python3
"""Build the distributable launcher: PyInstaller onedir + the kit beside it.

    build_launcher.py --kit <kit dir> --out <dist dir> --target linux-x86_64 [--appimage] [--archive]

1. launcher/assets/build_assets.py   (fonts subset, theme PNGs)
2. PyInstaller --onedir              (EmotionalSense / EmotionalSense.exe)
3. the kit goes next to the executable as `kit/`, the licences as `licenses/`
4. --appimage (Linux): wraps the onedir in an AppImage with appimagetool.
5. --archive: EmotionalSense-P3-<version>-<target>.tar.gz (Linux) or .zip (Windows) of the onedir.

Targets: linux-x86_64, linux-aarch64, windows-x86_64, windows-arm64. PyInstaller does not
cross-build: run this on a machine of the target architecture.

Run it with a Python that has pyinstaller and pillow (and fonttools for step 1).
The result contains no game data; scripts/release_guard.py checks that.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L = ROOT / "launcher"
NAME = "EmotionalSense-P3"           # onedir folder and launcher executable (.exe on Windows)
DISPLAY = "EmotionalSense-P3"        # the product name users see


def project_version() -> str:
    import re
    m = re.search(r"project\(emotionalsense\s+VERSION\s+([0-9.]+)", (ROOT / "CMakeLists.txt").read_text())
    return m.group(1) if m else "0"


def sh(*a, **k):
    print("$", " ".join(str(x) for x in a), flush=True)
    subprocess.run([str(x) for x in a], check=True, **k)


def make_icon(dst: Path):
    """The application icon (launcher/es_icon.py): original, drawn by code."""
    sys.path.insert(0, str(L))
    import es_icon
    es_icon.render(256).save(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", default="linux-x86_64" if os.name != "nt" else "windows-x86_64",
                    choices=["linux-x86_64", "linux-aarch64", "windows-x86_64", "windows-arm64"])
    ap.add_argument("--appimage", action="store_true")
    ap.add_argument("--archive", action="store_true", help="also write the .tar.gz (Linux) / .zip (Windows)")
    ap.add_argument("--appimagetool", type=Path)
    ap.add_argument("--cache", type=Path, default=L / "assets" / ".cache")
    a = ap.parse_args()
    out = a.out.resolve()
    work = out.parent / (out.name + "-work")
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    out.mkdir(parents=True)

    sh(sys.executable, L / "assets" / "build_assets.py", "--cache", a.cache)
    icons = work / "icons"
    sys.path.insert(0, str(L))
    import es_icon
    es_icon.write_all(icons)
    gen = L / "assets" / "generated"
    sep = ";" if os.name == "nt" else ":"
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", NAME,
       "--distpath", work / "dist", "--workpath", work / "build", "--specpath", work,
       "--paths", L, "--add-data", f"{L / 'locales'}{sep}locales",
       "--add-data", f"{gen}{sep}assets/generated",
       "--add-data", f"{icons / 'icon-256.png'}{sep}.",
       "--hidden-import", "es_gui", "--hidden-import", "es_prepare", "--hidden-import", "es_build",
       "--hidden-import", "es_portable", "--hidden-import", "es_texpack",
       "--hidden-import", "tkinter.filedialog", "--hidden-import", "es_icon", "--hidden-import", "es_pad", "--hidden-import", "es_chd", "--hidden-import", "es_cso", "--hidden-import", "es_env",
       "--icon", icons / "icon.ico",
       L / "es_main.py")
    app = work / "dist" / NAME
    shutil.copytree(app, out / NAME)
    root = out / NAME
    shutil.copytree(a.kit, root / "kit", symlinks=True)
    lic = root / "licenses"
    lic.mkdir()
    for f in gen.glob("OFL-*.txt"):
        shutil.copy2(f, lic / f.name)
    for f in ("LICENSE",):
        shutil.copy2(ROOT / f, lic / f"{NAME}-{f}.txt")
    nt = ROOT / "THIRD_PARTY_NOTICES.md"
    if nt.exists():
        shutil.copy2(nt, root / "THIRD_PARTY_NOTICES.md")
    for p in (ROOT / "licenses").glob("*") if (ROOT / "licenses").exists() else []:
        shutil.copy2(p, lic / p.name)
    print(f"onedir: {root}")

    if a.appimage:
        appdir = work / "AppDir"
        (appdir / "usr").mkdir(parents=True)
        shutil.copytree(root, appdir / "usr" / "lib" / NAME, symlinks=True)
        (appdir / "AppRun").write_text(
            '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
            f'exec "$HERE/usr/lib/{NAME}/{NAME}" "$@"\n')
        (appdir / "AppRun").chmod(0o755)
        (appdir / "EmotionalSense-P3.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=EmotionalSense-P3\nComment=Launcher for DJMAX Portable 3, recompiled\n"
            f"Exec={NAME}\nIcon=EmotionalSense-P3\nCategories=Game;\nTerminal=false\n")
        make_icon(appdir / "EmotionalSense-P3.png")
        tool = a.appimagetool or Path(shutil.which("appimagetool") or "")
        if not tool or not tool.exists():
            sys.exit("appimagetool not found (--appimagetool)")
        arch = a.target.split("-", 1)[1]
        img = out / f"{DISPLAY}-{project_version()}-{a.target}.AppImage"
        env = dict(os.environ, ARCH=arch, APPIMAGE_EXTRACT_AND_RUN="1")
        sh(tool, "--no-appstream", appdir, img, env=env)
        print(f"AppImage: {img} ({img.stat().st_size >> 20} MiB)")
    if a.archive:
        import tarfile
        import zipfile
        base = f"{DISPLAY}-{project_version()}-{a.target}"
        if a.target.startswith("windows"):
            with zipfile.ZipFile(out / (base + ".zip"), "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(root.rglob("*")):
                    z.write(f, f.relative_to(out).as_posix())
            print(f"archive: {out / (base + '.zip')}")
        else:
            with tarfile.open(out / (base + ".tar.gz"), "w:gz") as tf:
                tf.add(root, arcname=NAME)          # keeps the executable bits and the symlinks
            print(f"archive: {out / (base + '.tar.gz')}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
