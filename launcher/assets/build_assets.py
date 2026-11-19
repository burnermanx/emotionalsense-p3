#!/usr/bin/env python3
"""Build step for the launcher's look (run before PyInstaller; needs Pillow and fontTools).

    build_assets.py [--cache DIR]

1. downloads the OFL fonts pinned in fonts.json (sha256 checked) into the cache,
2. makes Exo 2 Italic (semi-bold) and Noto Sans JP/KR (medium) static, keeps
   only the characters used by launcher/locales/*.json plus ASCII (a few tens of
   kB instead of ~10 MB each),
3. renders the background, header and selection band PNGs from es_theme,
4. copies the font licences next to the fonts.

Output: launcher/assets/generated/ (git-ignored, regenerated in CI).
"""

import argparse
import hashlib
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fontTools import subset                     # noqa: E402
from fontTools.ttLib import TTFont               # noqa: E402
from fontTools.varLib import instancer           # noqa: E402


def fetch(cache: Path, spec: dict, commit: str) -> Path:
    dst = cache / spec["path"].replace("/", "_")
    if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest() == spec["sha256"]:
        return dst
    url = "https://raw.githubusercontent.com/google/fonts/%s/%s" % (
        commit, urllib.parse.quote(spec["path"]))
    cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise SystemExit(f"checksum mismatch for {spec['path']}")
    dst.write_bytes(data)
    return dst


def used_chars() -> str:
    chars = {chr(c) for c in range(0x20, 0x7F)}
    for f in (HERE.parent / "locales").glob("*.json"):
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            chars.update(v)
    import es_i18n
    for name in es_i18n.NAMES.values():
        chars.update(name)
    return "".join(sorted(chars))


def make_font(src: Path, dst: Path, weight: int, text: str):
    f = TTFont(src)
    f = instancer.instantiateVariableFont(f, {"wght": weight})
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    opts.name_IDs = ["*"]
    s = subset.Subsetter(opts)
    s.populate(text=text)
    s.subset(f)
    f.save(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=HERE / ".cache")
    a = ap.parse_args()
    spec = json.loads((HERE / "fonts.json").read_text())
    out = HERE / "generated"
    out.mkdir(exist_ok=True)
    files = {Path(f["path"]).parent.name + "/" + Path(f["path"]).name: fetch(a.cache, f, spec["commit"])
             for f in spec["files"]}
    text = used_chars()
    make_font(files["exo2/Exo2-Italic[wght].ttf"], out / "Exo2-Italic.ttf", 600, text)
    make_font(files["notosansjp/NotoSansJP[wght].ttf"], out / "NotoSansJP-sub.ttf", 500, text)
    make_font(files["notosanskr/NotoSansKR[wght].ttf"], out / "NotoSansKR-sub.ttf", 500, text)
    for name in ("exo2", "notosansjp", "notosanskr"):
        shutil.copy2(files[f"{name}/OFL.txt"], out / f"OFL-{name}.txt")
    import es_theme
    for k, im in es_theme.render_assets().items():
        im.save(out / f"{k}.png")
    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    print("assets:", json.dumps(sizes, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
