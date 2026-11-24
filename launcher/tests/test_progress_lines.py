"""--progress-lines: the window-less preparation speaks RecompLauncher's `progress <0..1> <text>` lines."""
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_i18n  # noqa: E402
import es_main  # noqa: E402

LINE = re.compile(r"^progress (0|1)\.\d{3}( .+)?$")


class ProgressLines(unittest.TestCase):
    def setUp(self):
        es_i18n.set_language("en")

    def lines(self, out):
        return out.getvalue().splitlines()

    def test_format_and_step_text(self):
        out = io.StringIO()
        p = es_main.ProgressLines(out)
        p.step("verify")
        p.progress(0.0)
        p.progress(0.03)
        got = self.lines(out)
        self.assertEqual(got[0], "progress 0.000 Checking the disc")
        for ln in got:
            self.assertRegex(ln, LINE)

    def test_throttled_but_always_prints_a_new_step_and_the_end(self):
        out = io.StringIO()
        p = es_main.ProgressLines(out)
        p.step("compile")
        for i in range(1001):                       # 1001 callbacks from 0.4 to 1.0
            p.progress(0.4 + 0.6 * i / 1000)
        got = self.lines(out)
        self.assertLess(len(got), 140)               # about one line per half percent, not a thousand
        self.assertEqual(got[-1], "progress 1.000 Compiling for this computer")
        p.step("done")
        p.progress(1.0)                              # a new step at 1.0 prints once more
        self.assertEqual(len(self.lines(out)), len(got) + 1)

    def test_already_prepared_prints_done_and_exits_0(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with mock.patch("es_prepare.Preparer.is_prepared", return_value=True), \
                    mock.patch("es_portable.apply_env"), mock.patch("sys.stdout", out):
                code = es_main.main(["--prepare", "--progress-lines", "--data", d, "--lang", "en"])
            self.assertEqual(code, 0)
            self.assertEqual(self.lines(out), ["progress 1.000 Ready"])

    def test_without_the_flag_nothing_is_printed(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with mock.patch("es_prepare.Preparer.is_prepared", return_value=True), \
                    mock.patch("es_portable.apply_env"), mock.patch("sys.stdout", out):
                code = es_main.main(["--prepare", "--data", d, "--lang", "en"])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
