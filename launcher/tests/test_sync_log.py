"""K3: the collection mode writes a scrubbed log under <data>/logs and sets only the cheap traces."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_main  # noqa: E402


class SyncLog(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EMOTIONALSENSE_SYNC_LOG", None)
            self.assertFalse(es_main.sync_log_wanted())
            self.assertTrue(es_main.sync_log_wanted(True))
        with mock.patch.dict(os.environ, {"EMOTIONALSENSE_SYNC_LOG": "1"}):
            self.assertTrue(es_main.sync_log_wanted())
        with mock.patch.dict(os.environ, {"EMOTIONALSENSE_SYNC_LOG": "0"}):
            self.assertFalse(es_main.sync_log_wanted())

    def test_traces_are_the_cheap_ones(self):
        self.assertNotIn("EMOTIONALSENSE_TRACE_FPS", es_main.SYNC_ENV)   # per-frame hashing: heavy on a small CPU
        self.assertIn("EMOTIONALSENSE_TRACE_CLOCK", es_main.SYNC_ENV)
        self.assertIn("EMOTIONALSENSE_TRACE_AUDIO", es_main.SYNC_ENV)

    def test_writes_and_scrubs(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            home = str(Path.home())
            script = ("import os,sys; print('clock[60]: x', os.environ['EMOTIONALSENSE_TRACE_CLOCK']); "
                      "print(os.environ['ES_TEST_HOME'] + '/secret', file=sys.stderr)")
            p = es_main.spawn_game([sys.executable, "-c", script], dict(os.environ, ES_TEST_HOME=home), data, sync_log=True)
            p.wait()
            es_main.finish_game(p)
            files = list((data / "logs").glob("sync-*.log"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            self.assertIn("# EmotionalSense-P3 sync log", text)
            self.assertIn("clock[60]: x 1", text)
            self.assertNotIn(home, text)

    def test_no_file_when_off(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("EMOTIONALSENSE_SYNC_LOG", None)
                p = es_main.spawn_game([sys.executable, "-c", "pass"], dict(os.environ), Path(d))
                p.wait()
            es_main.finish_game(p)
            self.assertFalse((Path(d) / "logs").exists())


if __name__ == "__main__":
    unittest.main()
