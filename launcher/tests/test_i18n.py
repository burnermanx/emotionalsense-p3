"""Run: python3 -m unittest discover launcher/tests"""
import json
import re
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_i18n  # noqa: E402

LOC = Path(__file__).resolve().parent.parent / "locales"


def table(code):
    return json.loads((LOC / f"{code}.json").read_text(encoding="utf-8"))


class Keys(unittest.TestCase):
    def test_same_keys_everywhere(self):
        ref = {k for k in table("en") if not k.startswith("_")}
        for code in es_i18n.LANGS:
            keys = {k for k in table(code) if not k.startswith("_")}
            self.assertEqual(ref - keys, set(), f"{code}: missing keys")
            self.assertEqual(keys - ref, set(), f"{code}: extra keys")

    def test_same_placeholders(self):
        en = table("en")
        for code in es_i18n.LANGS:
            for k, v in table(code).items():
                if k.startswith("_"):
                    continue
                self.assertEqual(set(re.findall(r"\{(\w+)\}", v)),
                                 set(re.findall(r"\{(\w+)\}", en[k])), f"{code}:{k}")
                self.assertTrue(v.strip(), f"{code}:{k} is empty")

    def test_unreviewed_translations_are_marked(self):
        for code in ("es", "ja", "ko"):
            self.assertIn("NEEDS NATIVE REVIEW", table(code)["_note"])


class Detect(unittest.TestCase):
    def d(self, **env):
        return es_i18n.detect(env, "linux")

    def test_linux_variables(self):
        self.assertEqual(self.d(LANG="ja_JP.UTF-8"), "ja")
        self.assertEqual(self.d(LANG="ko_KR.UTF-8"), "ko")
        self.assertEqual(self.d(LANG="pt_PT.UTF-8"), "pt-BR")
        self.assertEqual(self.d(LANG="pt_BR.UTF-8"), "pt-BR")
        self.assertEqual(self.d(LANG="es_MX.UTF-8"), "es")
        self.assertEqual(self.d(LANG="C"), "en")
        self.assertEqual(self.d(LANG="POSIX"), "en")
        self.assertEqual(self.d(LANG="de_DE.UTF-8"), "en")
        self.assertEqual(self.d(), "en")

    def test_priority(self):
        self.assertEqual(self.d(LANGUAGE="ko:en", LANG="ja_JP.UTF-8"), "ko")
        self.assertEqual(self.d(LC_ALL="es_ES.UTF-8", LANG="ja_JP.UTF-8"), "es")
        self.assertEqual(self.d(LC_MESSAGES="ja_JP.UTF-8", LANG="ko_KR.UTF-8"), "ja")
        self.assertEqual(self.d(LANGUAGE="fr:pt_BR"), "pt-BR")

    def test_normalize(self):
        for tag, want in [("pt-BR", "pt-BR"), ("pt-PT", "pt-BR"), ("es-419", "es"),
                          ("ja-JP", "ja"), ("ko-KR", "ko"), ("en-GB", "en"),
                          ("zh-CN", None), ("C", None), ("", None)]:
            self.assertEqual(es_i18n.normalize(tag), want, tag)


class Lookup(unittest.TestCase):
    def test_format_and_fallback(self):
        es_i18n.set_language("pt-BR")
        self.assertIn("X", es_i18n.t("welcome.ok", title="X", id="Y"))
        self.assertEqual(es_i18n.t("no.such.key"), "no.such.key")
        es_i18n.set_language("auto")




class SteamGameMode(unittest.TestCase):
    """Game Mode (gamescope) keeps LANG at the system default; the Steam client's language wins.
    Synthetic files in temp dirs only; the real Steam folders are never read."""
    VDF = ('"Registry"\n{\n\t"HKCU"\n\t{\n\t\t"Software"\n\t\t{\n\t\t\t"Valve"\n\t\t\t{\n\t\t\t\t"Steam"\n\t\t\t\t{\n'
           '\t\t\t\t\t"language"\t\t"%s"\n\t\t\t\t}\n\t\t\t}\n\t\t}\n\t}\n}\n')
    GAME = {"LANG": "en_US.UTF-8", "SteamDeck": "1", "XDG_CURRENT_DESKTOP": "gamescope"}

    def home(self, lang=None, raw=None, plasma=None):
        import tempfile
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        h = Path(td.name)
        if lang is not None or raw is not None:
            (h / ".steam").mkdir()
            (h / ".steam/registry.vdf").write_text(raw if raw is not None else self.VDF % lang)
        if plasma:
            (h / ".config").mkdir()
            (h / ".config/plasma-localerc").write_text(plasma)
        return h

    def test_each_steam_language(self):
        for name, code in (("brazilian", "pt-BR"), ("portuguese", "pt-BR"), ("english", "en"),
                           ("spanish", "es"), ("latam", "es"), ("japanese", "ja"), ("koreana", "ko")):
            self.assertEqual(es_i18n.detect(self.GAME, "linux", self.home(name)), code, name)

    def test_session_detection(self):
        for env in ({"SteamDeck": "1"}, {"SteamGamepadUI": "1"}, {"SteamOS": "1"},
                    {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}, {"XDG_CURRENT_DESKTOP": "gamescope"},
                    {"SteamAppId": "1"}):
            self.assertTrue(es_i18n.steam_session(env), env)
        self.assertFalse(es_i18n.steam_session({"XDG_CURRENT_DESKTOP": "KDE", "LANG": "pt_BR.UTF-8"}))

    def test_desktop_mode_ignores_steam_file(self):
        env = {"LANG": "pt_BR.UTF-8", "XDG_CURRENT_DESKTOP": "KDE"}
        self.assertEqual(es_i18n.detect(env, "linux", self.home("japanese")), "pt-BR")

    def test_manual_choice_wins(self):
        es_i18n.set_language("ko")                      # a saved choice never reaches detect()
        self.assertEqual(es_i18n.language(), "ko")
        es_i18n.set_language("en")

    def test_fallbacks(self):
        env = dict(self.GAME, LANG="es_ES.UTF-8")
        self.assertEqual(es_i18n.detect(env, "linux", self.home()), "es")                    # no file
        self.assertEqual(es_i18n.detect(env, "linux", self.home(raw="\x00{{ garbage")), "es")  # corrupt
        self.assertEqual(es_i18n.detect(env, "linux", self.home("german")), "es")            # unsupported
        self.assertEqual(es_i18n.detect(env, "linux", self.home(plasma="[Formats]\nLANG=ja_JP.UTF-8\n")), "ja")
        self.assertEqual(es_i18n.detect(self.GAME, "linux", self.home()), "en")

    def test_steam_beats_plasma(self):
        h = self.home("brazilian", plasma="[Translations]\nLANGUAGE=ko\n")
        self.assertEqual(es_i18n.detect(self.GAME, "linux", h), "pt-BR")

class ProtonLanguage(SteamGameMode):
    """The Windows build under Wine/Proton: host variables reach the process, the host
    home is read through a Unix root (Z:\\ in real life, a temp dir here)."""
    PROTON = {"LANG": "en_US.UTF-8", "SteamAppId": "123", "HOME": "/home/deck",
              "STEAM_COMPAT_CLIENT_INSTALL_PATH": "/home/deck/.local/share/Steam"}

    def root(self, lang=None, **kw):
        r = self.home()
        (r / "home/deck").mkdir(parents=True)
        h = r / "home/deck"
        if lang is not None or kw:
            (h / ".steam").mkdir()
            (h / ".steam/registry.vdf").write_text(kw.get("raw") or self.VDF % lang)
        if kw.get("plasma"):
            (h / ".config").mkdir()
            (h / ".config/plasma-localerc").write_text(kw["plasma"])
        return r

    def det(self, env, root, wine=True):
        with mock.patch.object(es_i18n, "_windows", return_value="en"):
            return es_i18n.detect(env, "win32", wine=wine, unix_root=root)

    def test_registry_through_unix_root(self):
        for name, code in (("brazilian", "pt-BR"), ("koreana", "ko"), ("latam", "es")):
            self.assertEqual(self.det(self.PROTON, self.root(name)), code)

    def test_home_from_compat_path_only(self):
        env = {k: v for k, v in self.PROTON.items() if k != "HOME"}
        self.assertEqual(self.det(env, self.root("japanese")), "ja")

    def test_wine_host_variables(self):
        env = {"LANG": "en_US.UTF-8", "WINE_HOST_HOME": "/home/deck", "WINE_HOST_XDG_CURRENT_DESKTOP": "gamescope"}
        self.assertEqual(self.det(env, self.root("koreana")), "ko")
        env = {"LANG": "en_US.UTF-8", "WINE_HOST_HOME": "/home/deck", "WINE_HOST_XDG_CURRENT_DESKTOP": "KDE"}
        self.assertEqual(self.det(env, self.root("koreana")), "en")

    def test_upper_cased_names_like_windows(self):
        env = {k.upper(): v for k, v in self.PROTON.items()}
        self.assertEqual(self.det(env, self.root("brazilian")), "pt-BR")

    def test_env_language_wins(self):
        env = dict(self.PROTON, SteamGameLanguage="spanish")
        self.assertEqual(self.det(env, self.root("brazilian")), "es")

    def test_plasma_then_wine_locale(self):
        self.assertEqual(self.det(self.PROTON, self.root(plasma="[Formats]\nLANG=pt_BR.UTF-8\n")), "pt-BR")
        self.assertEqual(self.det(self.PROTON, self.root()), "en")
        with mock.patch.object(es_i18n, "_windows", return_value="ko"):
            self.assertEqual(es_i18n.detect(self.PROTON, "win32", wine=True, unix_root=self.root()), "ko")

    def test_real_windows_and_no_steam_unchanged(self):
        self.assertEqual(self.det(self.PROTON, self.root("brazilian"), wine=False), "en")
        env = {"LANG": "en_US.UTF-8", "HOME": "/home/deck"}
        self.assertEqual(self.det(env, self.root("brazilian")), "en")

    def test_corrupt_falls_back(self):
        self.assertEqual(self.det(self.PROTON, self.root(raw="\x00{{")), "en")

    def test_under_wine_matches_host(self):
        import ctypes
        if sys.platform.startswith("win"):               # real Windows: False; Wine/Proton: True
            self.assertEqual(es_i18n.under_wine(), hasattr(ctypes.windll.ntdll, "wine_get_version"))
        else:
            self.assertFalse(es_i18n.under_wine())


if __name__ == "__main__":
    unittest.main()
