#!/usr/bin/env python3
"""Measure how fast the lane's beat lines fall in captured frames (frame_<vblank>.ppm), in pixels of the
480x272 picture per vblank. Used by scripts/check_scripts.sh to compare two songs / two speed systems:

    measure_speed.py <dir> <first_vb> <last_vb> <step>

The measure lines (one thin cyan line across the lane per bar) are found in the lane above the judgment
box in every captured frame; each line is followed to the next frame (a short step, so it is the same
line) and the shifts are averaged. Prints `px_per_vblank <value> samples <n> ...`. Only PIL is needed."""
import sys
from pathlib import Path
from PIL import Image

LANE_X = (180, 300)        # inside the lane borders, 480x272 units
TOP, BOTTOM = 4, 120       # above the judgment box (y ~ 126..132)


def lines(path: Path):
    im = Image.open(path).convert("RGB")
    s = im.width // 480
    px = im.load()
    x0, x1, y0, y1 = LANE_X[0] * s, LANE_X[1] * s, TOP * s, BOTTOM * s
    score = []
    for y in range(y0, y1):
        n = 0
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            if g > 100 and b > 110 and r < 70 and g - r > 70:
                n += 1
        score.append(n / ((x1 - x0) / 2))
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


def main():
    d = Path(sys.argv[1])
    first, last, step = (int(v) for v in sys.argv[2:5])
    vbs = list(range(first, last + 1, step))
    fr = [lines(d / f"frame_{v}.ppm") for v in vbs]
    # Frames are compared `lag` captures apart (the game may repeat a picture, and one pixel of a short step is
    # a large share of it); the same line is the first one below the old position within 12 px per vblank.
    lag = 6
    shifts = []
    for i in range(len(vbs) - lag):
        a, b = fr[i], fr[i + lag]
        dt = vbs[i + lag] - vbs[i]
        for y in a:
            below = [z - y for z in b if 0 <= z - y <= 12 * dt]
            if below and len(a) == len(b):
                shifts.append(min(below) / dt)
    if len(shifts) < 4:
        print("not enough moving lines", len(shifts))
        return 1
    shifts.sort()
    med = shifts[len(shifts) // 2]
    good = [x for x in shifts if abs(x - med) <= 0.15 * med]
    speed = sum(good) / len(good)
    print(f"px_per_vblank {speed:.4f} samples {len(good)} of {len(shifts)} lines_per_frame {sum(len(f) for f in fr) / len(fr):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
