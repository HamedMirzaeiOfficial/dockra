import io
import shutil
import tarfile
import tempfile
import threading
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps_core.models import Disk, DiskBackup
from engine import disk_backup
from engine.models import Job

REMOTE = {
    "/media/a.jpg": b"A" * 1000,
    "/media/sub/b.txt": b"bbb",
    "/media/sub/deep/c.bin": b"\x00" * 5000,
}


class FakeFTP:
    """Fake FTP server with MLSD and RETR; counts downloads to test incremental transfers."""

    retrieved: list[str] = []
    lock = threading.Lock()

    def __init__(self, timeout=None):
        pass

    def connect(self, host, port):
        pass

    def login(self, user, password):
        if password != "secret":
            import ftplib

            raise ftplib.error_perm("530 Login incorrect")

    def set_pasv(self, value):
        pass

    def cwd(self, path):
        import ftplib

        path = path.rstrip("/") or "/"
        if path != "/" and not any(full.startswith(path + "/") for full in REMOTE):
            raise ftplib.error_perm("501 No such directory.")

    def mlsd(self, path, facts=None):
        path = path.rstrip("/") or "/"
        children = {}
        for full, data in REMOTE.items():
            if not full.startswith(path + "/"):
                continue
            rest = full[len(path) + 1:]
            head, _, tail = rest.partition("/")
            children[head] = {"type": "dir"} if tail else {"type": "file", "size": str(len(data)), "modify": "20260101000000"}
        return iter([(".", {"type": "cdir"}), *children.items()])

    def retrbinary(self, cmd, callback, blocksize=8192):
        path = cmd.split(" ", 1)[1]
        with self.lock:
            self.retrieved.append(path)
        callback(REMOTE[path])

    def quit(self):
        pass

    def close(self):
        pass


class PlainOnlyFTP(FakeFTP):
    """Like some hosting providers' FTP: advertises an "FTPS" port but rejects AUTH."""

    def auth(self):
        import ftplib

        raise ftplib.error_perm('500 Command "AUTH" not understood.')


def _tar(items: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in items.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _zip(items: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in items.items():
            archive.writestr(name, data)
    return buf.getvalue()


# Tests must not depend on the free space of the machine running them.
@override_settings(DOCKRA_RESERVED_DISK_GB=0)
class DiskImportTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(DOCKRA_DATA_ROOT=str(self.tmp), MEDIA_ROOT=self.tmp / "media", WORK_ROOT=self.tmp / "work")
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/app/media"}, format="json")
        self.disk = Disk.objects.get(name="media")
        self.root = disk_backup.disk_data(self.disk)
        FakeFTP.retrieved = []

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _upload(self, body: bytes, query=""):
        return self.client.generic("POST", f"/api/apps/shop/disks/media/import{query}", body, content_type="application/octet-stream")

    def _job(self, res):
        self.assertEqual(res.status_code, 202, res.content)
        return self.client.get(f"/api/jobs/{res.data['job']['id']}").data

    def test_tar_and_zip_archives(self):
        job = self._job(self._upload(_tar({"photos/1.jpg": b"one", "2.txt": b"two"})))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "photos" / "1.jpg").read_bytes(), b"one")
        job = self._job(self._upload(_zip({"docs/z.txt": b"zip"})))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "docs" / "z.txt").read_bytes(), b"zip")
        self.assertTrue((self.root / "2.txt").exists())
        self.assertEqual(list((self.tmp / "media" / "restore").iterdir()), [])

    def test_liara_disk_backup_folder_is_unwrapped(self):
        wrapped = "media-2026-10-14T16-08-14-1791981494855"
        for body in (_tar({f"{wrapped}/blog/t.png": b"png", f"{wrapped}/a.txt": b"a"}), _zip({f"{wrapped}/blog/z.png": b"z"})):
            job = self._job(self._upload(body))
            self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "blog" / "t.png").read_bytes(), b"png")
        self.assertEqual((self.root / "blog" / "z.png").read_bytes(), b"z")
        self.assertTrue((self.root / "a.txt").exists())
        self.assertFalse((self.root / wrapped).exists())

    def test_folder_named_like_the_disk_is_unwrapped(self):
        job = self._job(self._upload(_tar({"media/blog/m.png": b"m"})))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "blog" / "m.png").read_bytes(), b"m")
        job = self._job(self._upload(_tar({"photos/p.png": b"p"})))
        self.assertTrue((self.root / "photos" / "p.png").exists())

    def _pieces(self, body: bytes) -> str:
        upload_id = self.client.post("/api/uploads").data["upload"]
        for offset in range(0, len(body), 7):
            res = self.client.generic("PUT", f"/api/uploads/{upload_id}?offset={offset}", body[offset:offset + 7], content_type="application/octet-stream")
            self.assertEqual(res.status_code, 200, res.content)
        return upload_id

    def test_piecewise_uploads_for_import_and_file_manager(self):
        upload_id = self._pieces(_tar({"big/a.bin": b"x" * 50}))
        job = self._job(self.client.post("/api/apps/shop/disks/media/import", {"upload": upload_id}, format="json"))
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((self.root / "big" / "a.bin").read_bytes(), b"x" * 50)

        # A file under its own name, a tar archive that is extracted, and an empty file.
        upload_id = self._pieces(b"hello world, piece by piece")
        res = self.client.post(f"/api/apps/shop/disks/media/upload?path=/docs&upload={upload_id}&filename=note.txt")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.root / "docs" / "note.txt").read_bytes(), b"hello world, piece by piece")
        upload_id = self._pieces(_tar({"t/inner.txt": b"in"}))
        res = self.client.post(f"/api/apps/shop/disks/media/upload?path=/&upload={upload_id}")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.root / "t" / "inner.txt").read_bytes(), b"in")
        upload_id = self.client.post("/api/uploads").data["upload"]
        res = self.client.post(f"/api/apps/shop/disks/media/upload?path=/&upload={upload_id}&filename=empty.txt")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.root / "empty.txt").read_bytes(), b"")

        missing = self.client.post("/api/apps/shop/disks/media/import", {"upload": "c" * 32}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual([p.name for p in (self.tmp / "media" / "restore").iterdir()], [])

    def test_archives_cannot_escape_the_disk(self):
        for body in (_tar({"../evil.txt": b"x"}), _zip({"../../evil.txt": b"x"}), b"not an archive"):
            job = self._job(self._upload(body))
            self.assertEqual(job["status"], "failed")
        self.assertFalse(list(self.tmp.rglob("evil.txt")))

    def test_replace_keeps_a_safety_backup(self):
        (self.root / "old.txt").write_text("old")
        job = self._job(self._upload(_tar({"new.txt": b"new"}), "?replace=1"))
        self.assertEqual(job["status"], "done", job)
        self.assertFalse((self.root / "old.txt").exists())
        self.assertTrue((self.root / "new.txt").exists())
        self.assertTrue(DiskBackup.objects.filter(kind=DiskBackup.Kind.SAFETY, status=DiskBackup.Status.READY).exists())

    @mock.patch("engine.disk_import.ensure_public")
    @mock.patch("engine.disk_import.ftplib.FTP", FakeFTP)
    def test_ftp_mirror_is_parallel_incremental_and_forgets_password(self, _public):
        url = "ftp://liara:secret@ftp.example.com/media"
        res = self.client.post("/api/apps/shop/disks/media/import", {"ftp": url}, format="json")
        job = self._job(res)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["result"]["files"], 3)
        self.assertEqual((self.root / "sub" / "deep" / "c.bin").read_bytes(), b"\x00" * 5000)
        self.assertNotIn("secret", str(Job.objects.get(pk=job["id"]).payload))

        # Second run (final cut-over): only the new file is fetched.
        REMOTE["/media/new.png"] = b"new"
        try:
            FakeFTP.retrieved = []
            job = self._job(self.client.post("/api/apps/shop/disks/media/import", {"ftp": url}, format="json"))
            self.assertEqual(FakeFTP.retrieved, ["/media/new.png"])
            self.assertEqual(job["result"]["skipped"], 3)
        finally:
            REMOTE.pop("/media/new.png")

        bad = self._job(self.client.post("/api/apps/shop/disks/media/import", {"ftp": "ftp://liara:wrong@ftp.example.com/media"}, format="json"))
        self.assertEqual(bad["status"], "failed")
        self.assertIn("FTP", bad["log"])

    @mock.patch("engine.disk_import.ensure_public")
    @mock.patch("engine.disk_import.ftplib.FTP", FakeFTP)
    @mock.patch("engine.disk_import.ftplib.FTP_TLS", PlainOnlyFTP)
    def test_liara_ftps_without_tls_and_wrong_path(self, _public):
        job = self._job(self.client.post("/api/apps/shop/disks/media/import", {"ftp": "ftps://liara:secret@ftp.example.com:2112/media"}, format="json"))
        self.assertEqual(job["status"], "done", job)
        self.assertIn("TLS", job["log"])
        self.assertEqual(job["result"]["files"], 3)

        # The mount path inside the container instead of the FTP root: a clear error, and with replace the disk is untouched.
        res = self.client.post("/api/apps/shop/disks/media/import", {"ftp": "ftps://liara:secret@ftp.example.com:2112/usr/src/app/media", "replace": True}, format="json")
        job = self._job(res)
        self.assertEqual(job["status"], "failed")
        self.assertIn("/usr/src/app/media", job["log"])
        self.assertIn("media", job["log"].split("at the FTP root:")[-1])
        self.assertTrue((self.root / "a.jpg").exists())

    def test_internal_hosts_and_bad_input_rejected(self):
        for body in ({"ftp": "ftp://u:p@127.0.0.1/"}, {"url": "http://10.1.2.3/media.tar.gz"}, {"ftp": "http://x/"}):
            res = self.client.post("/api/apps/shop/disks/media/import", body, format="json")
            self.assertEqual(res.status_code, 400, body)
        self.assertEqual(self._upload(b"").status_code, 400)


# Tests must not depend on the free space of the machine running them.
@override_settings(DOCKRA_RESERVED_DISK_GB=0)
class DiskBackupScheduleTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(
            DOCKRA_DATA_ROOT=str(self.tmp), MEDIA_ROOT=self.tmp / "media", DOCKRA_DISK_BACKUP_HOURS=24, DOCKRA_DISK_BACKUP_KEEP=3
        )
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/app/media"}, format="json")
        self.disk = Disk.objects.get(name="media")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _age(self, hours):
        DiskBackup.objects.all().update(created_at=timezone.now() - timedelta(hours=hours))

    def test_failed_backup_retried_and_old_ones_pruned(self):
        with mock.patch("engine.disk_backup.make_backup", side_effect=RuntimeError("disk gone")):
            self.assertEqual(disk_backup.schedule_auto(), 1)
        self.assertEqual(disk_backup.schedule_auto(), 0)
        self._age(2)
        self.assertEqual(disk_backup.schedule_auto(), 1)
        self.assertFalse(DiskBackup.objects.filter(status=DiskBackup.Status.FAILED).exists())
        for _ in range(4):
            self._age(30)
            disk_backup.schedule_auto()
        self.assertEqual(DiskBackup.objects.filter(status=DiskBackup.Status.READY).count(), 3)

    def test_per_disk_schedule(self):
        res = self.client.patch("/api/apps/shop/disks/media", {"backup_hours": 0, "backup_keep": 2}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["backup_keep"], 2)
        self.assertEqual(disk_backup.schedule_auto(), 0)
        self.client.patch("/api/apps/shop/disks/media", {"backup_hours": 6}, format="json")
        self.assertEqual(disk_backup.schedule_auto(), 1)
        self._age(5)
        self.assertEqual(disk_backup.schedule_auto(), 0)
        self.assertEqual(self.client.patch("/api/apps/shop/disks/media", {"backup_keep": 0}, format="json").status_code, 400)


class RestoreReportTests(TestCase):
    def setUp(self):
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/databases", {"name": "shopdb", "engine": "postgres"}, format="json")

    def test_restore_job_reports_summary(self):
        from databases.models import Database

        db = Database.objects.get(name="shopdb")
        backup = self.client.post(f"/api/databases/{db.id}/backup", {}, format="json").data["backup"]
        with mock.patch("engine.drivers.FakeDriver.db_summary", return_value={"tables": 4, "rows": 120, "unit": "tables"}):
            res = self.client.post(f"/api/databases/{db.id}/restore", {"backup_id": backup["id"]}, format="json")
        job = self.client.get(f"/api/jobs/{res.data['job']['id']}").data
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["result"]["summary"]["tables"], 4)
        self.assertIn("120", job["log"])
