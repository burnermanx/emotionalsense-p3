"""RecompLauncher support (round 37): recomplauncher.json settings, Big Picture start, prepared.json marker."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_main  # noqa: E402
import es_prepare  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VALUES = {"fever": es_main.FEVER_VALUES, "score": es_main.SCORE_VALUES, "speed": es_main.SPEED_VALUES,
          "confirm": es_main.CONFIRM_VALUES, "refresh": es_main.REFRESH_VALUES,
          "button_style": es_main.BUTTON_STYLE_VALUES, "triggers_2t": es_main.TRIGGERS_2T_VALUES,
          "start_mode": es_main.START_MODE_VALUES, "bga_smooth": es_main.BGA_VALUES}


class Manifest(unittest.TestCase):
    def setUp(self):
        self.m = json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8"))
        self.by_id = {s["id"]: s for s in self.m["settings"]}

    def test_button_style_and_triggers_are_settings(self):
        for sid in ("button_style", "triggers_2t"):
            self.assertIn(sid, self.by_id)

    def test_every_choice_is_a_value_the_launcher_accepts(self):
        for sid, vals in VALUES.items():
            if sid not in self.by_id:
                continue
            got = [json.loads(c["set"][sid]) for c in self.by_id[sid]["choices"]]
            for v in got:
                self.assertIn(v, vals, sid)
            self.assertEqual(json.loads(self.by_id[sid]["defaults"][sid]), es_main.CONFIG_DEFAULTS[sid], sid)

    def test_new_settings_cover_all_values(self):
        for sid in ("button_style", "triggers_2t"):
            got = {json.loads(c["set"][sid]) for c in self.by_id[sid]["choices"]}
            self.assertEqual(got, set(VALUES[sid]))


class BigPicture(unittest.TestCase):
    def fs(self, mode, rl_mode):
        cfg = dict(es_main.CONFIG_DEFAULTS, start_mode=mode)
        env = dict(os.environ)
        env.pop("RECOMPLAUNCHER_MODE", None)
        if rl_mode:
            env["RECOMPLAUNCHER_MODE"] = rl_mode
        with mock.patch.dict("os.environ", env, clear=True):
            return es_main.game_command(Path("/x/g"), Path("/d"), cfg)[1]["EMOTIONALSENSE_FULLSCREEN"]

    def test_last_in_big_picture_is_full_screen(self):
        self.assertEqual(self.fs("last", "bigpicture"), "1")

    def test_last_otherwise_stays_auto(self):
        self.assertEqual(self.fs("last", None), "auto")
        self.assertEqual(self.fs("last", "desktop"), "auto")

    def test_named_option_wins_over_big_picture(self):
        self.assertEqual(self.fs("window", "bigpicture"), "0")
        self.assertEqual(self.fs("fullscreen", "bigpicture"), "1")


class PreparedMarker(unittest.TestCase):
    def test_marker_is_removed_before_a_rebuild_and_stays_gone_if_it_is_interrupted(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "prepared.json").write_text(json.dumps({"launcher": {"version_full": "old"}, "iso": {"sha256": "a"}}))
            p = es_prepare.Preparer(es_prepare.PrepareOptions(iso=Path("x.iso"), data_dir=data, kit_dir=data))
            kit = mock.Mock()
            seen = []
            def boom(*a):
                seen.append((data / "prepared.json").exists())
                raise es_prepare.PipelineError("interrupted")
            with mock.patch.object(es_prepare.es_build, "Kit", return_value=kit), \
                 mock.patch.object(p, "verify", return_value={"sha256": "b"}), \
                 mock.patch.object(p, "extract", side_effect=boom), \
                 mock.patch.object(p, "launcher_stamp", return_value={"version_full": "new"}):
                with self.assertRaises(es_prepare.PipelineError):
                    p.run()
            self.assertEqual(seen, [False])
            self.assertFalse((data / "prepared.json").exists())

    def test_a_rejected_disc_keeps_the_marker(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "prepared.json").write_text("{}")
            p = es_prepare.Preparer(es_prepare.PrepareOptions(iso=Path("x.iso"), data_dir=data, kit_dir=data))
            with mock.patch.object(es_prepare.es_build, "Kit", return_value=mock.Mock()), \
                 mock.patch.object(p, "verify", side_effect=es_prepare.PipelineError("bad disc")):
                with self.assertRaises(es_prepare.PipelineError):
                    p.run()
            self.assertTrue((data / "prepared.json").exists())


if __name__ == "__main__":
    unittest.main()
