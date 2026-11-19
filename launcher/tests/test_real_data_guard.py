"""Run: python3 -m unittest discover launcher/tests"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import es_pipeline  # noqa: E402


class RealDataGuard(unittest.TestCase):
    def test_detects_work_data(self):
        self.assertTrue(es_pipeline.is_repo_work_data("/x/repo/work/data"))
        self.assertTrue(es_pipeline.is_repo_work_data("/x/repo/work/data/"))
        self.assertFalse(es_pipeline.is_repo_work_data("/x/repo/work/data2"))
        self.assertFalse(es_pipeline.is_repo_work_data("/home/u/.local/share/emotionalsense"))

    def test_refuses_without_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EMOTIONALSENSE_ALLOW_REAL_DATA", None)
            with self.assertRaises(SystemExit):
                es_pipeline.refuse_real_data("/x/repo/work/data")
            es_pipeline.refuse_real_data("/tmp/scratch")

    def test_override(self):
        with mock.patch.dict(os.environ, {"EMOTIONALSENSE_ALLOW_REAL_DATA": "1"}):
            es_pipeline.refuse_real_data("/x/repo/work/data")


if __name__ == "__main__":
    unittest.main()
