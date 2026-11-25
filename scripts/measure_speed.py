#!/usr/bin/env python3
"""Measure how fast the lane's beat lines fall in captured frames (frame_<vblank>.ppm), in pixels of the
480x272 picture per vblank. Used by scripts/check_scripts.sh to compare two songs / two speed systems:

    measure_speed.py <dir> <first_vb> <last_vb> <step>

The measure lines (one thin cyan line across the lane per bar) are found in the lane above the judgment
box in every captured frame; each line is followed to the next frame (a short step, so it is the same
line) and the slope of each line's path is fitted. Prints `px_per_vblank <value> samples <n> ...`. Only PIL is needed."""
import sys
from pathlib import Path
from PIL import Image

LANE_X = (180, 300)        # inside the lane borders, 480x272 units
TOP, BOTTOM = 4, 120       # above the judgment box (y ~ 126..132)


def lines(path: Path):
    """y positions (480x272 units) of the thin beat lines across the lane. Colour-agnostic: a line is a row that
    is brighter than the rows 4 units above and below it over most of the lane's width (the original skin draws
    it cyan, the default skin of a fresh profile grey; the lane behind it is dark in both)."""
    im = Image.open(path).convert("L")
    s = im.width // 480
    px = im.load()
    x0, x1, y0, y1 = LANE_X[0] * s, LANE_X[1] * s, TOP * s, BOTTOM * s
    xs = range(x0, x1, 2)
    gap = 4 * s
    rows = {}

    def row(y):
        if y not in rows:
            rows[y] = [px[x, y] for x in xs]
        return rows[y]

    score = []
    for y in range(y0, y1):
        r, up, dn = row(y), row(y - gap), row(y + gap)
        n = 0
        for v, u, d in zip(r, up, dn):
            if v - max(u, d) > 28:
                n += 1
        score.append(n / len(r))
    # centres of runs of rows whose coverage is high
    out, run = [], []
    for i, v in enumerate(score):
        if v >= 0.5:
            run.append((i + y0, v))
        elif run:
            tot = sum(w for _, w in run)
            out.append(sum(y * w for y, w in run) / tot / s)
            run = []
    if run:
        tot = sum(w for _, w in run)
        out.append(sum(y * w for y, w in run) / tot / s)
    return out


def tracks(vbs, fr, step):
    """Follow every line from capture to capture (a line moves down by 0..12 px per vblank, and the lines are
    far apart, so the nearest one at or below the old position is the same line). -> [[(vblank, y), ...]]"""
    done, live = [], []
    for vb, ys in zip(vbs, fr):
        nxt, taken = [], set()
        for tr in live:
            y0 = tr[-1][1]
            cand = [(z - y0, j) for j, z in enumerate(ys) if 0 <= z - y0 <= 12 * step and j not in taken]
            if cand:
                _, j = min(cand)
                taken.add(j)
                tr.append((vb, ys[j]))
                nxt.append(tr)
            else:
                done.append(tr)
        nxt += [[(vb, z)] for j, z in enumerate(ys) if j not in taken]
        live = nxt
    return done + live


def slope(tr):
    """Least-squares slope of y against the vblank (px per vblank). A single capture is placed at its
    vblank only to within a frame or so, and a fit over the 15-30 captures a line stays visible averages
    that out where the difference of two far-apart captures does not."""
    n = len(tr)
    mt = sum(t for t, _ in tr) / n
    my = sum(y for _, y in tr) / n
    den = sum((t - mt) ** 2 for t, _ in tr)
    return sum((t - mt) * (y - my) for t, y in tr) / den


def main():
    d = Path(sys.argv[1])
    first, last, step = (int(v) for v in sys.argv[2:5])
    vbs = list(range(first, last + 1, step))
    fr = [lines(d / f"frame_{v}.ppm") for v in vbs]
    # Only whole passes of a line through the lane count (it entered below the top edge and was followed to
    # the bottom): a track cut by the end of the window, or by a missed detection, is short.
    full = [tr for tr in tracks(vbs, fr, step) if len(tr) >= 6 and tr[-1][1] - tr[0][1] >= 60]
    sl = [slope(tr) for tr in full]
    if len(sl) < 3:
        print("not enough moving lines", len(sl))
        return 1
    sl.sort()
    med = sl[len(sl) // 2]
    good = [x for x in sl if abs(x - med) <= 0.15 * med]
    speed = sum(good) / len(good)
    print(f"px_per_vblank {speed:.4f} samples {len(good)} of {len(sl)} lines_per_frame {sum(len(f) for f in fr) / len(fr):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
