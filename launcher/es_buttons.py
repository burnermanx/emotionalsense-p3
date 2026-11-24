"""Button styles (round 36, B): which symbol or letter each face button shows, in the launcher.

The same table as the game's `game/button_style.h` (the game repaints its own prompts, this draws the
launcher's footer and hints), and the same composition with the confirm option: a prompt shows the
button that runs its action. Positions are the PSP's: cross = bottom, circle = right, square = left,
triangle = top; the controller is read by position (SDL_GAMECONTROLLER_USE_BUTTON_LABELS=0), so the
bottom button is the cross whatever is printed on it.
"""

from __future__ import annotations

from typing import Optional, Tuple

STYLES = ("auto", "playstation", "xbox", "steamdeck", "switch")
POSITIONS = ("cross", "circle", "square", "triangle")

# letter at each position
_LETTERS = {
    "xbox": ("A", "B", "X", "Y"),
    "steamdeck": ("A", "B", "X", "Y"),
    "switch": ("B", "A", "Y", "X"),
}
XBOX_COLORS = {"A": (0x6C, 0xC0, 0x4A), "B": (0xE8, 0x45, 0x3C), "X": (0x3F, 0x8C, 0xF2), "Y": (0xF2, 0xC2, 0x30)}
WHITE = (255, 255, 255)

# SDL_GameControllerType (SDL 2.0.12 .. 2.30)
_SDL_PS = {3, 4, 7}                    # PS3, PS4, PS5
_SDL_SWITCH = {5, 11, 12, 13}          # Switch Pro, Joy-Con left / right / pair
DECK_VID, DECK_PID = 0x28DE, 0x1205


def classify_pad(sdl_type: int, vid: int = 0, pid: int = 0, deck_env: bool = False) -> str:
    """The style of a controller: what SDL says it is (the real type when Steam Input describes it,
    otherwise "Xbox 360" for the Steam virtual pad), the Steam Deck by its own ids or, for Steam's
    virtual pad, by SteamDeck=1."""
    if vid == DECK_VID and pid == DECK_PID:
        return "steamdeck"
    if sdl_type in _SDL_PS:
        return "playstation"
    if sdl_type in _SDL_SWITCH:
        return "switch"
    if deck_env and vid == DECK_VID:
        return "steamdeck"
    return "xbox"                      # Xbox 360/One and every generic pad


def resolve_style(option: str, pad_style: Optional[str]) -> str:
    """`auto` follows the controller that pressed last; without one (keyboard only) PlayStation."""
    if option in STYLES and option != "auto":
        return option
    return pad_style if pad_style in STYLES and pad_style != "auto" else "playstation"


def position_of(action: str, confirm_mode: str) -> str:
    """The physical position (cross/circle) of "ok" or "back": circle confirms unless the confirm
    option is cross."""
    ok = "cross" if confirm_mode == "cross" else "circle"
    back = "circle" if ok == "cross" else "cross"
    return ok if action == "ok" else back


def face(style: str, position: str) -> Tuple[Optional[str], Optional[Tuple[int, int, int]]]:
    """(letter, colour) of a position in a style; (None, None) for PlayStation (the symbol itself)."""
    if style not in _LETTERS:
        return None, None
    letter = _LETTERS[style][POSITIONS.index(position)]
    return letter, XBOX_COLORS[letter] if style == "xbox" else WHITE


def action_face(style: str, action: str, confirm_mode: str):
    """What the footer draws for "ok" / "back": (position, letter, colour)."""
    pos = position_of(action, confirm_mode)
    letter, col = face(style, pos)
    return pos, letter, col


def letter_of(style: str, position: str) -> str:
    """The name of a position's button for a text: the letter, or the PlayStation one."""
    letter, _ = face(style, position)
    return letter or {"cross": "X", "circle": "O", "square": "[]", "triangle": "/\\"}[position]


# Button pills (round 36b): the same table as game/button_style.h `bs_pill_look`. "M" is the Menu icon (three
# bars), "V" the View icon (two overlapping squares), "" keeps the original word. START, SELECT, left and right shoulder.
PILLS = ("start", "select", "left", "right")
_PILLS = {
    "playstation": ("", "", "L1", "R1"),
    "xbox": ("M", "V", "LB", "RB"),
    "steamdeck": ("M", "V", "L1", "R1"),
    "switch": ("+", "-", "L", "R"),
}


def pill(style: str, which: str) -> str:
    """The label a button pill shows in a style (independent of the confirm option)."""
    return _PILLS.get(style, _PILLS["playstation"])[PILLS.index(which)]
