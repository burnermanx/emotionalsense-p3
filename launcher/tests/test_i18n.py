"""Run: python3 -m unittest discover launcher/tests"""
import json
import re
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
