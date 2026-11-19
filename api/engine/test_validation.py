import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps_core.models import App, Disk, EnvVar
from engine.source import apply_manifest


class DiskRulesTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(DOCKRA_DATA_ROOT=str(self.tmp), MEDIA_ROOT=self.tmp / "media", WORK_ROOT=self.tmp / "work")
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        for slug in ("shop", "shop-media"):
            self.client.post("/api/apps", {"name": slug, "slug": slug}, format="json")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _disk(self, slug="shop", **body):
        return self.client.post(f"/api/apps/{slug}/disks", {"name": "media", "mount_to": "/app/media", "size_gb": 2, **body}, format="json")

    def test_same_name_cannot_overwrite_existing_disk(self):
        self.assertEqual(self._disk().status_code, 201)
        again = self._disk(mount_to="/somewhere/else", size_gb=1)
        self.assertEqual(again.status_code, 400)
        disk = Disk.objects.get(app__slug="shop", name="media")
        self.assertEqual((disk.mount_to, disk.size_gb), ("/app/media", 2))

    def test_mount_paths(self):
        for bad in ("/", "/etc", "/usr/", "app/media", "/app/../etc", ""):
            self.assertEqual(self._disk(name="d1", mount_to=bad).status_code, 400, bad)
        self.assertEqual(self._disk(mount_to="/app/media/").status_code, 201)
        self.assertEqual(Disk.objects.get(name="media").mount_to, "/app/media")
        self.assertEqual(self._disk(name="other", mount_to="/app/media").status_code, 400)
        self.assertEqual(self._disk(name="thumbs", mount_to="/app/media/thumbs").status_code, 400)
        self.assertEqual(self._disk(name="static", mount_to="/app/static").status_code, 201)

    def test_volume_names_cannot_collide_across_apps(self):
        self.assertEqual(self._disk("shop", name="media-x").status_code, 201)
        clash = self._disk("shop-media", name="x")
        self.assertEqual(clash.status_code, 400)
        self.assertIn("another app", clash.data["detail"])
        # dockra-disk-shop-media- (23 characters) + name; volume names are limited to 62 characters.
        self.assertEqual(self._disk("shop-media", name="d" * 39, mount_to="/long").status_code, 201)
        self.assertEqual(self._disk("shop-media", name="e" * 40, mount_to="/longer").status_code, 400)

    def test_resize_grows_only(self):
        self._disk()
        res = self.client.patch("/api/apps/shop/disks/media", {"size_gb": 5}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["size_gb"], 5)
        self.assertEqual(self.client.patch("/api/apps/shop/disks/media", {"size_gb": 3}, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/apps/shop/disks/media", {"size_gb": 100000}, format="json").status_code, 400)
        moved = self.client.patch("/api/apps/shop/disks/media", {"mount_to": "/srv/media"}, format="json")
        self.assertEqual(moved.data["mount_to"], "/srv/media")
        self.assertEqual(self.client.patch("/api/apps/shop/disks/media", {"mount_to": "/etc"}, format="json").status_code, 400)

    def test_manifest_never_shrinks_and_reports_bad_disks(self):
        app = App.objects.get(slug="shop")
        apply_manifest(app, {"disks": [{"name": "media", "mountTo": "/app/media", "sizeGb": 5}]})
        apply_manifest(app, {"disks": [{"name": "media", "mountTo": "/app/media", "sizeGb": 2}]})
        self.assertEqual(Disk.objects.get(app=app, name="media").size_gb, 5)
        with self.assertRaisesRegex(RuntimeError, "liara.json"):
            apply_manifest(app, {"disks": [{"name": "bad", "mountTo": "/etc"}]})
        other = App.objects.get(slug="shop-media")
        apply_manifest(app, {"disks": [{"name": "media-y", "mountTo": "/y"}]})
        with self.assertRaises(RuntimeError):
            apply_manifest(other, {"disks": [{"name": "y", "mountTo": "/y"}]})


class DatabaseLinkRulesTests(TestCase):
    def setUp(self):
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/apps", {"name": "shop", "slug": "shop"}, format="json")
        self.first = self.client.post("/api/databases", {"name": "first", "engine": "postgres"}, format="json").data["id"]
        self.second = self.client.post("/api/databases", {"name": "second", "engine": "postgres"}, format="json").data["id"]

    def test_second_database_cannot_take_the_same_variable(self):
        self.assertEqual(self.client.post(f"/api/databases/{self.first}/link", {"app": "shop"}, format="json").status_code, 200)
        url = EnvVar.objects.get(app__slug="shop", key="DATABASE_URL").value
        taken = self.client.post(f"/api/databases/{self.second}/link", {"app": "shop"}, format="json")
        self.assertEqual(taken.status_code, 400)
        self.assertIn("first", taken.data["detail"])
        self.assertEqual(EnvVar.objects.get(app__slug="shop", key="DATABASE_URL").value, url)
        self.assertEqual(self.client.post(f"/api/databases/{self.second}/link", {"app": "shop", "env_key": "REPORTS_DB"}, format="json").status_code, 200)
        # Linking the same database again (e.g. to refresh the URL) is fine.
        self.assertEqual(self.client.post(f"/api/databases/{self.first}/link", {"app": "shop"}, format="json").status_code, 200)
        for bad in ("BAD KEY", "1X", "a-b"):
            self.assertEqual(self.client.post(f"/api/databases/{self.first}/link", {"app": "shop", "env_key": bad}, format="json").status_code, 400)
