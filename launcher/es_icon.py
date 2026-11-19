"""The application icon, drawn from scratch with Pillow.

A round glossy black button with a chrome ring and a reflection, amber light
rising from below in the centre, an italic geometric "P" with a "3" under it,
and "EMOTIONAL SENSE" in Exo 2 Italic (SIL OFL). Every shape is built from
circles, rectangles and arcs below; no game or internet image was used as a
base or traced, and the "P" is a plain stem with a half-round bowl, not the
shape of any existing logo.

    python3 launcher/es_icon.py --out DIR [--docs docs/launcher/icon.png]

writes icon-<n>.png for 16..512 px and icon.ico (16..256) into DIR.
`render(size)` is what the launcher and the packaging scripts call.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
S = 1024                                   # design canvas
SHEAR = 0.20                               # italic slant of the P and the 3
AMBER = (255, 168, 34)
GOLD = (255, 214, 96)


def _font_path() -> Optional[Path]:
    for p in (HERE / "assets" / "generated" / "Exo2-Italic.ttf",
              HERE / "assets" / ".cache" / "ofl_exo2_Exo2-Italic[wght].ttf"):
        if p.exists():
            return p
    return None


def _disc_mask(r: float, cx: float = S / 2, cy: float = S / 2, size: int = S) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return m


def _vertical(stops, h: int = S, w: int = S) -> Image.Image:
    """stops [(t, (r,g,b)), ...] top to bottom, as an RGBA image."""
    col = Image.new("RGBA", (1, h))
    px = col.load()
    for y in range(h):
        t = y / (h - 1)
        for i in range(len(stops) - 1):
            t0, c0 = stops[i]
            t1, c1 = stops[i + 1]
            if t0 <= t <= t1:
                u = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                px[0, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3)) + (255,)
                break
    return col.resize((w, h))


def _shear(img: Image.Image, k: float, anchor_y: float) -> Image.Image:
    """Slant an image to the right going up, about the line y = anchor_y."""
    return img.transform(img.size, Image.Transform.AFFINE, (1, k, -k * anchor_y, 0, 1, 0),
                         Image.Resampling.BICUBIC)


def _radial_alpha(cx: float, cy: float, rx: float, ry: float, power: float = 2.0) -> Image.Image:
    """L image: 255 at the centre falling to 0 on the ellipse."""
    small = 256
    m = Image.new("L", (small, small))
    px = m.load()
    for y in range(small):
        for x in range(small):
            dx = (x / (small - 1) * S - cx) / rx
            dy = (y / (small - 1) * S - cy) / ry
            d = math.sqrt(dx * dx + dy * dy)
            px[x, y] = int(255 * max(0.0, 1.0 - d) ** power)
    return m.resize((S, S), Image.Resampling.BICUBIC)


def _p_shape() -> Image.Image:
    """The P: a vertical stem and a bowl that is a rectangle with a half-round
    right side, with a half-round counter. Upright, 'L' mask."""
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    x0, y0, y1 = 322, 170, 548               # left edge, top, bottom of the stem
    stem = 96
    d.rectangle([x0, y0, x0 + stem, y1], fill=255)
    top, bot = y0, y0 + 246                  # bowl height
    r = (bot - top) / 2
    xr = x0 + stem + 154                     # where the bowl's straight part ends
    d.rectangle([x0, top, xr, bot], fill=255)
    d.pieslice([xr - r, top, xr + r, bot], -90, 90, fill=255)
    # counter
    ct, cb = top + 68, bot - 68
    cr = (cb - ct) / 2
    cxr = xr - 6
    d.rectangle([x0 + stem, ct, cxr, cb], fill=0)
    d.pieslice([cxr - cr, ct, cxr + cr, cb], -90, 90, fill=0)
    return m


def _three_shape() -> Image.Image:
    """The 3: two stacked half-rings opening to the left, round caps."""
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    t = 40                                    # stroke
    cx = 546
    # upper bowl runs from its upper left round the right to the middle, the
    # lower bowl from the middle round the right to its lower left.
    for cy, r, a0, a1 in ((628, 60, -150, 90), (726, 72, -90, 150)):
        d.arc([cx - r, cy - r, cx + r, cy + r], a0, a1, fill=255, width=t)
        for ang in (a0, a1):
            ex = cx + (r - t / 2) * math.cos(math.radians(ang))
            ey = cy + (r - t / 2) * math.sin(math.radians(ang))
            d.ellipse([ex - t / 2, ey - t / 2, ex + t / 2, ey + t / 2], fill=255)
    return m


def _chrome(mask: Image.Image, top: int, bottom: int) -> Image.Image:
    """Fill a mask with a bright-to-dark-to-bright chrome gradient."""
    h = bottom - top
    grad = _vertical([(0, (255, 255, 255)), (0.42, (214, 220, 226)), (0.5, (120, 128, 138)),
                      (0.58, (206, 212, 220)), (1, (248, 250, 252))], h=h)
    full = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    full.paste(grad, (0, top))
    full.putalpha(ImageChops.multiply(full.getchannel("A"), mask))
    return full


def render(size: int = 256, with_text: Optional[bool] = None) -> Image.Image:
    """The icon as an RGBA image of size x size."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    c = S / 2

    # --- outer chrome ring ---------------------------------------------------
    outer, inner = 492, 424
    ring = Image.new("L", (S, S), 0)
    ImageDraw.Draw(ring).ellipse([c - outer, c - outer, c + outer, c + outer], fill=255)
    ImageDraw.Draw(ring).ellipse([c - inner, c - inner, c + inner, c + inner], fill=0)
    chrome = _vertical([(0, (252, 253, 255)), (0.18, (200, 206, 214)), (0.5, (74, 80, 90)),
                        (0.78, (168, 176, 186)), (1, (236, 240, 246))])
    chrome.putalpha(ring)
    img.alpha_composite(chrome)
    # thin dark groove and a bright edge at the ring's inner side
    groove = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(groove)
    gd.ellipse([c - inner - 6, c - inner - 6, c + inner + 6, c + inner + 6], outline=(10, 12, 16, 230), width=12)
    img.alpha_composite(groove)

    # --- black glossy face ---------------------------------------------------
    face_r = inner - 4
    face_mask = _disc_mask(face_r)
    face = _vertical([(0, (34, 36, 42)), (0.5, (10, 11, 14)), (1, (2, 2, 3))])
    face.putalpha(face_mask)
    img.alpha_composite(face)

    # --- amber light rising from below --------------------------------------
    glow = Image.new("RGBA", (S, S), AMBER + (0,))
    glow.putalpha(_radial_alpha(c, 960, 520, 520, 1.6).point(lambda v: int(v * 0.95)))
    core = Image.new("RGBA", (S, S), GOLD + (0,))
    core.putalpha(_radial_alpha(c, 980, 250, 360, 1.4))
    beam = Image.new("L", (S, S), 0)                   # a narrow vertical beam in the middle
    bd = ImageDraw.Draw(beam)
    for i in range(60):
        w = 6 + i * 2.2
        a = int(70 * (1 - i / 60) ** 2)
        bd.rectangle([c - w, 330 + i * 7, c + w, 990], fill=a)
    beam = beam.filter(ImageFilter.GaussianBlur(18))
    bl = Image.new("RGBA", (S, S), GOLD + (0,))
    bl.putalpha(beam)
    light = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    for layer in (glow, bl, core):
        light.alpha_composite(layer)
    light.putalpha(ImageChops.multiply(light.getchannel("A"), face_mask))
    img.alpha_composite(light)

    # --- the P, with a soft drop shadow and an amber rim light --------------
    p = _shear(_p_shape(), SHEAR, 360)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow.putalpha(p.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.7)))
    img.alpha_composite(shadow, (0, 10))
    img.alpha_composite(_chrome(p, 170, 548))
    rim = p.filter(ImageFilter.MaxFilter(7))
    rim = ImageChops.subtract(rim, p)
    rl = Image.new("RGBA", (S, S), AMBER + (0,))
    rl.putalpha(rim.point(lambda v: int(v * 0.85)))
    img.alpha_composite(rl)

    # --- the 3 underneath ------------------------------------------------------
    th = _shear(_three_shape(), SHEAR, 690)
    sh2 = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh2.putalpha(th.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.6)))
    img.alpha_composite(sh2, (0, 8))
    amber3 = _vertical([(0, (255, 232, 150)), (1, (255, 160, 30))])
    amber3.putalpha(th)
    img.alpha_composite(amber3)

    # --- reflection on the upper face ----------------------------------------
    refl_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(refl_mask).ellipse([c - 330, 118, c + 330, 520], fill=255)
    refl_mask = ImageChops.multiply(refl_mask, face_mask)
    fade = _vertical([(0, (255, 255, 255)), (1, (0, 0, 0))], h=S)
    refl = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    a = ImageChops.multiply(fade.convert("L").crop((0, 118, S, 118 + 402)).resize((S, 402)).transform(
        (S, S), Image.Transform.AFFINE, (1, 0, 0, 0, 1, -118)), refl_mask)
    refl.putalpha(a.point(lambda v: int(v * 0.20)))
    img.alpha_composite(refl)
    # a small hot spot on the ring, top left
    spot = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    spot.putalpha(_radial_alpha(c - 330, c - 330, 150, 110, 2.0).point(lambda v: int(v * 0.8)))
    spot.putalpha(ImageChops.multiply(spot.getchannel("A"), ring))
    img.alpha_composite(spot)

    # --- the name, only where it can be read ----------------------------------
    if with_text is None:
        with_text = size >= 96
    fp = _font_path()
    if with_text and fp is not None:
        f = ImageFont.truetype(str(fp), 50)
        txt = "EMOTIONAL SENSE"
        track = 6
        widths = [f.getlength(ch) + track for ch in txt]
        tw = int(sum(widths))
        t_img = Image.new("RGBA", (tw + 40, 90), (0, 0, 0, 0))
        td = ImageDraw.Draw(t_img)
        x = 20
        for ch, w in zip(txt, widths):
            td.text((x, 14), ch, font=f, fill=(246, 244, 238, 255))
            x += w
        sh3 = t_img.getchannel("A").filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.9))
        ty = 850
        tx = int(c - t_img.width / 2)
        black = Image.new("RGBA", t_img.size, (0, 0, 0, 255))
        black.putalpha(sh3)
        img.alpha_composite(black, (tx, ty + 3))
        img.alpha_composite(t_img, (tx, ty))

    return img.resize((size, size), Image.Resampling.LANCZOS)


def write_all(out: Path, docs: Optional[Path] = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    big = render(512)
    for n in SIZES:
        (big if n == 512 else render(n)).save(out / f"icon-{n}.png")
    big.save(out / "icon.ico", sizes=[(n, n) for n in SIZES if n <= 256])
    if docs is not None:
        docs.parent.mkdir(parents=True, exist_ok=True)
        big.save(docs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--docs", type=Path, help="also write the 512 px render here")
    a = ap.parse_args()
    write_all(a.out, a.docs)
    print("icons:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
