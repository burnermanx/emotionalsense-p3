"""The pad's d-pad repeat: console-menu timing, one move per poll, no skipped or bursty steps."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_pad  # noqa: E402


class FakeSdl:
    def SDL_PumpEvents(self):
        pass


def make(held):
    p = es_pad.Pad.__new__(es_pad.Pad)
    p.sdl, p.gcs, p._scan, p._held, p._next, p.confirm_cross = FakeSdl(), [1], 0.0, {}, {}, False
    p._open = lambda: None
    p._state = lambda: {k: k in held for k in ("up", "down", "left", "right", "ok", "back")}
    return p


class PadRepeat(unittest.TestCase):
    def run_hold(self, seconds, poll=0.008, stall=None):
        held = {"down"}
        p = make(held)
        t = [100.0]
        out = []
        with mock.patch.object(es_pad.time, "monotonic", lambda: t[0]):
            end = t[0] + seconds
            while t[0] < end:
                for k in p.poll():
                    out.append(t[0] - 100.0)
                t[0] += stall(t[0]) if stall else poll
        return out

    def test_timing(self):
        m = self.run_hold(2.0)
        self.assertEqual(m[0], 0.0)                       # the press itself: immediately
        self.assertAlmostEqual(m[1], 0.35, delta=0.012)   # delay before repeating
        gaps = [b - a for a, b in zip(m[1:], m[2:])]
        for g in gaps:
            self.assertAlmostEqual(g, 0.075, delta=0.012)
        self.assertGreaterEqual(len(m), 22)               # ~13 a second

    def test_no_burst_after_stall(self):
        # one 300 ms stall (a slow frame): at most one move on resume, never a catch-up burst
        stalled = [False]

        def stall(now):
            if not stalled[0] and now > 100.6:
                stalled[0] = True
                return 0.3
            return 0.008
        m = self.run_hold(1.5, stall=stall)
        gaps = [b - a for a, b in zip(m, m[1:])]
        self.assertTrue(all(g >= 0.06 for g in gaps), gaps)

    def test_release_resets(self):
        p = make({"down"})
        self.assertEqual(p.poll(), ["down"])
        p._state = lambda: {k: False for k in ("up", "down", "left", "right", "ok", "back")}
        self.assertEqual(p.poll(), [])
        p._state = lambda: {k: k == "down" for k in ("up", "down", "left", "right", "ok", "back")}
        self.assertEqual(p.poll(), ["down"])              # a new press is a new first move


if __name__ == "__main__":
    unittest.main()
