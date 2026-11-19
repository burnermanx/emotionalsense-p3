"""The environment of the programs the launcher starts has none of the PyInstaller launcher's
folders (`_internal`), so the system's libraries are what the game and its Vulkan driver/layers
load. Regression test for the Steam Deck abort (Gamescope WSI layer)."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_env  # noqa: E402
import es_main  # noqa: E402

APP = "/opt/EmotionalSense-P3"
MEI = APP + "/_internal"


def frozen():
    return (mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "_MEIPASS", MEI, create=True),
            mock.patch.object(sys, "executable", APP + "/EmotionalSense-P3"))


class ChildEnv(unittest.TestCase):
    def run_env(self, env):
        p1, p2, p3 = frozen()
        with p1, p2, p3:
            return es_env.child_env(env)

    def test_restores_the_saved_system_value(self):
        env = self.run_env({"LD_LIBRARY_PATH": MEI + ":/usr/lib/steam", "LD_LIBRARY_PATH_ORIG": "/usr/lib/steam"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/lib/steam")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", env)

    def test_empty_saved_value_means_unset(self):
        env = self.run_env({"LD_LIBRARY_PATH": MEI, "LD_LIBRARY_PATH_ORIG": ""})
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_without_saved_value_our_folders_are_dropped(self):
        env = self.run_env({"LD_LIBRARY_PATH": MEI + ":/x/lib:" + APP + "/_internal/numpy.libs"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/x/lib")
        self.assertNotIn("LD_LIBRARY_PATH", self.run_env({"LD_LIBRARY_PATH": MEI}))

    def test_runtime_variables(self):
        env = self.run_env({"TCL_LIBRARY": MEI + "/tcl8.6", "TK_LIBRARY": MEI + "/tk8.6", "PYTHONHOME": "/home/u/mine",
                            "_PYI_APPLICATION_HOME_DIR": MEI, "_PYI_ARCHIVE_FILE": "x", "_MEIPASS2": MEI, "HOME": "/h"})
        for k in ("TCL_LIBRARY", "TK_LIBRARY", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_MEIPASS2"):
            self.assertNotIn(k, env)
        self.assertEqual(env["PYTHONHOME"], "/home/u/mine")      # the user's own is not ours to remove
        self.assertEqual(env["HOME"], "/h")

    def test_plain_environment_is_unchanged(self):
        base = {"LD_LIBRARY_PATH": "/a:/b", "PATH": "/usr/bin", "LD_PRELOAD": "gameoverlayrenderer.so"}
        self.assertEqual(es_env.child_env(base), base)


class GameCommand(unittest.TestCase):
    def test_game_env_has_no_internal(self):
        p1, p2, p3 = frozen()
        base = {"LD_LIBRARY_PATH": MEI + ":/usr/lib/steam", "LD_LIBRARY_PATH_ORIG": "/usr/lib/steam",
                "TCL_LIBRARY": MEI + "/tcl8.6", "PATH": "/usr/bin"}
        with p1, p2, p3, mock.patch.dict(os.environ, base, clear=True):
            argv, env = es_main.game_command(Path("/data/game/EmotionalSense-P3-game"), Path("/data"), dict(es_main.CONFIG_DEFAULTS))
        self.assertEqual(env["LD_LIBRARY_PATH"], "/data/game:/usr/lib/steam")
        self.assertFalse(any("_internal" in v for v in env.values()), env)
        self.assertNotIn("TCL_LIBRARY", env)

    def test_game_env_without_system_value(self):
        p1, p2, p3 = frozen()
        with p1, p2, p3, mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": MEI, "LD_LIBRARY_PATH_ORIG": ""}, clear=True):
            argv, env = es_main.game_command(Path("/data/game/g"), Path("/data"), dict(es_main.CONFIG_DEFAULTS))
        self.assertEqual(env["LD_LIBRARY_PATH"], "/data/game")


S = "/home/u/.local/share/Steam"
RT = S + "/ubuntu12_32/steam-runtime"


class SteamEnv(unittest.TestCase):
    """What Steam gives a non-Steam shortcut in Game Mode must not reach the game's Vulkan start."""
    BASE = {"LD_LIBRARY_PATH": ":".join([S + "/ubuntu12_32", S + "/ubuntu12_64", RT + "/pinned_libs_32", RT + "/pinned_libs_64",
                                         "/usr/lib32", "/usr/lib", RT + "/lib/x86_64-linux-gnu", RT + "/usr/lib"]),
            "LD_PRELOAD": S + "/ubuntu12_32/gameoverlayrenderer.so:" + S + "/ubuntu12_64/gameoverlayrenderer.so",
            "SteamDeck": "1", "ENABLE_GAMESCOPE_WSI": "1", "PATH": "/usr/bin"}

    def test_steam_runtime_and_overlay_are_dropped_for_the_game(self):
        env = es_env.game_env(self.BASE)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/lib32:/usr/lib")
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env["SteamDeck"], "1")                       # only the library injection goes
        self.assertEqual(env["ENABLE_GAMESCOPE_WSI"], "1")

    def test_other_preloads_stay(self):
        env = es_env.game_env({"LD_PRELOAD": S + "/ubuntu12_64/gameoverlayrenderer.so /usr/lib/libmangohud.so"})
        self.assertEqual(env["LD_PRELOAD"], "/usr/lib/libmangohud.so")

    def test_only_steam_folders_go(self):
        env = es_env.game_env({"LD_LIBRARY_PATH": "/opt/mine/ubuntu12:/usr/lib/steam:/data/game:" + RT})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/mine/ubuntu12:/usr/lib/steam:/data/game")

    def test_all_steam_means_unset(self):
        self.assertNotIn("LD_LIBRARY_PATH", es_env.game_env({"LD_LIBRARY_PATH": S + "/ubuntu12_64:" + RT + "/lib"}))

    def test_keep_switch(self):
        env = es_env.game_env(dict(self.BASE, EMOTIONALSENSE_KEEP_STEAM_ENV="1"))
        self.assertIn("steam-runtime", env["LD_LIBRARY_PATH"])
        self.assertIn("gameoverlayrenderer", env["LD_PRELOAD"])

    def test_frozen_launcher_under_steam(self):
        p1, p2, p3 = frozen()
        base = dict(self.BASE, LD_LIBRARY_PATH=MEI + ":" + self.BASE["LD_LIBRARY_PATH"],
                    LD_LIBRARY_PATH_ORIG=self.BASE["LD_LIBRARY_PATH"])
        with p1, p2, p3:
            env = es_env.game_env(base)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/lib32:/usr/lib")

    def test_game_command_uses_it(self):
        with mock.patch.dict(os.environ, self.BASE, clear=True):
            argv, env = es_main.game_command(Path("/data/game/g"), Path("/data"), dict(es_main.CONFIG_DEFAULTS))
        self.assertEqual(env["LD_LIBRARY_PATH"], "/data/game:/usr/lib32:/usr/lib")
        self.assertNotIn("LD_PRELOAD", env)


if __name__ == "__main__":
    unittest.main()
