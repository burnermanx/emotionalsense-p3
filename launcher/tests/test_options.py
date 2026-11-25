"""Run: python3 -m unittest discover launcher/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_i18n  # noqa: E402
import es_main  # noqa: E402


def env_for(**over):
    cfg = dict(es_main.CONFIG_DEFAULTS)
    cfg.update(over)
    es_i18n.set_language("pt-BR")
    return es_main.game_command(Path("/x/game"), Path("/d"), cfg)[1]


class GameOptions(unittest.TestCase):
    def test_defaults(self):
        env = env_for()
        self.assertEqual(env["EMOTIONALSENSE_FEVER"], "respect")      # Respect is the default
        self.assertEqual(env["EMOTIONALSENSE_FULLSCREEN"], "auto")    # the game remembers the last mode
        self.assertNotIn("EMOTIONALSENSE_REFRESH", env)               # no rate chosen: borderless
        self.assertEqual(env["EMOTIONALSENSE_LANG"], "pt-BR")         # the game's own prompts follow the launcher

    def test_fever_original_and_start_mode(self):
        env = env_for(fever="original", start_mode="fullscreen")
        self.assertEqual(env["EMOTIONALSENSE_FEVER"], "original")
        self.assertEqual(env["EMOTIONALSENSE_FULLSCREEN"], "1")
        self.assertEqual(env_for(start_mode="window")["EMOTIONALSENSE_FULLSCREEN"], "0")

    def test_bad_fever_value_falls_back_to_respect(self):
        self.assertEqual(env_for(fever="nonsense")["EMOTIONALSENSE_FEVER"], "respect")

    def test_score_default_and_original(self):
        self.assertEqual(env_for()["EMOTIONALSENSE_SCORE"], "respect")
        self.assertEqual(env_for(score="original")["EMOTIONALSENSE_SCORE"], "original")
        self.assertEqual(env_for(score="nonsense")["EMOTIONALSENSE_SCORE"], "respect")

    def test_speed_default_and_original(self):
        self.assertEqual(env_for()["EMOTIONALSENSE_SPEED"], "respect")
        self.assertEqual(env_for(speed="original")["EMOTIONALSENSE_SPEED"], "original")
        self.assertEqual(env_for(speed="nonsense")["EMOTIONALSENSE_SPEED"], "respect")

    def test_button_style_and_triggers(self):
        self.assertEqual(env_for()["EMOTIONALSENSE_BUTTONS"], "auto")
        self.assertEqual(env_for(button_style="switch")["EMOTIONALSENSE_BUTTONS"], "switch")
        self.assertEqual(env_for(button_style="nonsense")["EMOTIONALSENSE_BUTTONS"], "auto")
        self.assertEqual(env_for()["EMOTIONALSENSE_TRIGGERS_2T"], "stick")
        self.assertEqual(env_for(triggers_2t="lr")["EMOTIONALSENSE_TRIGGERS_2T"], "lr")
        self.assertEqual(env_for(triggers_2t="nonsense")["EMOTIONALSENSE_TRIGGERS_2T"], "stick")

    def test_chosen_rate_is_passed(self):
        self.assertEqual(env_for(refresh="120")["EMOTIONALSENSE_REFRESH"], "120")
        self.assertEqual(env_for(refresh="auto")["EMOTIONALSENSE_REFRESH"], "auto")


if __name__ == "__main__":
    unittest.main()
