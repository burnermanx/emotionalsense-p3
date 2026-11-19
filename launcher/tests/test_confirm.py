"""Run: python3 -m unittest discover launcher/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import es_main  # noqa: E402
import es_i18n  # noqa: E402


class Confirm(unittest.TestCase):
    def test_auto_follows_the_language(self):
        expected = {"ja": "circle", "ko": "circle", "pt-BR": "cross", "en": "cross", "es": "cross"}
        self.assertEqual(set(expected), set(es_i18n.LANGS))
        for lang, want in expected.items():
            self.assertEqual(es_main.resolve_confirm("auto", lang), want, lang)

    def test_future_language_defaults_to_cross(self):
        self.assertEqual(es_main.resolve_confirm("auto", "fr"), "cross")

    def test_explicit_wins(self):
        self.assertEqual(es_main.resolve_confirm("circle", "en"), "circle")
        self.assertEqual(es_main.resolve_confirm("cross", "ja"), "cross")

    def test_game_command_passes_the_resolved_value(self):
        es_i18n.set_language("ko")
        argv, env = es_main.game_command(Path("/x/game"), Path("/d"), dict(es_main.CONFIG_DEFAULTS))
        self.assertEqual(argv[argv.index("--confirm") + 1], "circle")
        self.assertEqual(env["EMOTIONALSENSE_CONFIRM"], "circle")
        argv, env = es_main.game_command(Path("/x/game"), Path("/d"), dict(es_main.CONFIG_DEFAULTS), confirm="cross")
        self.assertEqual(env["EMOTIONALSENSE_CONFIRM"], "cross")
        es_i18n.set_language("en")


class Pad(unittest.TestCase):
    def test_pad_without_a_controller_is_silent(self):
        import es_pad
        p = es_pad.Pad(None)
        self.assertEqual(p.poll(), [])
        p.close()


if __name__ == "__main__":
    unittest.main()
