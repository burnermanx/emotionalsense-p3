#!/usr/bin/env python3
"""Classify dumped textures into track / interface / illustration (T5).

    texpack/classify_textures.py <dump_dir>... [--only <pack>] [--overrides texpack/tex_class_overrides.txt]
                             [--out texpack/tex_classes.txt] [--sheets <dir>]

Every <dump_dir> is an EMOTIONALSENSE_TEX_DUMP output (or a merged one): the
`<key>.png` files and any `index.txt` (`key fmt w h scene [route]` or
`key addr fmt w h scene`), directly in it or in its sub-directories.

Classes
  track         the track and its HUD: notes, gear, combo, judgments, bars, fever, the
                pause menu -- everything that was written while the gameplay scene (3)
                was current
  illustration  covers and discs, eyecatch pictures, character icons, backgrounds and
                photographs: either a 128x128 disc cover (round alpha mask, many
                colours) or an opaque picture of at least 256x128 with many colours
  interface     the rest: panels, buttons, fonts, icons, logos, result effects

The heuristic is a starting point; <overrides> lists the exceptions as
`<key> <class>` lines (hashes only, no pictures). <out> gets `<key> <class>` for
every texture, sorted, again only keys. Nothing derived from the game is written
anywhere but where you point --sheets.
"""
import argparse
import collections
import glob
import os

import numpy as np
from PIL import Image

CLASSES = ("track", "interface", "illustration")


def read_indexes(dirs):
    scenes = collections.defaultdict(set)
    for d in dirs:
        for idx in glob.glob(os.path.join(d, "index.txt")) + glob.glob(os.path.join(d, "*", "index.txt")):
            for ln in open(idx):
                p = ln.split()
                if ln.startswith("#") or len(p) < 5:
                    continue
                try:
                    scene = int(p[5] if len(p[1]) >= 6 and len(p) >= 6 else p[4])
                except ValueError:
                    continue
                scenes[p[0]].add(scene)
    return scenes


def features(path):
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im).astype(np.float32)
    al = a[..., 3] / 255
    vis = al > 0.05
    uc = len(np.unique((a[..., :3][vis] // 8).reshape(-1, 3), axis=0)) if vis.any() else 0
    return im.width, im.height, uc, float((al < 0.95).mean())


def classify(scenes, w, h, uc, transparent):
    if 3 in scenes:
        return "track"
    if w == 128 and h == 128 and uc >= 400 and 0.1 < transparent < 0.35:
        return "illustration"
    if transparent < 0.08 and w >= 256 and h >= 128 and uc >= 800:
        return "illustration"
    return "interface"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", nargs="+")
    ap.add_argument("--overrides", default=os.path.join(os.path.dirname(__file__), "tex_class_overrides.txt"))
    ap.add_argument("--only", help="classify only the keys that have a <key>.png in this directory (e.g. the pack)")
    ap.add_argument("--out")
    ap.add_argument("--sheets", help="write one contact sheet per class here (stays outside the repo)")
    a = ap.parse_args()
    scenes = read_indexes(a.dumps)
    pngs = {}
    for d in a.dumps:
        for f in glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*", "*.png")):
            pngs.setdefault(os.path.basename(f)[:-4], f)
    if a.only:
        keep = {f[:-4] for f in os.listdir(a.only) if f.endswith(".png")}
        pngs = {k: v for k, v in pngs.items() if k in keep}
    over = {}
    if os.path.exists(a.overrides):
        for ln in open(a.overrides):
            p = ln.split("#")[0].split()
            if len(p) == 2 and p[1] in CLASSES:
                over[p[0]] = p[1]
    res = {}
    for k, f in sorted(pngs.items()):
        res[k] = over.get(k) or classify(scenes.get(k, set()), *features(f))
    counts = collections.Counter(res.values())
    print("textures: %d   " % len(res) + "   ".join("%s %d" % (c, counts[c]) for c in CLASSES)
          + "   (overrides applied: %d)" % sum(1 for k in res if k in over))
    if a.out:
        with open(a.out, "w") as f:
            f.write("# texture class by key (texpack/classify_textures.py); keys only\n")
            for k, c in sorted(res.items()):
                f.write("%s %s\n" % (k, c))
    if a.sheets:
        os.makedirs(a.sheets, exist_ok=True)
        from PIL import ImageDraw
        for c in CLASSES:
            keys = [k for k, v in res.items() if v == c]
            T, cols = 120, 12
            n = (len(keys) + cols - 1) // cols
            W = Image.new("RGB", (cols * (T + 4), max(1, n) * (T + 4)), (50, 50, 60))
            for i, k in enumerate(keys):
                im = Image.open(pngs[k]).convert("RGBA")
                bg = Image.new("RGBA", im.size, (25, 25, 35, 255))
                bg.alpha_composite(im)
                s = min(T / im.width, T / im.height)
                im = bg.convert("RGB").resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
                W.paste(im, ((i % cols) * (T + 4) + 2, (i // cols) * (T + 4) + 2))
            W.save(os.path.join(a.sheets, "class_%s.png" % c))


if __name__ == "__main__":
    main()
