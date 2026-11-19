"""K1: nothing may repeat without a real press. Controller states fed through a fake SDL:
rest with noise, a trigger at -32768, a released hat, a duplicated/ghost device stuck "down",
a raw joystick that is not a game controller (must not cause reopen churn)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_pad  # noqa: E402


class FakeSdl:
    """Devices: list of dicts {"gc": bool, "btn": {n: 1}, "axis": {n: v}}."""

    def __init__(self, devices):
        self.devices = devices
        self.opens = 0

    def SDL_PumpEvents(self): pass
    def SDL_NumJoysticks(self): return len(self.devices)
    def SDL_IsGameController(self, i): return 1 if self.devices[i]["gc"] else 0
    def SDL_GameControllerOpen(self, i):
        self.opens += 1
        return i + 1                                   # handle = index + 1 (non-zero)
    def SDL_GameControllerClose(self, g): pass
    def SDL_GameControllerGetAttached(self, g): return 1
    def SDL_GameControllerGetButton(self, g, n): return self.devices[g - 1]["btn"].get(n, 0)
    def SDL_GameControllerGetAxis(self, g, n): return self.devices[g - 1]["axis"].get(n, 0)


def make(devices):
    p = es_pad.Pad.__new__(es_pad.Pad)
    p.sdl = FakeSdl(devices)
    p.gcs, p._scan, p._held, p._next, p.confirm_cross = [], 0.0, {}, {}, False
    p._stuck, p._axis, p._ngc = set(), {}, -1
    return p


def run(p, seconds, step=0.008, t0=100.0, hook=None):
    t = [t0]
    out = []
    with mock.patch.object(es_pad.time, "monotonic", lambda: t[0]):
        while t[0] < t0 + seconds:
            if hook:
                hook(t[0] - t0)
            out += [(round(t[0] - t0, 3), k) for k in p.poll()]
            t[0] += step
    return out


def pad(**kw):
    d = {"gc": True, "btn": {}, "axis": {}}
    d.update(kw)
    return d


class Ghost(unittest.TestCase):
    def test_rest_with_noise_is_silent(self):
        noisy = {0: 4000, 1: -5000, 2: -32768, 3: 3000, 4: -2000, 5: -32768}   # sticks wobble, both triggers at rest = -32768
        p = make([pad(axis=noisy)])
        self.assertEqual(run(p, 5), [])

    def test_trigger_axes_are_not_directions(self):
        p = make([pad(axis={2: 32767, 5: 32767})])         # triggers fully pressed
        self.assertEqual(run(p, 2), [])

    def test_stick_noise_around_the_edge_does_not_flicker(self):
        p = make([pad()])
        vals = [0, 17000, 15000, 17000, 12000, 17000, 9000]    # engage at 16000, release below 10000

        def hook(t):
            p.sdl.devices[0]["axis"][1] = vals[min(int(t / 0.05), len(vals) - 1)]
        out = run(p, 0.34, hook=hook)
        self.assertEqual([k for _, k in out], ["down"])     # one press; 15000/12000 do not release it

    def test_ghost_device_stuck_down_from_the_start_never_repeats(self):
        ghost = pad(btn={es_pad.BTN_DOWN: 1})
        p = make([ghost])
        self.assertEqual(run(p, 5), [])

    def test_ghost_does_not_block_the_real_pad(self):
        ghost, real = pad(btn={es_pad.BTN_DOWN: 1}), pad()
        p = make([ghost, real])

        def hook(t):
            real["btn"][es_pad.BTN_UP] = 1 if 1.0 <= t < 1.1 else 0
        out = run(p, 3, hook=hook)
        self.assertEqual([k for _, k in out], ["up"])

    def test_ghost_that_is_released_once_then_works_normally(self):
        g = pad(btn={es_pad.BTN_DOWN: 1})
        p = make([g])

        def hook(t):
            g["btn"][es_pad.BTN_DOWN] = 1 if (t < 0.5 or 1.0 <= t < 1.02) else 0
        out = run(p, 2, hook=hook)
        self.assertEqual([k for _, k in out], ["down"])
        self.assertAlmostEqual(out[0][0], 1.0, delta=0.02)

    def test_non_controller_joystick_does_not_reopen_every_scan(self):
        p = make([pad(), {"gc": False, "btn": {}, "axis": {}}])
        run(p, 5)
        self.assertEqual(p.sdl.opens, 1)                    # opened once, not once a second

    def test_press_while_held_at_open_needs_release(self):
        d = pad()
        p = make([d])
        run(p, 0.2)
        d["btn"][es_pad.BTN_DOWN] = 1
        out = run(p, 0.3, t0=101.0)
        self.assertEqual([k for _, k in out], ["down"])     # a real press after rest

    def test_rearm_ignores_what_is_still_held(self):
        d = pad()
        p = make([d])
        run(p, 0.1)
        d["btn"][es_pad.BTN_B] = 1
        self.assertEqual([k for _, k in run(p, 0.1, t0=100.2)], ["ok"])
        p.rearm()
        self.assertEqual(run(p, 1.0, t0=105.0), [])         # still held after rearm: nothing
        d["btn"][es_pad.BTN_B] = 0
        run(p, 0.1, t0=107.0)
        d["btn"][es_pad.BTN_B] = 1
        self.assertEqual([k for _, k in run(p, 0.1, t0=108.0)], ["ok"])

    def test_no_event_without_a_press(self):
        p = make([pad(), pad(btn={es_pad.BTN_UP: 0})])
        self.assertEqual(run(p, 10), [])


if __name__ == "__main__":
    unittest.main()
