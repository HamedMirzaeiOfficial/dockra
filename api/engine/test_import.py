import gzip
import io
import shutil
import struct
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from databases.models import Database
from engine import dumps
from engine.drivers import MYSQL_SQL_DROP, PG_SQL_DROP, DockerDriver
from engine.models import Job

PG_SQL = b"""--
-- PostgreSQL database dump
--

-- Dumped from database version 15.4
-- Dumped by pg_dump version 15.6

SET client_encoding = 'UTF8';
CREATE DATABASE liara WITH TEMPLATE = template0;
ALTER DATABASE liara OWNER TO root;
\\connect liara
CREATE TABLE public.shop (id integer);
ALTER TABLE public.shop OWNER TO root;
COPY public.shop (id) FROM stdin;
1
2
\\.
GRANT ALL ON SCHEMA public
    TO root;
SELECT 1;
"""

MYSQL_SQL = b"""-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)
--
-- Host: localhost    Database: liara
-- ------------------------------------------------------
-- Server version\t8.0.35
SET @@GLOBAL.GTID_PURGED=/*!80000 '+'*/ 'aaaa:1-10,
bbbb:1-3';
CREATE DATABASE /*!32312 IF NOT EXISTS*/ `liara`;
USE `liara`;
CREATE TABLE `t` (`id` int);
INSERT INTO `t` VALUES (1),(2);
"""


def _pg_custom(server="16.4 (Debian 16.4-1.pgdg120+1)", tool="16.4"):
    def string(value: str) -> bytes:
        raw = value.encode()
        return b"\x00" + struct.pack("<i", len(raw)) + raw

    return b"PGDMP\x01\x0f\x00\x04\x08\x01" + b"\x00" * 30 + string("liara") + string(server) + string(tool) + b"\x00" * 100


def _tar(name: str, data: bytes, zipped=True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if zipped else "w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _bson_string(key: str, value: str) -> bytes:
    raw = value.encode() + b"\x00"
    return b"\x02" + key.encode() + b"\x00" + struct.pack("<i", len(raw)) + raw


class DetectTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _file(self, data: bytes, zipped=False) -> Path:
        path = self.tmp / f"f{len(list(self.tmp.iterdir()))}"
        path.write_bytes(gzip.compress(data) if zipped else data)
        return path

    def test_formats_and_versions(self):
        self.assertEqual(dumps.detect(self._file(_pg_custom()))["version"], "16")
        self.assertEqual(dumps.detect(self._file(_pg_custom("17.2", "17.2")))["version"], "17")
        plain = dumps.detect(self._file(PG_SQL, zipped=True))
        self.assertEqual((plain["engine"], plain["version"]), ("postgres", "15"))
        mysql = dumps.detect(self._file(MYSQL_SQL))
        self.assertEqual((mysql["engine"], mysql["version"]), ("mysql", "8.0"))
        maria = dumps.detect(self._file(b"-- MariaDB dump 10.19  Distrib 10.11.6-MariaDB\n-- Server version\t10.11.6-MariaDB-1\n"))
        self.assertEqual((maria["engine"], maria["version"]), ("mariadb", "10.11"))
        self.assertEqual(dumps.detect(self._file(b"REDIS0011\xfa"))["version"], "7.4")
        self.assertEqual(dumps.detect(self._file(b"REDIS0012\xfa"))["version"], "7.4")
        mongo = dumps.MONGO_MAGIC + b"\x00" * 8 + _bson_string("db", "liara-shop") + _bson_string("collection", "orders")
        found = dumps.detect(self._file(mongo, zipped=True))
        self.assertEqual((found["engine"], found["databases"]), ("mongodb", ["liara-shop"]))
        with self.assertRaises(dumps.DumpError):
            dumps.detect(self._file(b"hello world"))

    def test_liara_tar_archive_is_unwrapped(self):
        # Provider-style postgres backup: tar.gz with one custom-format pg_dump named postgres.dump
        dump = _pg_custom("14.5 (Debian 14.5-2.pgdg110+2)", "14.5")
        for zipped in (True, False):
            path = self._file(_tar("postgres.dump", dump, zipped))
            self.assertEqual(dumps.detect(path), {"engine": "postgres", "version": "14", "format": "pg_dump custom"})
            dumps.unwrap(path)
            self.assertEqual(path.read_bytes(), dump)
        plain = self._file(PG_SQL, zipped=True)
        dumps.unwrap(plain)
        self.assertEqual(gzip.decompress(plain.read_bytes()), PG_SQL)

    def test_tar_with_several_files_is_refused(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("a.sql", "b.sql"):
                info = tarfile.TarInfo(name)
                info.size = len(PG_SQL)
                tar.addfile(info, io.BytesIO(PG_SQL))
        with self.assertRaises(dumps.DumpError):
            dumps.unwrap(self._file(buf.getvalue()))

    def test_restore_that_creates_no_table_fails(self):
        from engine.runner import _restore_and_report

        driver = mock.Mock()
        driver.restore_database.return_value = "ERROR:  syntax error at or near \"postgres\""
        driver.db_summary.return_value = {"tables": 0, "rows": 0, "unit": "tables"}
        with mock.patch("engine.runner.get_driver", return_value=driver), self.assertRaisesRegex(RuntimeError, "No tables"):
            _restore_and_report(mock.Mock(), self._file(PG_SQL), lambda line: None)

    def test_needs_newer(self):
        self.assertTrue(dumps.needs_newer("postgres", "15", "16"))
        self.assertFalse(dumps.needs_newer("postgres", "17", "16"))
        self.assertFalse(dumps.needs_newer("mysql", "5.7", "8.0"))

    def test_sql_filter_keeps_data_and_drops_source_specific_statements(self):
        pg = DockerDriver()._pipe_filtered(["cat"], self._file(PG_SQL, zipped=True), PG_SQL_DROP)
        self.assertNotIn("CREATE DATABASE", pg)
        self.assertNotIn("\\connect", pg)
        self.assertNotIn("OWNER TO", pg)
        self.assertNotIn("TO root", pg)
        for kept in ("CREATE TABLE public.shop", "COPY public.shop", "1\n2\n\\.", "SELECT 1;"):
            self.assertIn(kept, pg)
        my = DockerDriver()._pipe_filtered(["cat"], self._file(MYSQL_SQL), MYSQL_SQL_DROP)
        self.assertNotIn("GTID", my)
        self.assertNotIn("bbbb", my)
        self.assertNotIn("USE `liara`", my)
        self.assertIn("INSERT INTO `t` VALUES (1),(2);", my)

    def test_failing_client_raises(self):
        with self.assertRaises(RuntimeError):
            DockerDriver()._pipe_filtered(["sh", "-c", "cat >/dev/null; echo boom; exit 3"], self._file(MYSQL_SQL), MYSQL_SQL_DROP)

    def test_download_refuses_internal_addresses(self):
        for url in ("http://127.0.0.1/x", "http://10.0.0.5/backup", "file:///etc/passwd", "http://localhost:8000/"):
            with self.assertRaises(dumps.DumpError):
                dumps.download(url, self.tmp / "out")


class ImportApiTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(MEDIA_ROOT=self.tmp)
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, query: str, body: bytes):
        return self.client.generic("POST", f"/api/databases/import?{query}", body, content_type="application/octet-stream")

    def test_import_creates_matching_database(self):
        res = self._post("name=shop-db", gzip.compress(PG_SQL))
        self.assertEqual(res.status_code, 202, res.content)
        self.assertEqual(res.data["detected"]["version"], "15")
        db = Database.objects.get(name="shop-db")
        self.assertEqual((db.engine, db.version, db.status), ("postgres", "15", Database.Status.RUNNING))
        status = self.client.get(f"/api/databases/import/{res.data['job']['id']}")
        self.assertEqual(status.data["status"], Job.Status.DONE, status.data)
        self.assertIn("Importing data", status.data["log"])
        self.assertTrue(status.data["database"]["url"].startswith("postgres://"))
        # The temporary file is removed after the job.
        self.assertEqual(list((self.tmp / "restore").iterdir()), [])

    def test_engine_mismatch_and_bad_input(self):
        res = self._post("name=wrong&engine=mongodb", MYSQL_SQL)
        self.assertEqual(res.status_code, 202)
        status = self.client.get(f"/api/databases/import/{res.data['job']['id']}").data
        self.assertEqual(status["status"], Job.Status.FAILED)
        self.assertEqual(Database.objects.get(name="wrong").status, Database.Status.FAILED)

        self.assertEqual(self._post("name=x1", b"just text").status_code, 400)
        self.assertEqual(self._post("name=1bad", MYSQL_SQL).status_code, 400)
        self.assertEqual(self._post("name=x2", b"").status_code, 400)
        self.assertFalse(Database.objects.filter(name="x1").exists())

    def test_old_version_request_is_refused(self):
        res = self._post("name=old&version=14", gzip.compress(PG_SQL))
        status = self.client.get(f"/api/databases/import/{res.data['job']['id']}").data
        self.assertEqual(status["status"], Job.Status.FAILED)
        self.assertIn("15", status["log"])

    def test_url_import_downloads_on_server(self):
        def fake_download(url, dest, on_progress=None):
            dest.write_bytes(MYSQL_SQL)
            return len(MYSQL_SQL)

        with mock.patch("engine.dumps.download", side_effect=fake_download) as download, mock.patch("engine.dumps.check_url"):
            res = self.client.post("/api/databases/import?name=from-url&url=https://liara.example/backup.sql")
        self.assertEqual(res.status_code, 202, res.content)
        download.assert_called_once()
        db = Database.objects.get(name="from-url")
        self.assertEqual((db.engine, db.version, db.status), ("mysql", "8.0", Database.Status.RUNNING))

    def _piece(self, upload_id: str, offset: int, data: bytes):
        return self.client.generic("PUT", f"/api/uploads/{upload_id}?offset={offset}", data, content_type="application/octet-stream")

    def test_piecewise_upload_resumes_and_imports(self):
        body = gzip.compress(PG_SQL)
        upload_id = self.client.post("/api/uploads").data["upload"]
        self.assertEqual(self._piece(upload_id, 0, body[:100]).data["size"], 100)
        # The chunk arrived but the response was lost: resending it returns 409 with the current size.
        again = self._piece(upload_id, 0, body[:100])
        self.assertEqual((again.status_code, again.data["size"]), (409, 100))
        self.assertEqual(self._piece(upload_id, 100, body[100:]).data["size"], len(body))
        self.assertEqual(self.client.get(f"/api/uploads/{upload_id}").data["size"], len(body))

        res = self.client.post(f"/api/databases/import?name=pieces&upload={upload_id}")
        self.assertEqual(res.status_code, 202, res.content)
        db = Database.objects.get(name="pieces")
        self.assertEqual((db.engine, db.version, db.status), ("postgres", "15", Database.Status.RUNNING))
        self.assertEqual(list((self.tmp / "restore").iterdir()), [])

    def test_restore_from_piecewise_upload(self):
        self.assertEqual(self._post("name=target", gzip.compress(PG_SQL)).status_code, 202)
        db = Database.objects.get(name="target")
        body = gzip.compress(PG_SQL)
        upload_id = self.client.post("/api/uploads").data["upload"]
        self.assertEqual(self._piece(upload_id, 0, body).data["size"], len(body))
        res = self.client.post(f"/api/databases/{db.id}/restore", {"upload": upload_id}, format="json")
        self.assertIn(res.status_code, (200, 202), res.content)
        self.assertEqual(self.client.get(f"/api/jobs/{res.data['job']['id']}").data["status"], Job.Status.DONE)
        safety = Job.objects.get(pk=res.data["job"]["id"]).result["safety_backup_id"]
        self.assertTrue(db.backups.filter(pk=safety, status="ready").exists())
        # The same upload cannot be consumed twice.
        again = self.client.post(f"/api/databases/{db.id}/restore", {"upload": upload_id}, format="json")
        self.assertEqual(again.status_code, 400)

    def test_linked_apps_are_stopped_during_restore(self):
        from apps_core.models import App
        from databases.models import DatabaseLink
        from engine.drivers import FakeDriver

        self.assertEqual(self._post("name=linked", gzip.compress(PG_SQL)).status_code, 202)
        db = Database.objects.get(name="linked")
        app = App.objects.create(organization=db.organization, name="shop", slug="shop", status=App.Status.RUNNING)
        DatabaseLink.objects.create(database=db, app=app, env_key="DATABASE_URL")
        calls = []
        with mock.patch.object(FakeDriver, "stop", lambda self, a: calls.append(("stop", a.slug))), \
                mock.patch.object(FakeDriver, "start", lambda self, a: calls.append(("start", a.slug))), \
                mock.patch.object(FakeDriver, "restore_database", lambda self, d, s: calls.append(("restore", d.name)) or ""):
            res = self.client.post(f"/api/databases/{db.id}/restore", {"file": SimpleUploadedFile("b.sql.gz", gzip.compress(PG_SQL))}, format="multipart")
        self.assertIn(res.status_code, (200, 202), res.content)
        self.assertEqual(calls, [("stop", "shop"), ("restore", "linked"), ("start", "shop")])

    def test_piecewise_upload_rejects_unknown_ids(self):
        self.assertEqual(self._piece("../../etc", 0, b"x").status_code, 404)
        self.assertEqual(self._piece("a" * 32, 0, b"x").status_code, 404)
        res = self.client.post(f"/api/databases/import?name=ghost&upload={'b' * 32}")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Database.objects.filter(name="ghost").exists())

    def test_internal_url_rejected_before_creating_anything(self):
        res = self.client.post("/api/databases/import?name=ssrf&url=http://127.0.0.1:8000/api/health")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Database.objects.filter(name="ssrf").exists())
