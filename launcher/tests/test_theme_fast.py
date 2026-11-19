"""K2: the gradient is built per row (fast) and must equal the per-pixel definition; panels are cached."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_theme as th  # noqa: E402


def naive(w, h, stops):
    from PIL import Image
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                u = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                c = tuple(int(c0[k] + ((c1[k] if len(c1) > k else 255) - c0[k]) * u) for k in range(len(c0)))
                if len(c) == 3:
                    c = c + (255,)
                for x in range(w):
                    px[x, y] = c
                break
    return img


class Fast(unittest.TestCase):
    def test_same_pixels(self):
        for st in ([(0, (22, 28, 32, 235)), (1, (1, 2, 3, 235))],
                   [(0, (1, 2, 3)), (.5, (70, 78, 84, 200)), (1, (36, 40, 44, 160))]):
            for w, h in ((1, 5), (33, 17), (300, 120)):
                self.assertEqual(th.vgradient(w, h, st).tobytes(), naive(w, h, st).tobytes())

    def test_panel_cached_and_quick(self):
        th.render_panel(848, 160)
        t = time.perf_counter()
        a = th.render_panel(848, 160)
        self.assertLess(time.perf_counter() - t, 0.005)
        self.assertIs(a, th.render_panel(848, 160))
        self.assertIsNot(a, th.render_panel(848, 161))


if __name__ == "__main__":
    unittest.main()
