"""Run: python3 -m unittest discover launcher/tests"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_main  # noqa: E402


class ScrubTest(unittest.TestCase):
    def test_paths(self):
        cases = {
            "/home/alice/x": "/home/~/x",
            "Z:/home/alice/x": "Z:/home/~/x",
            "Z:\\home\\alice\\x": "Z:/home/~\\x",
            "C:\\Users\\Alice Smith\\Desktop": "C:/Users/~\\Desktop",
            "C:/users/alice/x": "C:/users/~/x",
            "/Users/alice": "/Users/~",
            "D:\\Games\\ES": "D:\\Games\\ES",
            "/opt/homework/x": "/opt/homework/x",
        }
        for src, want in cases.items():
            self.assertEqual(es_main.scrub_paths(src), want, src)

    def test_exact_home(self):
        with mock.patch.object(Path, "home", return_value=Path("/srv/bob")):
            self.assertEqual(es_main.scrub_paths("/srv/bob/data"), "~/data")

    def test_issue_url_has_no_user(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / "crash-1.txt"
            r.write_text("kind: segv\ndetail: at /home/alice/g\nversion: 0.1\nos: x\ngpu: y\n")
            url = es_main.issue_url(r)
            self.assertNotIn("alice", url)


if __name__ == "__main__":
    unittest.main()
