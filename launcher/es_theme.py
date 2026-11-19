"""The launcher's look: dark stage, diagonal light beams, a soft flare, a
metallic header strip, italic type, green selection bar, neon accents.

Drawn from scratch with Pillow from the parameters below; no image, font
glyph outline or colour table is taken from the game. It is inspired by the
*language* of the game's menus, nothing more.

`render_assets()` produces the heavy images once (the build step
launcher/assets/build_assets.py writes them as PNG; the launcher falls back to
rendering them at start-up when the files are missing).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 960, 540

BLACK = (5, 7, 9)
GREEN = (38, 214, 96)
LIME = (166, 255, 40)
CYAN = (40, 214, 255)
MAGENTA = (255, 54, 168)
WHITE = (240, 244, 246)
DIM = (120, 128, 132)
PANEL = (10, 13, 16)

Color = Tuple[int, int, int]


def assets_dir() -> Path:
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here))
        if (base / "assets").exists():
            return base / "assets"
    return here / "assets"


def _lerp(a: Color, b: Color, t: float) -> Color:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def vgradient(w: int, h: int, stops) -> Image.Image:
    """stops: [(pos 0..1, (r,g,b[,a])), ...] top to bottom."""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                u = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                c = tuple(int(c0[k] + ((c1[k] if len(c1) > k else 255) - c0[k]) * u)
                          for k in range(len(c0)))
                if len(c) == 3:
                    c = c + (255,)
                for x in range(w):
                    px[x, y] = c
                break
    return img


def hgradient(w: int, h: int, stops) -> Image.Image:
    return vgradient(h, w, stops).transpose(Image.Transpose.ROTATE_90).transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def _beam(size, p0, p1, width0, width1, alpha) -> Image.Image:
    layer = Image.new("L", size, 0)
    d = ImageDraw.Draw(layer)
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    nx, ny = -dy / n, dx / n
    poly = [(x0 + nx * width0, y0 + ny * width0), (x0 - nx * width0, y0 - ny * width0),
            (x1 - nx * width1, y1 - ny * width1), (x1 + nx * width1, y1 + ny * width1)]
    d.polygon(poly, fill=alpha)
    return layer.filter(ImageFilter.GaussianBlur(10))


def render_background(w: int = W, h: int = H) -> Image.Image:
    """Near-black stage with long diagonal beams and a flare behind the title."""
    img = Image.new("RGBA", (w, h), BLACK + (255,))
    # a faint vertical falloff so the top is a touch lighter than the bottom
    img.alpha_composite(vgradient(w, h, [(0, (22, 26, 30, 255)), (0.5, (8, 10, 13, 255)), (1, (2, 3, 4, 255))]))
    light = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    beams = [  # (from, to, half-width at start, at end, alpha, tint)
        ((w * 0.78, -40), (w * 0.28, h + 40), 6, 90, 42, (210, 230, 255)),
        ((w * 0.95, -40), (w * 0.62, h + 40), 4, 60, 30, (255, 220, 235)),
        ((w * 0.52, -40), (w * 0.05, h + 40), 3, 40, 22, (200, 255, 225)),
        ((w * 1.1, 60), (w * 0.72, h + 40), 2, 24, 26, (255, 255, 255)),
    ]
    for p0, p1, a, b, al, tint in beams:
        m = _beam((w, h), p0, p1, a, b, al)
        layer = Image.new("RGBA", (w, h), tint + (0,))
        layer.putalpha(m)
        light.alpha_composite(layer)
    img.alpha_composite(light)
    # soft flare, warm core fading to violet, behind the big title
    fx, fy = int(w * 0.30), int(h * 0.30)
    flare = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for r, col, al in ((260, (150, 90, 200), 46), (150, (255, 150, 170), 70), (70, (255, 230, 220), 120)):
        g = Image.radial_gradient("L").resize((2 * r, 2 * r))
        g = g.point(lambda v, al=al: int(max(0, 255 - v * 1.42) * al / 255))
        tile = Image.new("RGBA", (2 * r, 2 * r), col + (0,))
        tile.putalpha(g)
        flare.alpha_composite(tile, (fx - r, fy - r))
    streak = Image.new("L", (w, h), 0)
    ImageDraw.Draw(streak).line([(fx - 280, fy + 120), (fx + 380, fy - 150)], fill=70, width=2)
    streak = streak.filter(ImageFilter.GaussianBlur(1.5))
    s = Image.new("RGBA", (w, h), (255, 235, 235, 0))
    s.putalpha(streak)
    flare.alpha_composite(s)
    img.alpha_composite(flare)
    # vignette
    v = Image.radial_gradient("L").resize((w, h))
    v = v.point(lambda p: max(0, min(255, int((p - 140) * 1.4))))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    black.putalpha(v)
    img.alpha_composite(black)
    return img


def render_header(w: int = 420, h: int = 46) -> Image.Image:
    """Metallic grey strip, flat on the left, swept up at the right end."""
    base = vgradient(w, h, [(0, (150, 154, 158, 255)), (0.45, (92, 96, 100, 255)),
                            (0.55, (50, 53, 56, 255)), (1, (26, 28, 30, 255))])
    mask = Image.new("L", (w * 4, h * 4), 0)
    d = ImageDraw.Draw(mask)
    pts = [(0, 0), (w * 4, 0)]
    for i in range(0, 41):                     # a smooth concave sweep on the right edge
        t = i / 40
        pts.append((w * 4 * (0.80 + 0.20 * (1 - t) ** 2.2), h * 4 * t))
    pts.append((0, h * 4))
    d.polygon(pts, fill=255)
    mask = mask.resize((w, h), Image.Resampling.LANCZOS)
    base.putalpha(mask)
    hl = ImageDraw.Draw(base)
    hl.line([(0, 1), (int(w * 0.8), 1)], fill=(220, 224, 226, 200))
    hl.line([(0, h - 1), (int(w * 0.78), h - 1)], fill=(30, 255, 120, 160))
    return base


def render_select_band(w: int = 600, h: int = 50) -> Image.Image:
    """The selected row: deep green with a bright core and lime rules."""
    img = hgradient(w, h, [(0, (6, 46, 22, 0)), (0.12, (10, 92, 44, 230)), (0.55, (18, 150, 72, 245)),
                           (1, (30, 210, 96, 255))])
    core = vgradient(w, h, [(0, (0, 0, 0, 90)), (0.35, (255, 255, 255, 0)), (0.5, (190, 255, 210, 70)),
                            (0.65, (255, 255, 255, 0)), (1, (0, 0, 0, 90))])
    img = ImageChops.multiply(img, Image.new("RGBA", (w, h), (255, 255, 255, 255)))
    img.alpha_composite(core)
    d = ImageDraw.Draw(img)
    d.line([(0, 1), (w, 1)], fill=LIME + (220,))
    d.line([(0, h - 2), (w, h - 2)], fill=LIME + (160,))
    glow = img.filter(ImageFilter.GaussianBlur(5))
    out = Image.new("RGBA", (w, h + 20), (0, 0, 0, 0))
    out.alpha_composite(glow, (0, 10))
    out.alpha_composite(img, (0, 10))
    return out


def render_panel(w: int, h: int, cut: int = 16, accent: Color = CYAN) -> Image.Image:
    """Dark panel with chamfered corners and a gradient frame."""
    s = 3
    big = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    poly = [(cut * s, 0), ((w - cut) * s, 0), (w * s, cut * s), (w * s, (h - cut) * s),
            ((w - cut) * s, h * s), (cut * s, h * s), (0, (h - cut) * s), (0, cut * s)]
    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    fill = vgradient(w * s, h * s, [(0, (22, 28, 32, 235)), (1, PANEL + (235,))])
    fill.putalpha(ImageChops.multiply(fill.getchannel("A"), mask))
    big.alpha_composite(fill)
    frame = Image.new("RGBA", big.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.polygon(poly, outline=(150, 160, 166, 255), width=2 * s)
    grad = vgradient(w * s, h * s, [(0, accent + (255,)), (0.5, (70, 78, 84, 200)), (1, (36, 40, 44, 160))])
    grad.putalpha(ImageChops.multiply(grad.getchannel("A"), frame.getchannel("A")))
    big.alpha_composite(grad)
    return big.resize((w, h), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
# type
# ---------------------------------------------------------------------------

_font_cache: dict = {}


def font_for(lang: str, size: int, kind: str = "ui") -> ImageFont.FreeTypeFont:
    """Latin (and the big titles): Exo 2 Italic. Japanese / Korean body text:
    the Noto Sans subsets. All from assets/ (SIL OFL)."""
    name = {"ja": "NotoSansJP-sub.ttf", "ko": "NotoSansKR-sub.ttf"}.get(lang, "Exo2-Italic.ttf") \
        if kind == "ui" else "Exo2-Italic.ttf"
    key = (name, size)
    if key not in _font_cache:
        p = assets_dir() / "generated" / name
        if not p.exists():
            raise FileNotFoundError(f"{p}: run launcher/assets/build_assets.py")
        _font_cache[key] = ImageFont.truetype(str(p), size)
    return _font_cache[key]


def _shear(img: Image.Image, k: float) -> Image.Image:
    w, h = img.size
    pad = int(h * k) + 2
    out = Image.new("RGBA", (w + pad, h), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    return out.transform(out.size, Image.Transform.AFFINE, (1, k, -k * h, 0, 1, 0), Image.Resampling.BICUBIC)


def text_image(text: str, font: ImageFont.FreeTypeFont, fill=WHITE, *, chrome: bool = False,
               shadow: bool = True, slant: float = 0.0, stroke: Optional[Color] = None) -> Image.Image:
    l, t, r, b = font.getbbox(text)
    w, h = r - l + 12, b - t + 12
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((6 - l, 6 - t), text, font=font, fill=255)
    if chrome:
        paint = vgradient(w, h, [(0, (255, 255, 255, 255)), (0.45, (190, 196, 202, 255)),
                                 (0.5, (110, 116, 124, 255)), (0.62, (214, 220, 226, 255)),
                                 (1, (150, 156, 162, 255))])
    else:
        paint = Image.new("RGBA", (w, h), tuple(fill) + (255,))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.putalpha(mask.point(lambda v: v // 2))
        out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(2)), (2, 3))
    if stroke:
        st = mask.filter(ImageFilter.MaxFilter(5))
        sc = Image.new("RGBA", (w, h), tuple(stroke) + (255,))
        sc.putalpha(st)
        out.alpha_composite(sc)
    paint.putalpha(mask)
    out.alpha_composite(paint)
    return _shear(out, slant) if slant else out


def render_assets() -> dict:
    return {
        "bg": render_background(),
        "header": render_header(),
        "band": render_select_band(),
    }
