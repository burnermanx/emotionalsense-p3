"""Run: python3 -m unittest discover launcher/tests"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import es_prepare  # noqa: E402
from es_pipeline import PipelineError  # noqa: E402


def make_tpa(path: Path, hashes, payload=b"x" * 16):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(hashes)
    off = 12 + 16 * n
    table = b"".join(struct.pack("<IIII", h, len(payload), off + i * len(payload), 0) for i, h in enumerate(hashes))
    body = payload * n
    path.write_bytes(struct.pack("<III", 0x0004C0DA, n << 16, len(table) + len(body)) + table + body)


class Update(unittest.TestCase):
    def test_install_find_and_remove(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            data = td / "data"
            make_tpa(data / "disc/PSP_GAME/INSDIR/DATA.TPA", [1, 2, 3])
            make_tpa(td / "upd/ULKS46236/PSP/GAME/ULKS46236/DATA.TPA", [2, 3])
            self.assertEqual(es_prepare.install_update(data, td / "upd"), 2)
            self.assertEqual(es_prepare.update_status(data), 2)
            es_prepare.remove_update(data)
            self.assertEqual(es_prepare.update_status(data), 0)

    def test_refuses_exe_and_foreign_files(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            data = td / "data"
            make_tpa(data / "disc/PSP_GAME/INSDIR/DATA.TPA", [1, 2])
            exe = td / "patch.exe"
            exe.write_bytes(b"MZ")
            with self.assertRaises(PipelineError):
                es_prepare.install_update(data, exe)
            make_tpa(td / "other/ULKS46236/DATA.TPA", [9])           # no hash in common
            with self.assertRaises(PipelineError):
                es_prepare.install_update(data, td / "other")
            bad = td / "bad/ULKS46236/DATA.TPA"
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"\0" * 64)
            with self.assertRaises(PipelineError):
                es_prepare.install_update(data, td / "bad")
            self.assertEqual(es_prepare.update_status(data), 0)


if __name__ == "__main__":
    unittest.main()
