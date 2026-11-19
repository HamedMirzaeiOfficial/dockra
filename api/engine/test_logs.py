import gzip
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from engine import logs

REAL_POPEN = subprocess.Popen


class ArchiveTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch("engine.logs.app_dir", side_effect=lambda slug: self.tmp / slug)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_archive_is_real_gzip(self):
        fake = lambda args, **kw: REAL_POPEN(["sh", "-c", "echo one; echo two >&2"], **kw)
        with mock.patch("engine.logs.subprocess.Popen", side_effect=fake):
            path = logs.archive_container("shop", "abcdef1234567890", "r1")
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes()[:2], b"\x1f\x8b")
        self.assertEqual(sorted(logs.iter_lines(path)), ["one", "two"])

    def test_reads_legacy_broken_archive(self):
        folder = self.tmp / "shop"
        folder.mkdir()
        path = folder / "20260101T000000Z-r1-abc.log.gz"
        with gzip.open(path, "wb") as handle:
            subprocess.run(["sh", "-c", "echo line 1; echo line 2"], stdout=handle)
        self.assertEqual(list(logs.iter_lines(path)), ["line 1", "line 2"])
        self.assertEqual(len(logs.search("shop", "line 2", "")["matches"]), 1)

    def test_truncated_gzip_does_not_raise(self):
        folder = self.tmp / "shop"
        folder.mkdir()
        path = folder / "x.log.gz"
        data = "".join(f"line {i} {i * 7919 % 104729}\n" for i in range(5000)).encode()
        path.write_bytes(gzip.compress(data)[:4000])
        lines = list(logs.iter_lines(path))
        self.assertIn("truncated", lines[-1])

    def test_search_skips_unreadable_archive(self):
        folder = self.tmp / "shop"
        folder.mkdir()
        good = folder / "20260102T000000Z-r2-abc.log.gz"
        good.write_bytes(gzip.compress(b"needle here\n"))
        (folder / "20260101T000000Z-r1-abc.log.gz").write_bytes(b"x")
        with mock.patch("engine.logs._legacy_text", side_effect=[None, PermissionError("denied")]):
            found = logs.search("shop", "needle", "current needle")
        self.assertEqual([m["source"] for m in found["matches"]], ["current", good.name])
