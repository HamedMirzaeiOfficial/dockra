import io
import tarfile
from datetime import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps_core.models import App, Disk
from engine.cron import cron_matches


class ApiTests(TestCase):
    def setUp(self):
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def test_platforms_and_engines(self):
        denied = self.client.post(
            "/api/apps",
            {"name": "PHP", "slug": "phpapp", "platform": "php"},
            format="json",
        )
        self.assertEqual(denied.status_code, 400)
        node = self.client.post("/api/apps", {"name": "Node", "slug": "nodeapp", "platform": "node"}, format="json")
        self.assertEqual(node.status_code, 201, node.content)
        self.assertEqual(node.data["port"], 3000)
        created = self.client.post(
            "/api/apps",
            {"name": "Shop", "slug": "shop"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["platform"], "django")
        self.assertEqual(created.data["region"], "local")
        self.assertEqual(created.data["runtime"], "")
        abroad = self.client.post(
            "/api/apps",
            {"name": "Outer", "slug": "outer", "region": "abroad"},
            format="json",
        )
        self.assertEqual(abroad.status_code, 201, abroad.content)
        self.assertEqual(abroad.data["region"], "local")
        self.assertEqual(abroad.data["region_label"], "This server")
        self.assertTrue(created.data["domains"][0]["hostname"].endswith(".sslip.io"))
        oracle = self.client.post("/api/databases", {"name": "bad-db", "engine": "oracle"}, format="json")
        self.assertEqual(oracle.status_code, 400)
        for engine, port in (("mysql", 3306), ("mariadb", 3306), ("mongodb", 27017)):
            made = self.client.post("/api/databases", {"name": f"db-{engine}", "engine": engine}, format="json")
            self.assertEqual(made.status_code, 201, made.content)
            self.assertEqual(made.data["port"], port)
        redis = self.client.post("/api/databases", {"name": "cache", "engine": "redis"}, format="json")
        self.assertEqual(redis.status_code, 201)
        self.assertEqual(redis.data["engine"], "redis")
        self.assertEqual(redis.data["port"], 6379)

    def test_trailing_slash_is_optional(self):
        created = self.client.post("/api/apps/", {"name": "Shop", "slug": "shop"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(self.client.get("/api/apps/shop/").status_code, 200)
        self.assertEqual(self.client.get("/api/apps/shop").status_code, 200)
        patched = self.client.patch("/api/apps/shop/", {"cpu": 0.25}, format="json")
        self.assertEqual(patched.status_code, 200, patched.content)
        admin = self.client.get("/admin")
        self.assertEqual(admin.status_code, 301)
        self.assertEqual(admin["Location"], "/admin/")

    def test_seed_has_no_demo_data(self):
        from databases.models import Database

        self.assertEqual(App.objects.count(), 0)
        self.assertEqual(Database.objects.count(), 0)

    def test_deploy_start_and_rollback(self):
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        res = self.client.post("/api/apps/shop/deploy", {"message": "first"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        app = App.objects.get(slug="shop")
        self.assertEqual(app.status, App.Status.RUNNING)
        first = app.deployments.order_by("number").first()
        again = self.client.post("/api/apps/shop/deploy", {"message": "next release"}, format="json")
        self.assertEqual(again.status_code, 200)
        back = self.client.post("/api/apps/shop/rollback", {"deployment_id": first.id}, format="json")
        self.assertEqual(back.status_code, 200, back.content)
        app.refresh_from_db()
        self.assertEqual(app.current_image, f"dockra/shop:v{first.number}")
        detail = self.client.get("/api/apps/shop")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["metrics"]["cpu"], 0)
        self.assertEqual(detail.data["metrics"]["network_mbps"], 0)
        states = {step["key"]: step["state"] for step in detail.data["pipeline"]}
        self.assertEqual(states["build"], "skipped")
        self.assertEqual(states["docker"], "skipped")
        self.assertEqual(states["ssl"], "pending")
        self.assertEqual(states["run"], "done")

    def test_manifest_disk_and_backup(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            payload = b'{"platform":"django","port":8000,"disks":[{"name":"media","mountTo":"/app/media"}],"cron":["* * * * * python manage.py run_due_jobs"]}'
            info = tarfile.TarInfo("dockra.json")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        upload = SimpleUploadedFile("src.tar.gz", buf.getvalue(), content_type="application/gzip")
        res = self.client.post("/api/apps/shop/deploy", {"message": "with manifest", "file": upload}, format="multipart")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Disk.objects.filter(app__slug="shop", name="media", mount_to="/app/media").exists())
        created = self.client.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        db_id = created.data["id"]
        backup = self.client.post(f"/api/databases/{db_id}/backup", {}, format="json")
        self.assertEqual(backup.status_code, 200, backup.content)
        self.assertEqual(backup.data["backup"]["status"], "ready")

    def test_viewer_cannot_deploy(self):
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        self.client.post(
            "/api/members",
            {"username": "viewer", "password": "dockra-view", "role": "viewer"},
            format="json",
        )
        client = APIClient()
        res = client.post("/api/auth/login", {"username": "viewer", "password": "dockra-view"}, format="json")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        denied = client.post("/api/apps/shop/deploy", {"message": "no"}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_cron_expression(self):
        when = datetime(2026, 10, 6, 14, 30)
        self.assertTrue(cron_matches("* * * * *", when))
        self.assertTrue(cron_matches("30 14 * * *", when))
        self.assertTrue(cron_matches("*/15 * * * *", when))
        self.assertFalse(cron_matches("0 14 * * *", when))
        self.assertTrue(cron_matches("* * * * 2", when))

    @override_settings(
        DOCKRA_HOST_CPU=2,
        DOCKRA_HOST_MEMORY_MB=4096,
        DOCKRA_HOST_DISK_GB=40,
        DOCKRA_RESERVED_CPU=1,
        DOCKRA_RESERVED_MEMORY_MB=2048,
        DOCKRA_RESERVED_DISK_GB=20,
    )
    def test_quota_blocks_oversized_plan(self):
        from databases.models import Database

        created = self.client.post(
            "/api/apps",
            {"name": "Shop", "slug": "shop", "cpu": "0.5", "memory_mb": 512},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data["cpu"], 0.5)
        huge = self.client.post(
            "/api/apps",
            {"name": "Heavy", "slug": "heavy", "cpu": "4", "memory_mb": 4096},
            format="json",
        )
        self.assertEqual(huge.status_code, 201, huge.content)
        denied = self.client.post("/api/apps/heavy/deploy", {"message": "over capacity"}, format="json")
        self.assertEqual(denied.status_code, 400, denied.content)
        self.assertIn("capacity", denied.data["detail"])
        self.assertEqual(App.objects.get(slug="heavy").status, App.Status.STOPPED)
        ok = self.client.post("/api/apps/shop/deploy", {"message": "fits"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        dash = self.client.get("/api/dashboard")
        self.assertGreaterEqual(dash.data["capacity"]["cpu_used"], 0.5)
        self.assertLess(dash.data["capacity"]["cpu_free"], dash.data["capacity"]["cpu_limit"])
        blocked = self.client.post(
            "/api/databases",
            {"name": "toobig", "engine": "postgres", "memory_mb": 8192, "disk_gb": 50},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertFalse(Database.objects.filter(name="toobig").exists())

    def test_disk_stays_reserved_and_delete_frees_it(self):
        from pathlib import Path
        import tempfile

        from databases.models import Database
        from engine.source import ensure_dockerfile

        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")
        disk = self.client.post(
            "/api/apps/shop/disks",
            {"name": "media", "mount_to": "/app/media", "size_gb": 2},
            format="json",
        )
        self.assertEqual(disk.status_code, 201, disk.content)
        self.assertEqual(App.objects.get(slug="shop").status, App.Status.STOPPED)
        dash = self.client.get("/api/dashboard")
        self.assertEqual(dash.data["capacity"]["disk_gb_used"], 2)
        app = App.objects.get(slug="shop")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            ensure_dockerfile(source, app, {})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("gunicorn", text)
            self.assertIn("USER dockra", text)
            self.assertNotIn("runserver", text)
            from engine.source import detect_runtime

            self.assertEqual(detect_runtime(source), "3.12")
            self.assertIn("COPY --chown=1000:1000 . .", text)
            self.assertNotIn("chown -R", text)
            self.assertIn("AS deps", text)
            (source / "Dockerfile").write_text("FROM python:3.9\n", encoding="utf-8")
            (source / "requirements.txt").write_text("psycopg2==2.9.9\n", encoding="utf-8")
            ensure_dockerfile(source, app, {"django": {"pythonVersion": "3.11"}, "packages": ["ffmpeg"]})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertEqual(detect_runtime(source), "3.11")
            self.assertIn("ffmpeg libpq5", text)
            self.assertIn("-r requirements.txt", text)
            (source / "Dockerfile").write_text("FROM python:3.9\n", encoding="utf-8")
            ensure_dockerfile(source, app, {"platform": "docker"})
            self.assertEqual(detect_runtime(source), "3.9")
        from engine.drivers import _net_mbps, _size_bytes

        self.assertEqual(_size_bytes("1.5kB"), 1500)
        self.assertEqual(_net_mbps("shop", 0), 0.0)
        created = self.client.post("/api/databases", {"name": "cache", "engine": "redis"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        removed_db = self.client.delete(f"/api/databases/{created.data['id']}")
        self.assertEqual(removed_db.status_code, 204, removed_db.content)
        self.assertFalse(Database.objects.filter(name="cache").exists())
        removed = self.client.delete("/api/apps/shop")
        self.assertEqual(removed.status_code, 204, removed.content)
        dash = self.client.get("/api/dashboard")
        self.assertEqual(dash.data["capacity"]["disk_gb_used"], 0)

    def test_cli_login_saves_a_token_after_the_browser_button(self):
        from django.test import Client

        started = Client().post("/api/auth/cli/start")
        self.assertEqual(started.status_code, 200)
        code = started.json()["code"]
        browser = Client()
        page = browser.get(f"/cli?code={code}")
        self.assertContains(page, "Username")
        signed = browser.post("/cli/login", {"code": code, "username": "admin", "password": "dockra"})
        self.assertEqual(signed.status_code, 302)
        approved = browser.post("/cli/approve", {"code": code})
        self.assertEqual(approved.status_code, 302)
        polled = Client().get(f"/api/auth/cli/poll?code={code}")
        self.assertEqual(polled.status_code, 200)
        self.assertTrue(polled.json()["token"].startswith("dockra_"))
        again = Client().get(f"/api/auth/cli/poll?code={code}")
        self.assertEqual(again.json()["status"], "pending")

    def test_deploy_page_uses_a_watch_ticket(self):
        from django.test import Client

        opened = self.client.post("/api/auth/cli/watch", {"app": "missing-app", "after": ""}, format="json")
        self.assertEqual(opened.status_code, 200, opened.content)
        page = Client().get(f"/cli/deploy?ticket={opened.data['code']}")
        self.assertContains(page, "Sending the project folder")
        status = Client().get(f"/cli/deploy/status?ticket={opened.data['code']}")
        self.assertEqual(status.json()["phase"], "upload")
