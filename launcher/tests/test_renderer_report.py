"""The launcher's side of the Vulkan start-up report (<data>/logs/renderer.log, written by the game
on every start): when to warn, what the issue link carries, and that the dialog texts exist."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_i18n  # noqa: E402
import es_main  # noqa: E402

HEAD = "EmotionalSense-P3 renderer report\nversion: 0.1.0 (psprecomp abc)\ntime: 2026-11-19 13:00:00\nrequested: auto\nresult: %s\n"


class Report(unittest.TestCase):
    def write(self, result, age=0.0):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / "logs").mkdir()
        p = d / "logs" / "renderer.log"
        p.write_text(HEAD % result + "env LD_LIBRARY_PATH=~/x\nvulkan: start\n")
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return d, p

    def test_vulkan_ok_is_silent(self):
        d, _ = self.write("vulkan")
        self.assertIsNone(es_main.renderer_report(d, time.time() - 60))

    def test_software_choice_is_silent(self):
        for r in ("software", "null"):
            d, _ = self.write(r)
            self.assertIsNone(es_main.renderer_report(d, time.time() - 60))

    def test_fallback_warns(self):
        d, p = self.write("software-fallback (vulkan did not start)")
        found = es_main.renderer_report(d, time.time() - 60)
        self.assertEqual(found[0], p)
        self.assertTrue(found[1].startswith("software-fallback"))

    def test_strict_failure_warns(self):
        d, p = self.write("failed (vulkan requested, no fallback)")
        self.assertEqual(es_main.renderer_report(d, time.time() - 60)[0], p)

    def test_an_old_report_is_not_this_run(self):
        d, _ = self.write("software-fallback (vulkan did not start)", age=3600)
        self.assertIsNone(es_main.renderer_report(d, time.time() - 60))

    def test_missing_or_garbled(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.assertIsNone(es_main.renderer_report(d))
        (d / "logs").mkdir()
        (d / "logs" / "renderer.log").write_bytes(b"\xff\xfe nonsense")
        self.assertIsNone(es_main.renderer_report(d))

    def test_issue_url_carries_no_home(self):
        d, p = self.write("software-fallback (vulkan did not start)")
        home = str(Path.home())
        p.write_text(HEAD % "software-fallback (x)" + "")
        url = es_main.renderer_issue_url(p, "software-fallback (vulkan did not start)")
        self.assertTrue(url.startswith(es_main.ISSUES_URL))
        q = parse_qs(urlparse(url).query)
        self.assertIn("vulkan", q["title"][0])
        self.assertIn("renderer.log", q["body"][0])
        self.assertNotIn(home, url)

    def test_dialog_texts_in_every_language(self):
        for code in es_i18n.LANGS:
            tab = json.loads((Path(es_main.__file__).parent / "locales" / f"{code}.json").read_text(encoding="utf-8"))
            self.assertTrue(tab["renderer.title"].strip(), code)
            self.assertIn("{file}", tab["renderer.body"], code)
            self.assertIn("{folder}", tab["renderer.body"], code)


if __name__ == "__main__":
    unittest.main()
