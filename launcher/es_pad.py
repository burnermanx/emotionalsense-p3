"""Gamepad for the launcher, through the SDL2 the kit already carries.

SDL2 is loaded with ctypes (no new dependency, no pip package) and only the
game-controller API is used, with no video subsystem, so it never touches the
Tk window. Polling is non-blocking: Pad.poll() returns the navigation keys
("up", "down", "left", "right", "ok", "back") produced since the last call.

South (A) and east (B) follow the confirm setting, the same way the game's
menus do: with circle confirming, east (B, the PSP circle) is OK and south (A,
the cross) is Back; with cross confirming it is the other way round. The d-pad
and the left stick move; Start is OK; direction keys repeat while held.
If SDL2 or a controller is missing, the pad is simply inactive.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

INIT_GAMECONTROLLER = 0x2000
# SDL_GameControllerButton
BTN_A, BTN_B, BTN_START = 0, 1, 6
BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT = 11, 12, 13, 14
# SDL_GameControllerAxis
AX_LX, AX_LY = 0, 1
DEAD = 16000         # a stick counts as pushed past this...
LIVE = 10000         # ...and as released only below this (no flicker around the edge)
# Console-menu feel: ~350 ms before repeating, then ~13 moves a second.
REPEAT_FIRST, REPEAT_NEXT = 0.35, 0.075


def _candidates(kit: Optional[Path]):
    names = ["SDL2.dll"] if sys.platform.startswith("win") else ["libSDL2-2.0.so.0", "libSDL2.so"]
    if kit is not None:
        for sub in ("deps/lib", "deps/bin", "deps/lib64"):
            for n in names:
                p = kit / sub / n
                if p.exists():
                    yield str(p)
    found = ctypes.util.find_library("SDL2")
    if found:
        yield found
    for n in names:
        yield n


class Pad:
    kind = None            # style of the controller that pressed last (es_buttons.classify_pad)

    def __init__(self, kit: Optional[Path] = None):
        self.sdl = None
        self.gcs = []
        self._scan = 0.0
        self._held = {}        # (device, key) -> pressed and armed
        self._next = {}
        self._stuck = set()    # (device, key) already "pressed" when first seen: ignored until released once
        self._axis = {}        # (device, key) -> past the dead zone (hysteresis)
        self.kind = None       # style of the controller that pressed last (es_buttons.classify_pad)
        self._ngc = -1
        self.confirm_cross = False
        if os.environ.get("EMOTIONALSENSE_NO_PAD"):
            return
        for c in _candidates(kit):
            try:
                self.sdl = ctypes.CDLL(c)
                break
            except OSError:
                continue
        if self.sdl is None:
            return
        s = self.sdl
        s.SDL_Init.argtypes = [ctypes.c_uint32]
        s.SDL_GameControllerOpen.restype = ctypes.c_void_p
        s.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
        s.SDL_GameControllerGetButton.argtypes = [ctypes.c_void_p, ctypes.c_int]
        s.SDL_GameControllerGetAxis.argtypes = [ctypes.c_void_p, ctypes.c_int]
        s.SDL_GameControllerGetAxis.restype = ctypes.c_int16
        s.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
        s.SDL_GameControllerGetAttached.argtypes = [ctypes.c_void_p]
        s.SDL_NumJoysticks.restype = ctypes.c_int
        s.SDL_IsGameController.argtypes = [ctypes.c_int]
        s.SDL_SetHint.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        s.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        # positional face buttons: the bottom one is always the cross, even on a Nintendo pad
        s.SDL_SetHint(b"SDL_GAMECONTROLLER_USE_BUTTON_LABELS", b"0")
        if s.SDL_Init(INIT_GAMECONTROLLER) != 0:
            self.sdl = None
            return

    @property
    def available(self) -> bool:
        return self.sdl is not None

    def _open(self):
        """Keep every attached game controller open; look again once a second."""
        s = self.sdl
        now = time.monotonic()
        if now < self._scan:
            return
        self._scan = now + 1.0
        self.gcs = [g for g in self.gcs if s.SDL_GameControllerGetAttached(g)]
        n = max(0, s.SDL_NumJoysticks())
        ids = [i for i in range(n) if s.SDL_IsGameController(i)]
        # Compare with the number of GAME controllers, not of joysticks: a raw device that is
        # not a game controller (Steam Deck's own, gyro, mouse-like HID...) would otherwise make
        # every scan close and reopen everything, once a second, forever.
        if len(self.gcs) == len(ids) and self._ngc == n:
            return
        self._ngc = n
        for g in self.gcs:
            s.SDL_GameControllerClose(g)
        self.gcs = []
        for i in ids:
            g = s.SDL_GameControllerOpen(i)
            if g:
                self.gcs.append(g)
        self._reset()

    def _reset(self):
        """The device set changed: whatever is down now was down before we looked (or is a
        phantom): it only counts after it has been seen released."""
        self._held, self._next, self._axis = {}, {}, {}
        self._stuck = None     # filled by the next poll with everything that is down right now

    def rearm(self):
        """After a modal dialog or the game: forget held keys; what is still down must be
        released before it counts again."""
        self._reset()

    def _state(self) -> dict:
        """{(device, key): pressed} for every open controller."""
        s = self.sdl
        st = {}
        for d, g in enumerate(self.gcs):
            b = lambda n: bool(s.SDL_GameControllerGetButton(g, n))  # noqa: E731
            lx, ly = s.SDL_GameControllerGetAxis(g, AX_LX), s.SDL_GameControllerGetAxis(g, AX_LY)
            south, east = b(BTN_A), b(BTN_B)
            ok, back = (south, east) if self.confirm_cross else (east, south)
            for key, hit in (("up", b(BTN_UP) or self._ax(d, "up", -ly)),
                             ("down", b(BTN_DOWN) or self._ax(d, "down", ly)),
                             ("left", b(BTN_LEFT) or self._ax(d, "left", -lx)),
                             ("right", b(BTN_RIGHT) or self._ax(d, "right", lx)),
                             ("ok", ok or b(BTN_START)), ("back", back)):
                st[(d, key)] = bool(hit)
        return st

    def _kind_of(self, d):
        """The style of controller `d` (None if this SDL cannot tell: older than 2.0.12)."""
        try:
            import es_buttons
            s, g = self.sdl, self.gcs[d]
            s.SDL_GameControllerGetType.argtypes = [ctypes.c_void_p]
            s.SDL_GameControllerGetVendor.argtypes = [ctypes.c_void_p]
            s.SDL_GameControllerGetProduct.argtypes = [ctypes.c_void_p]
            s.SDL_GameControllerGetVendor.restype = s.SDL_GameControllerGetProduct.restype = ctypes.c_uint16
            sd = os.environ.get("SteamDeck", "")
            return es_buttons.classify_pad(int(s.SDL_GameControllerGetType(g)), int(s.SDL_GameControllerGetVendor(g)),
                                           int(s.SDL_GameControllerGetProduct(g)), sd not in ("", "0"))
        except (AttributeError, IndexError, OSError):
            return None

    def _ax(self, d, key, v) -> bool:
        on = self._axis.get((d, key), False)
        on = v > (LIVE if on else DEAD)
        self._axis[(d, key)] = on
        return on

    def poll(self) -> List[str]:
        if self.sdl is None:
            return []
        self.sdl.SDL_PumpEvents()
        self._open()
        if not self.gcs:
            return []
        out = []
        now = time.monotonic()
        state = self._state()
        if self._stuck is None:                  # first look after (re)open: anything down is stale
            self._stuck = {k for k, down in state.items() if down}
        for k, down in state.items():
            if down:
                if k in self._stuck:
                    continue
                if not self._held.get(k):
                    self._held[k] = True
                    self.kind = self._kind_of(k[0]) or self.kind
                    self._next[k] = now + REPEAT_FIRST
                    out.append(k[1])
                elif k[1] in ("up", "down", "left", "right") and now >= self._next[k]:
                    # One move per poll at most, never a burst after a slow frame: the next
                    # one is due REPEAT_NEXT after this one (after the due time, not "now",
                    # so the rate stays even; resynchronised if we fell far behind).
                    nxt = self._next[k] + REPEAT_NEXT
                    self._next[k] = nxt if nxt > now else now + REPEAT_NEXT
                    out.append(k[1])
            else:
                self._held[k] = False
                self._stuck.discard(k)
        return out

    def close(self):
        for g in self.gcs:
            self.sdl.SDL_GameControllerClose(g)
        self.gcs = []
