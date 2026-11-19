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
DEAD = 14000
REPEAT_FIRST, REPEAT_NEXT = 0.40, 0.12


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
    def __init__(self, kit: Optional[Path] = None):
        self.sdl = None
        self.gcs = []
        self._scan = 0.0
        self._held = {}
        self._next = {}
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
        have = len(self.gcs)
        if have == max(0, s.SDL_NumJoysticks()):
            return
        for g in self.gcs:
            s.SDL_GameControllerClose(g)
        self.gcs = []
        for i in range(max(0, s.SDL_NumJoysticks())):
            if s.SDL_IsGameController(i):
                g = s.SDL_GameControllerOpen(i)
                if g:
                    self.gcs.append(g)

    def _state(self) -> dict:
        s = self.sdl
        st = {k: False for k in ("up", "down", "left", "right", "ok", "back")}
        for g in self.gcs:
            b = lambda n: bool(s.SDL_GameControllerGetButton(g, n))  # noqa: E731
            lx, ly = s.SDL_GameControllerGetAxis(g, AX_LX), s.SDL_GameControllerGetAxis(g, AX_LY)
            south, east = b(BTN_A), b(BTN_B)
            ok, back = (south, east) if self.confirm_cross else (east, south)
            st["up"] |= b(BTN_UP) or ly < -DEAD
            st["down"] |= b(BTN_DOWN) or ly > DEAD
            st["left"] |= b(BTN_LEFT) or lx < -DEAD
            st["right"] |= b(BTN_RIGHT) or lx > DEAD
            st["ok"] |= ok or b(BTN_START)
            st["back"] |= back
        return st

    def poll(self) -> List[str]:
        if self.sdl is None:
            return []
        self.sdl.SDL_PumpEvents()
        self._open()
        if not self.gcs:
            return []
        out = []
        now = time.monotonic()
        for k, down in self._state().items():
            if down:
                if not self._held.get(k):
                    self._held[k] = True
                    self._next[k] = now + REPEAT_FIRST
                    out.append(k)
                elif k in ("up", "down", "left", "right") and now >= self._next[k]:
                    self._next[k] = now + REPEAT_NEXT
                    out.append(k)
            else:
                self._held[k] = False
        return out

    def close(self):
        for g in self.gcs:
            self.sdl.SDL_GameControllerClose(g)
        self.gcs = []
