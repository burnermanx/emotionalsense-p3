"""Launcher language: detection and string lookup.

Languages: pt-BR, en, es, ja, ko (English when the system language is not one
of them). Strings live in locales/<code>.json, one file per language with the
same keys (tests/launcher/test_i18n.py fails when a key is missing or extra).
Stylised screen titles (SETUP, PLAY, ...) are not translated, as in the game.
"""

from __future__ import annotations

import json
import locale
import os
import re
import sys
from pathlib import Path
from typing import Mapping, Optional

LANGS = ["en", "pt-BR", "es", "ja", "ko"]
NAMES = {"en": "English", "pt-BR": "Português (Brasil)", "es": "Español",
         "ja": "日本語", "ko": "한국어"}
DEFAULT = "en"

# Windows primary language ids (LANGID & 0x3FF).
_WIN_PRIMARY = {0x16: "pt-BR", 0x0A: "es", 0x11: "ja", 0x12: "ko", 0x09: "en"}


def locales_dir() -> Path:
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):          # PyInstaller onedir
        base = Path(getattr(sys, "_MEIPASS", here))
        cand = base / "locales"
        if cand.exists():
            return cand
    return here / "locales"


def normalize(tag: Optional[str]) -> Optional[str]:
    """'pt_PT.UTF-8' -> 'pt-BR', 'es-MX' -> 'es', 'ja_JP' -> 'ja'; None if unsupported."""
    if not tag:
        return None
    t = tag.split(".")[0].split("@")[0].replace("_", "-").strip()
    if not t or t in ("C", "POSIX"):
        return None
    lang = t.split("-")[0].lower()
    if lang == "pt":
        return "pt-BR"                          # pt-PT and the rest fall to pt-BR
    if lang in ("es", "ja", "ko", "en"):
        return lang
    # Windows spells Chinese/other locales we do not have: unsupported
    return None


def _posix(env: Mapping[str, str]) -> Optional[str]:
    # LANGUAGE is a colon-separated priority list; the first supported entry wins.
    for entry in env.get("LANGUAGE", "").split(":"):
        if entry in ("C", "POSIX"):
            return DEFAULT
        n = normalize(entry)
        if n:
            return n
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = env.get(var, "")
        if v:
            if v in ("C", "POSIX"):
                return DEFAULT
            return normalize(v) or DEFAULT      # the first one set decides
    return None


def _windows() -> Optional[str]:
    try:
        import ctypes
        k = ctypes.windll.kernel32                       # type: ignore[attr-defined]
        buf = ctypes.create_unicode_buffer(85)
        if k.GetUserDefaultLocaleName(buf, 85):
            n = normalize(buf.value)
            if n:
                return n
        lid = k.GetUserDefaultUILanguage()
        return _WIN_PRIMARY.get(lid & 0x3FF)
    except Exception:
        return None


# Language names of the Steam client (registry.vdf "language") -> ours.
_STEAM = {"brazilian": "pt-BR", "portuguese": "pt-BR", "english": "en", "spanish": "es",
          "latam": "es", "japanese": "ja", "koreana": "ko"}


def steam_session(env: Mapping[str, str]) -> bool:
    """Steam Game Mode (gamescope) or a game started by the Steam client."""
    if any(env.get(k) for k in ("SteamDeck", "SteamGamepadUI", "SteamOS", "GAMESCOPE_WAYLAND_DISPLAY",
                                "SteamAppId", "SteamGameId", "STEAM_COMPAT_DATA_PATH")):
        return True
    return "gamescope" in env.get("XDG_CURRENT_DESKTOP", "").lower().split(":") or \
        env.get("XDG_SESSION_DESKTOP", "").lower() == "gamescope"


def steam_language(home: Optional[Path] = None) -> Optional[str]:
    """Language of the Steam client, read-only, from registry.vdf
    (HKCU/Software/Valve/Steam/language). None when absent, unreadable or unsupported."""
    home = home or Path.home()
    for rel in (".steam/registry.vdf", ".local/share/Steam/registry.vdf"):
        try:
            text = (home / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"language"\s+"([^"]*)"', text, re.I)
        if m:
            n = _STEAM.get(m.group(1).strip().lower())
            if n:
                return n
    return None


def plasma_language(home: Optional[Path] = None) -> Optional[str]:
    """Desktop-mode language from ~/.config/plasma-localerc ([Formats] LANG, [Translations] LANGUAGE)."""
    home = home or Path.home()
    try:
        text = (home / ".config/plasma-localerc").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    vals = {}
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if _:
            vals.setdefault(k.strip(), v.strip())
    for entry in vals.get("LANGUAGE", "").split(":"):
        if normalize(entry):
            return normalize(entry)
    return normalize(vals.get("LANG"))


def detect(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None,
           home: Optional[Path] = None) -> str:
    real = env is None
    env = os.environ if env is None else env
    platform = platform or sys.platform
    code = None
    if platform.startswith("win"):
        code = _windows()
        if not code:                                    # Wine without the API, tests
            code = _posix(env)
    else:
        if steam_session(env):                          # Game Mode: LANG is the system default
            code = steam_language(home) or plasma_language(home)
        code = code or _posix(env)
        if not code and real:
            try:
                code = normalize(locale.getlocale()[0])
            except Exception:
                code = None
    return code or DEFAULT


_current = DEFAULT
_tables: dict = {}


def load(code: str) -> dict:
    if code not in _tables:
        f = locales_dir() / f"{code}.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        _tables[code] = {k: v for k, v in data.items() if not k.startswith("_")}
    return _tables[code]


def set_language(code: Optional[str]) -> str:
    """None or 'auto' means follow the system."""
    global _current
    if not code or code == "auto":
        code = detect()
    _current = code if code in LANGS else DEFAULT
    return _current


def language() -> str:
    return _current


def t(key: str, **kw) -> str:
    table = load(_current)
    s = table.get(key)
    if s is None:                                       # missing key: English, then the key
        s = load(DEFAULT).get(key, key)
    try:
        return s.format(**kw) if kw else s
    except (KeyError, IndexError):
        return s
