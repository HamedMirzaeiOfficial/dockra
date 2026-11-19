import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from databases.models import Backup, Database
from engine.runner import schedule_auto_backups


class BackupScheduleTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(MEDIA_ROOT=self.tmp, DOCKRA_AUTO_BACKUP_HOURS=24, DOCKRA_BACKUP_KEEP=3)
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/databases", {"name": "shopdb", "engine": "postgres"}, format="json")
        self.db = Database.objects.get(name="shopdb")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _age(self, backup, hours):
        Backup.objects.filter(pk=backup.pk).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_failure_is_recorded_and_retried(self):
        with mock.patch("engine.drivers.FakeDriver.backup_database", side_effect=RuntimeError('pg_dump: error: role "x" does not exist')):
            self.assertEqual(schedule_auto_backups(), 1)
        failed = self.db.backups.get()
        self.assertEqual(failed.status, Backup.Status.FAILED)
        self.assertIn("does not exist", failed.error)
        detail = self.client.get(f"/api/databases/{self.db.id}").data
        self.assertIn("does not exist", detail["backups"][0]["error"])

        # Not retaken before the retry delay has passed; retaken right after it (not 24 hours later).
        self.assertEqual(schedule_auto_backups(), 0)
        self._age(failed, 2)
        self.assertEqual(schedule_auto_backups(), 1)
        self.assertEqual(self.db.backups.filter(status=Backup.Status.READY).count(), 1)
        # The old failed backup is removed after a successful one.
        self.assertFalse(self.db.backups.filter(status=Backup.Status.FAILED).exists())
        self.assertEqual(schedule_auto_backups(), 0)

    def test_keeps_newest_three_ready_backups(self):
        for age in (100, 80, 60, 40):
            self.assertEqual(self.client.post(f"/api/databases/{self.db.id}/backup", {}, format="json").status_code, 200)
            self._age(self.db.backups.order_by("-id").first(), age)
        self.assertEqual(self.db.backups.count(), 3)
        self.assertEqual(len(list((self.tmp / "backups").iterdir())), 3)
        self.assertEqual(schedule_auto_backups(), 1)
        ages = sorted(self.db.backups.values_list("created_at", flat=True), reverse=True)
        self.assertEqual(len(ages), 3)
        self.assertGreater(ages[0], timezone.now() - timedelta(minutes=1))
        self.assertEqual(len(list((self.tmp / "backups").iterdir())), 3)

    def test_per_database_schedule(self):
        res = self.client.patch(f"/api/databases/{self.db.id}", {"backup_hours": 6, "backup_keep": 2}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["database"]["backup_hours"], 6)
        self.assertEqual(schedule_auto_backups(), 1)
        self._age(self.db.backups.get(), 5)
        self.assertEqual(schedule_auto_backups(), 0)
        self._age(self.db.backups.get(), 7)
        self.assertEqual(schedule_auto_backups(), 1)
        self._age(self.db.backups.order_by("-id").first(), 7)
        self.assertEqual(schedule_auto_backups(), 1)
        self.assertEqual(self.db.backups.count(), 2)

        self.client.patch(f"/api/databases/{self.db.id}", {"backup_hours": 0}, format="json")
        Backup.objects.all().update(created_at=timezone.now() - timedelta(days=5))
        self.assertEqual(schedule_auto_backups(), 0)
        self.assertEqual(self.client.patch(f"/api/databases/{self.db.id}", {"backup_keep": 0}, format="json").status_code, 400)
        self.client.patch(f"/api/databases/{self.db.id}", {"backup_hours": "", "backup_keep": ""}, format="json")
        self.db.refresh_from_db()
        self.assertIsNone(self.db.backup_hours)
        self.assertEqual(schedule_auto_backups(), 1)
