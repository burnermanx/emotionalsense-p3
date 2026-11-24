#!/usr/bin/env python3
"""Button-glyph catalogue (round 36, B; pills in 36b): finds the PlayStation button discs, and the pills
(SELECT, the shoulder buttons LB / RB) in a texture dump or in captures, by template matching. Nothing
from the game is stored: the templates are cut at run time from the textures that hold them (keys and
corners below: numbers only).

  find_glyphs.py catalog <dump/all>            print "key w h x y symbol score" for every disc found
  find_glyphs.py screens <dir-of-ppm> --dump <dump/all> [--min N]
                                               list the discs still visible in 480x272 captures
                                               (1x captures only: the match is at the game's scale)
  find_glyphs.py pills <dump/all>              print "key w h x y name score" for every pill (SELECT, L, R) in a dump:
                                               the pill's shape, whatever the word in it (rules of k_glyph_rules, 'E' 'l' 'r')
  find_glyphs.py pillscreens <dir-of-ppm> --dump <dump/all> [--min N]
                                               list the ORIGINAL words (SELECT, LB, RB) still visible in 1x captures

The dump is the one the texture-dump routes write (scripts/dump/routes.txt, EMOTIONALSENSE_TEX_DUMP).
Needs python3 with numpy and Pillow."""
import glob, os, sys
import numpy as np
from PIL import Image
from numpy.lib.stride_tricks import sliding_window_view as swv

# The pills: name, texture key, (x0, y0, x1, y1) of the whole pill, (x0, y0, x1, y1) of the word's box (the rules' text box)
PILLS = (("E", "a8fb042a8b1754db", (2, 2, 44, 15), (8, 4, 40, 11)),
         ("l", "3ddbb8a994b8c554", (6, 3, 38, 17), (20, 5, 35, 14)),
         ("r", "3ddbb8a994b8c554", (130, 3, 164, 17), (138, 5, 153, 14)))
STRIP = ("3e86e26d5d754018", 76, ((1, "o"), (23, "x"), (43, "s"), (64, "t")))   # key, y, (x, symbol)


def lum(a):
    return a[..., 0] * .3 + a[..., 1] * .59 + a[..., 2] * .11


def templates(dump):
    key, y, xs = STRIP
    im = np.asarray(Image.open(os.path.join(dump, key + ".png")).convert("RGBA")).astype(np.float32)
    return {n: lum(im[y:y + 18, x:x + 18]) * (im[y:y + 18, x:x + 18, 3] / 255) for x, n in xs}


def ncc(L, t):
    """Normalised cross-correlation of the template t (any size) over every position of L (FFT); None if L is smaller."""
    L = L.astype(np.float64)
    H, W = L.shape
    th, tw = t.shape
    if H < th or W < tw:
        return None
    n = t.size
    tc = (t - t.mean()).astype(np.float64)
    tn = np.sqrt((tc ** 2).sum())
    fh, fw = H + th - 1, W + tw - 1
    corr = np.fft.irfft2(np.fft.rfft2(L, (fh, fw)) * np.conj(np.fft.rfft2(tc, (fh, fw))), (fh, fw))[:H - th + 1, :W - tw + 1]
    c1 = np.pad(np.cumsum(np.cumsum(L, 0), 1), ((1, 0), (1, 0)))
    c2 = np.pad(np.cumsum(np.cumsum(L * L, 0), 1), ((1, 0), (1, 0)))

    def box(c):
        return c[th:, tw:] - c[:-th, tw:] - c[th:, :-tw] + c[:-th, :-tw]
    s1, s2 = box(c1), box(c2)
    var = np.maximum(s2 - s1 * s1 / n, 0)
    d = np.sqrt(var) * tn
    return np.where(var > 25.0 * n, corr / np.maximum(d, 1e-9), 0)


def pill_feat(a):
    """Shape and brightness of a texture: alpha and alpha-weighted luminance (the word in a pill is part of it)."""
    return a[..., 3] * 0.5 + lum(a) * (a[..., 3] / 255)


def pill_templates(dump, text_only):
    out = {}
    for name, key, whole, word in PILLS:
        im = np.asarray(Image.open(os.path.join(dump, key + ".png")).convert("RGBA")).astype(np.float32)
        x0, y0, x1, y1 = word if text_only else whole
        out[name] = (lum(im[y0:y1, x0:x1]) * (im[y0:y1, x0:x1, 3] / 255)) if text_only else pill_feat(im)[y0:y1, x0:x1]
    return out


def find(L, T, thr):
    M = {n: ncc(L, t) for n, t in T.items()}
    if any(m is None for m in M.values()):
        return []
    best = np.max(np.stack(list(M.values())), 0)
    out = []
    for y, x in zip(*np.where(best > thr)):
        win = best[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
        if best[y, x] < win.max():
            continue
        n = max(M, key=lambda q: M[q][y, x])
        out.append((int(x), int(y), n, float(M[n][y, x])))
    return out


def find_pills(img, T, thr):
    """Every match of each pill template: (x, y, name, score), the best of a 4x4 neighbourhood."""
    out = []
    for n, t in T.items():
        r = ncc(img, t)
        if r is None:
            continue
        for y, x in zip(*np.where(r > thr)):
            if r[y, x] >= r[max(0, y - 3):y + 4, max(0, x - 3):x + 4].max():
                out.append((int(x), int(y), n, float(r[y, x])))
    return out


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("catalog", "screens", "pills", "pillscreens"):
        sys.exit(__doc__)
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "pills":
        T = pill_templates(path, False)
        for f in sorted(glob.glob(os.path.join(path, "*.png"))):
            im = np.asarray(Image.open(f).convert("RGBA")).astype(np.float32)
            h, w = im.shape[:2]
            for x, y, n, sc in find_pills(pill_feat(im), T, 0.9):
                print(os.path.basename(f)[:-4], w, h, x, y, n, "%.3f" % sc)
    elif mode == "pillscreens":
        dump = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None
        T = pill_templates(dump, True)
        thr = float(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 0.9
        for f in sorted(glob.glob(os.path.join(path, "*.ppm"))):
            im = np.asarray(Image.open(f).convert("RGB")).astype(np.float32)
            if im.shape[:2] != (272, 480):
                continue
            for x, y, n, sc in find_pills(lum(im), T, thr):
                print(os.path.basename(f), x, y, n, "%.3f" % sc)
    elif mode == "catalog":
        T = templates(path)
        for f in sorted(glob.glob(os.path.join(path, "*.png"))):
            im = np.asarray(Image.open(f).convert("RGBA")).astype(np.float32)
            h, w = im.shape[:2]
            if w < 18 or h < 18:
                continue
            for x, y, n, s in find(lum(im) * (im[..., 3] / 255), T, 0.72):
                print(os.path.basename(f)[:-4], w, h, x, y, n, "%.3f" % s)
    else:
        dump = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None
        T = templates(dump)
        thr = float(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 0.9
        for f in sorted(glob.glob(os.path.join(path, "*.ppm"))):
            im = np.asarray(Image.open(f).convert("RGB")).astype(np.float32)
            if im.shape[:2] != (272, 480):
                continue
            for x, y, n, s in find(lum(im), T, thr):
                print(os.path.basename(f), x, y, n, "%.3f" % s)


if __name__ == "__main__":
    main()
