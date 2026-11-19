import hashlib
import hmac
import io
import json
import re
import shutil
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps_core.models import App, Deployment, Domain, EnvVar
from databases.models import Backup
from engine import blobs
from engine.models import Job, MetricSample


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_tar(items: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in items.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FeatureTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings_override = override_settings(
            DOCKRA_DATA_ROOT=str(self.tmp),
            MEDIA_ROOT=self.tmp / "media",
            WORK_ROOT=self.tmp / "work",
        )
        self.settings_override.enable()
        call_command("seed")
        self.client = APIClient()
        res = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _upload(self, items: dict[str, bytes]):
        body = _blob_tar({_sha(data): data for data in items.values()})
        return self.client.generic("POST", "/api/apps/shop/blobs", body, content_type="application/gzip")

    def _files(self, items: dict[str, bytes]):
        return [{"path": path, "hash": _sha(data), "size": len(data), "mode": 0o644} for path, data in items.items()]

    def test_delta_deploy_sends_only_missing_files(self):
        items = {"dockra.json": b'{"platform":"django","port":8000}', "app/wsgi.py": b"print(1)\n", "big.bin": b"x" * 5000}
        plan = self.client.post("/api/apps/shop/sync", {"files": self._files(items)}, format="json")
        self.assertEqual(plan.status_code, 200, plan.content)
        self.assertEqual(len(plan.data["missing"]), 3)
        early = self.client.post("/api/apps/shop/deploy", {"files": self._files(items)}, format="json")
        self.assertEqual(early.status_code, 409)
        self.assertEqual(self._upload(items).status_code, 200)
        again = self.client.post("/api/apps/shop/sync", {"files": self._files(items)}, format="json")
        self.assertEqual(again.data["missing"], [])
        deployed = self.client.post("/api/apps/shop/deploy", {"message": "delta", "files": self._files(items)}, format="json")
        self.assertEqual(deployed.status_code, 200, deployed.content)
        self.assertIn("3 of 3 files changed", deployed.data["deployment"]["log"])
        mirror = self.tmp / "work" / "mirror" / "shop"
        self.assertEqual((mirror / "app" / "wsgi.py").read_bytes(), b"print(1)\n")
        items["app/wsgi.py"] = b"print(2)\n"
        del items["big.bin"]
        plan = self.client.post("/api/apps/shop/sync", {"files": self._files(items)}, format="json")
        self.assertEqual(plan.data["missing"], [_sha(b"print(2)\n")])
        self._upload({"app/wsgi.py": items["app/wsgi.py"]})
        deployed = self.client.post("/api/apps/shop/deploy", {"files": self._files(items)}, format="json")
        self.assertIn("1 of 2 files changed", deployed.data["deployment"]["log"])
        self.assertFalse((mirror / "big.bin").exists())
        self.assertEqual((mirror / "app" / "wsgi.py").read_bytes(), b"print(2)\n")
        # The build folder is cleaned and the generated Dockerfile never touches the mirror.
        self.assertFalse((mirror / "Dockerfile").exists())
        dep = Deployment.objects.get(pk=deployed.data["deployment"]["id"])
        self.assertFalse((self.tmp / "work" / str(dep.id)).exists())

    def test_blob_upload_rejects_wrong_content_and_bad_paths(self):
        body = _blob_tar({_sha(b"real"): b"fake"})
        res = self.client.generic("POST", "/api/apps/shop/blobs", body, content_type="application/gzip")
        self.assertEqual(res.status_code, 400)
        bad = self.client.post(
            "/api/apps/shop/sync",
            {"files": [{"path": "../etc/passwd", "hash": "a" * 64, "size": 1}]},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_mirror_handles_file_to_directory_change(self):
        one = [{"path": "a", "hash": _sha(b"1"), "size": 1, "mode": 0o644}]
        two = [{"path": "a/b.txt", "hash": _sha(b"2"), "size": 1, "mode": 0o755}]
        for data in (b"1", b"2"):
            blobs._write_blob(_sha(data), [data])
        blobs.sync_mirror("shop", one)
        mirror, written = blobs.sync_mirror("shop", two)
        self.assertEqual(written, 1)
        self.assertEqual((mirror / "a" / "b.txt").read_bytes(), b"2")
        mirror, written = blobs.sync_mirror("shop", one)
        self.assertTrue((mirror / "a").is_file())

    def test_blob_gc_keeps_recent_trees(self):
        items = {"keep.txt": b"keep"}
        self._upload(items)
        self.client.post("/api/apps/shop/deploy", {"files": self._files(items)}, format="json")
        blobs._write_blob(_sha(b"orphan"), [b"orphan"])
        removed = blobs.gc(min_age_seconds=0)
        self.assertEqual(removed, 1)
        self.assertTrue(blobs.has(_sha(b"keep")))
        self.assertFalse(blobs.has(_sha(b"orphan")))

    def test_legacy_tar_rejects_symlink_escape_and_is_deleted(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            link = tarfile.TarInfo("escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc"
            archive.addfile(link)
            data = b"owned"
            info = tarfile.TarInfo("escape/owned.txt")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        upload = SimpleUploadedFile("src.tar.gz", buf.getvalue(), content_type="application/gzip")
        res = self.client.post("/api/apps/shop/deploy", {"message": "evil", "file": upload}, format="multipart")
        self.assertEqual(res.status_code, 400)
        dep = Deployment.objects.get(app__slug="shop")
        self.assertEqual(dep.status, Deployment.Status.FAILED)
        self.assertFalse(Path(dep.source_path).exists())
        self.assertFalse(Path("/etc/owned.txt").exists())

    def test_secrets_are_encrypted_at_rest(self):
        self.client.put("/api/apps/shop/env", {"items": [{"key": "TOKEN", "value": "s3cret", "is_secret": True}]}, format="json")
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM apps_core_envvar")
            raw = cursor.fetchone()[0]
        self.assertTrue(raw.startswith("enc:"))
        self.assertNotIn("s3cret", raw)
        self.assertEqual(EnvVar.objects.get(key="TOKEN").value, "s3cret")

    def test_webhook_signature_and_branch(self):
        self.client.patch("/api/apps/shop", {"git_url": "https://example.invalid/r.git", "git_branch": "main"}, format="json")
        made = self.client.post("/api/apps/shop/webhook", {}, format="json")
        self.assertEqual(made.status_code, 200, made.content)
        secret = made.data["secret"]
        anon = Client()

        def send(body, sig=None):
            payload = json.dumps(body).encode()
            sig = sig or "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            return anon.post("/api/hooks/shop", payload, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)

        self.assertEqual(send({"ref": "refs/heads/main"}, sig="sha256=bad").status_code, 403)
        skipped = send({"ref": "refs/heads/dev"})
        self.assertIn("skipped", skipped.json())
        with mock.patch("apps_core.extra.deploy_app") as deploy:
            deploy.return_value = (mock.Mock(id=9, number=4), None)
            ok = send({"ref": "refs/heads/main", "after": "abcdef123", "head_commit": {"message": "fix\nmore"}})
        self.assertEqual(ok.status_code, 202, ok.content)
        self.assertEqual(deploy.call_args.kwargs["message"], "webhook abcdef1 fix")
        gitlab = anon.post("/api/hooks/shop", b"{}", content_type="application/json", HTTP_X_GITLAB_TOKEN="nope")
        self.assertEqual(gitlab.status_code, 403)

    def test_disk_file_manager(self):
        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/app/media"}, format="json")
        upload = SimpleUploadedFile("hello.txt", b"hello", content_type="text/plain")
        res = self.client.post("/api/apps/shop/disks/media/upload?path=/docs", {"file": upload}, format="multipart")
        self.assertEqual(res.status_code, 200, res.content)
        listing = self.client.get("/api/apps/shop/disks/media/files?path=/docs")
        self.assertEqual([item["name"] for item in listing.data["entries"]], ["hello.txt"])
        got = self.client.get("/api/apps/shop/disks/media/download?path=/docs/hello.txt")
        self.assertEqual(b"".join(got.streaming_content), b"hello")
        folder = self.client.get("/api/apps/shop/disks/media/download?path=/docs")
        with tarfile.open(fileobj=io.BytesIO(b"".join(folder.streaming_content))) as archive:
            self.assertIn("docs/hello.txt", archive.getnames())
        escape = self.client.get("/api/apps/shop/disks/media/files?path=/../../")
        self.assertEqual(escape.status_code, 400)
        raw = _blob_tar({"bulk/a.txt": b"a"})
        res = self.client.generic("POST", "/api/apps/shop/disks/media/upload?path=/", raw, content_type="application/gzip")
        self.assertEqual(res.status_code, 200, res.content)
        removed = self.client.delete("/api/apps/shop/disks/media/files?path=/bulk")
        self.assertEqual(removed.status_code, 204)

    def test_exec_logs_stream_and_domains(self):
        out = self.client.post("/api/apps/shop/exec", {"command": "ls"}, format="json")
        self.assertEqual(out.status_code, 200, out.content)
        self.assertEqual(out.data["exit_code"], 0)
        ticket = self.client.post("/api/apps/shop/logs/ticket?tail=50", {}, format="json")
        self.assertEqual(ticket.status_code, 200, ticket.content)
        self.assertIn("/ws/logs?ticket=", ticket.data["url"])
        self.assertTrue(ticket.data["url"].endswith("&tail=50"))
        primary = Domain.objects.get(app__slug="shop", is_primary=True)
        self.assertEqual(self.client.delete(f"/api/apps/shop/domains/{primary.id}").status_code, 400)
        extra = self.client.post("/api/apps/shop/domains", {"hostname": "shop.example.com"}, format="json")
        patched = self.client.patch(f"/api/apps/shop/domains/{extra.data['id']}", {"redirect_https": True}, format="json")
        self.assertTrue(patched.data["redirect_https"])
        self.assertEqual(self.client.delete(f"/api/apps/shop/domains/{extra.data['id']}").status_code, 204)
        for bad in ("https://shop.example.com", "shop.example.com/x", "shop", "-a.example.com", "a b.com"):
            rejected = self.client.post("/api/apps/shop/domains", {"hostname": bad}, format="json")
            self.assertEqual(rejected.status_code, 400, bad)
        trailing = self.client.post("/api/apps/shop/domains", {"hostname": "New.Example.org."}, format="json")
        self.assertEqual(trailing.data["hostname"], "new.example.org")
        self.assertEqual(trailing.data["dns"][0]["name"], "new")
        self.assertEqual(trailing.data["dns"][0]["zone"], "example.org")
        apex = self.client.post("/api/apps/shop/domains", {"hostname": "a.b.example.co.uk"}, format="json")
        self.assertEqual((apex.data["dns"][0]["name"], apex.data["dns"][0]["zone"]), ("a.b", "example.co.uk"))
        root = self.client.post("/api/apps/shop/domains", {"hostname": "example.net"}, format="json")
        self.assertEqual(root.data["dns"][0]["name"], "@")
        self.assertEqual(primary.app.domains.get(is_primary=True).kind, Domain.Kind.SSLIP)
        with mock.patch("apps_core.views.resolve_ipv4", return_value=[]):
            check = self.client.get(f"/api/apps/shop/domains/{root.data['id']}/check")
        self.assertEqual(check.data["state"], "missing")
        with mock.patch("apps_core.views.resolve_ipv4", return_value=[settings.PUBLIC_IP]):
            check = self.client.get(f"/api/apps/shop/domains/{root.data['id']}/check")
        self.assertEqual(check.data["state"], "ok")
        with mock.patch("apps_core.views.resolve_ipv4", return_value=["185.143.233.132"]):
            check = self.client.get(f"/api/apps/shop/domains/{root.data['id']}/check")
        self.assertEqual(check.data["state"], "proxied")
        with mock.patch("apps_core.views.resolve_ipv4", return_value=["203.0.113.7"]), mock.patch(
            "apps_core.views.edge_server", return_value="ArvanCloud"
        ):
            check = self.client.get(f"/api/apps/shop/domains/{root.data['id']}/check")
        self.assertEqual(check.data["state"], "proxied")
        with mock.patch("apps_core.views.resolve_ipv4", return_value=["203.0.113.7"]), mock.patch(
            "apps_core.views.edge_server", return_value="nginx"
        ):
            check = self.client.get(f"/api/apps/shop/domains/{root.data['id']}/check")
        self.assertEqual(check.data["state"], "mismatch")
        import socket as _socket
        from apps_core import views as app_views

        with mock.patch("apps_core.views.socket.getaddrinfo", side_effect=_socket.gaierror), mock.patch(
            "apps_core.views.query_a", side_effect=[OSError, ["203.0.113.10"]]
        ):
            self.assertEqual(app_views.resolve_ipv4("app.example.com"), ["203.0.113.10"])

    def test_errors_are_json(self):
        missing = self.client.get("/no/such/path")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"], "Not found")

    def test_docker_labels_split_https_redirect(self):
        from engine.drivers import DockerDriver

        app = App.objects.get(slug="shop")
        Domain.objects.create(app=app, hostname="shop.example.com", redirect_https=True)
        labels = DockerDriver()._labels(app, "r1")
        joined = "\n".join(labels)
        self.assertIn("dockra.release=r1", joined)
        self.assertIn("traefik.http.routers.shop-r.rule=Host(`shop.example.com`)", joined)
        self.assertIn("traefik.http.routers.shop-r.middlewares=shop-https", joined)
        self.assertNotIn("shop.example.com", next(item for item in labels if item.startswith("traefik.http.routers.shop.rule")))

    def test_redirects_api_and_traefik_labels(self):
        from engine.drivers import DockerDriver
        from engine.redirects import regex_and_replacement

        app = App.objects.get(slug="shop")
        Domain.objects.create(app=app, hostname="shop.example.com")
        Domain.objects.create(app=app, hostname="shop.example.org")
        url = "/api/apps/shop/redirects"
        foreign = self.client.post(url, {"source_host": "other.example.com", "target": "https://shop.example.com"}, format="json")
        self.assertEqual(foreign.status_code, 400)
        loop = self.client.post(url, {"source_host": "shop.example.org", "target": "https://shop.example.org/x"}, format="json")
        self.assertEqual(loop.status_code, 400)
        bad = self.client.post(url, {"source_host": "shop.example.org", "target": "https://shop.example.com/$1"}, format="json")
        self.assertEqual(bad.status_code, 400)

        whole = self.client.post(url, {"source_host": "shop.example.org", "target": "https://shop.example.com"}, format="json")
        self.assertEqual(whole.status_code, 201, whole.content)
        moved = self.client.post(url, {"source_path": "/blog/", "target": "/news", "permanent": False}, format="json")
        self.assertEqual(moved.status_code, 201, moved.content)
        self.assertEqual(moved.data["source_path"], "/blog")
        page = self.client.post(url, {"source_path": "/old.html", "match": "exact", "target": "https://x.org/new?a=1"}, format="json")
        self.assertEqual(page.status_code, 201, page.content)
        again = self.client.post(url, {"source_path": "/blog", "target": "/other"}, format="json")
        self.assertEqual(again.status_code, 400)

        def follow(item, address):
            regex, replacement = regex_and_replacement(item)
            found = re.match(regex, address)
            if not found:
                return None
            return re.sub(r"\$\{(\d)\}", lambda m: found.group(int(m.group(1))) or "", replacement)

        rows = {item.id: item for item in app.redirects.all()}
        self.assertEqual(follow(rows[whole.data["id"]], "https://shop.example.org/a/b?q=1"), "https://shop.example.com/a/b?q=1")
        self.assertEqual(follow(rows[whole.data["id"]], "http://shop.example.org/"), "https://shop.example.com/")
        self.assertEqual(follow(rows[moved.data["id"]], "https://shop.example.com/blog/post-1?x=2"), "https://shop.example.com/news/post-1?x=2")
        self.assertEqual(follow(rows[moved.data["id"]], "http://shop.example.org/blog"), "http://shop.example.org/news")
        self.assertEqual(follow(rows[page.data["id"]], "https://shop.example.com/old.html?z=1"), "https://x.org/new?a=1")

        joined = "\n".join(DockerDriver()._labels(app, "r1"))
        rid = whole.data["id"]
        self.assertIn(f"traefik.http.routers.shop_redirect{rid}.rule=Host(`shop.example.org`)\n", joined + "\n")
        self.assertIn(f"traefik.http.routers.shop_redirect{rid}-s.tls=true", joined)
        self.assertIn(f"traefik.http.middlewares.shop_redirect{rid}.redirectregex.permanent=true", joined)
        self.assertIn(
            f"traefik.http.routers.shop_redirect{moved.data['id']}.rule=(Host(`shop.127.0.0.1.sslip.io`) || Host(`shop.example.com`) || Host(`shop.example.org`))"
            " && (Path(`/blog`) || PathPrefix(`/blog/`))",
            joined,
        )

        patched = self.client.patch(f"{url}/{page.data['id']}", {"permanent": False}, format="json")
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertFalse(patched.data["permanent"])
        self.assertEqual(patched.data["match"], "exact")
        domain = Domain.objects.get(hostname="shop.example.org")
        self.assertEqual(self.client.delete(f"/api/apps/shop/domains/{domain.id}").status_code, 204)
        self.assertFalse(app.redirects.filter(source_host="shop.example.org").exists())
        self.assertEqual(self.client.delete(f"{url}/{page.data['id']}").status_code, 204)
        self.assertEqual(len(self.client.get(url).data), 1)

    def test_redirect_django_admin_validates_and_syncs(self):
        from django.contrib.auth import get_user_model

        app = App.objects.get(slug="shop")
        Domain.objects.create(app=app, hostname="shop.example.org")
        browser = Client()
        browser.force_login(get_user_model().objects.get(username="admin"))
        form = {"app": app.id, "source_host": "shop.example.org", "source_path": "/", "match": "prefix", "keep_path": "on", "permanent": "on"}
        loop = browser.post("/admin/apps_core/redirect/add/", {**form, "target": "/x"})
        self.assertEqual(loop.status_code, 200)
        self.assertFalse(app.redirects.exists())
        with mock.patch("engine.actions.sync_app") as sync:
            saved = browser.post("/admin/apps_core/redirect/add/", {**form, "target": "https://shop.example.com"})
        self.assertEqual(saved.status_code, 302, saved.content[:2000])
        self.assertEqual(app.redirects.get().target, "https://shop.example.com")
        sync.assert_called_once()

    def test_restore_from_backup_and_upload(self):
        made = self.client.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json")
        db_id = made.data["id"]
        self.client.post(f"/api/databases/{db_id}/backup", {}, format="json")
        backup = Backup.objects.get(database_id=db_id)
        restored = self.client.post(f"/api/databases/{db_id}/restore", {"backup_id": backup.id}, format="json")
        self.assertEqual(restored.status_code, 200, restored.content)
        upload = SimpleUploadedFile("liara.dump", b"PGDMP...", content_type="application/octet-stream")
        res = self.client.post(f"/api/databases/{db_id}/restore", {"file": upload}, format="multipart")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Job.objects.filter(kind=Job.Kind.RESTORE, status=Job.Status.DONE).count(), 2)
        self.assertFalse(any((self.tmp / "media" / "restore").iterdir()))
        download = self.client.get(f"/api/databases/{db_id}/backups/{backup.id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/databases/{db_id}/backups/{backup.id}").status_code, 204)

    def test_stale_job_is_recovered_and_queues_split(self):
        from engine.runner import recover_stale_jobs

        app = App.objects.get(slug="shop")
        dep = Deployment.objects.create(app=app, number=1, status=Deployment.Status.BUILDING)
        job = Job.objects.create(
            kind=Job.Kind.DEPLOY,
            queue=Job.Queue.BUILD,
            status=Job.Status.RUNNING,
            payload={"deployment_id": dep.id},
            heartbeat_at=timezone.now() - timedelta(minutes=10),
        )
        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        dep.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertEqual(dep.status, Deployment.Status.FAILED)
        restart = self.client.post("/api/apps/shop/restart", {}, format="json")
        self.assertEqual(Job.objects.get(pk=restart.data["job"]["id"]).queue, Job.Queue.OPS)

    def test_metrics_series_and_auto_backup(self):
        from engine.runner import schedule_auto_backups

        app = App.objects.get(slug="shop")
        app.status = App.Status.RUNNING
        app.save()
        now = timezone.now()
        for minutes in range(5):
            MetricSample.objects.create(app=app, at=now - timedelta(minutes=minutes), cpu=10 * minutes, ram=50)
        data = self.client.get("/api/apps/shop/metrics?range=1h").data
        self.assertTrue(data["series"])
        self.assertEqual(data["ram"], 50)
        self.assertEqual(self.client.get("/api/apps/shop/metrics?range=2y").status_code, 400)
        self.client.post("/api/databases", {"name": "cache", "engine": "redis"}, format="json")
        self.assertEqual(schedule_auto_backups(), 1)
        self.assertEqual(schedule_auto_backups(), 0)
        self.assertEqual(Backup.objects.get().kind, Backup.Kind.AUTO)

    def test_cli_pages_escape_input(self):
        started = Client().post("/api/auth/cli/start").json()["code"]
        page = Client().get(f"/cli?code={started}%3Cscript%3E")
        self.assertNotIn(b"<script>", page.content)
        watch = self.client.post("/api/auth/cli/watch", {"app": "<b>x</b>"}, format="json")
        page = Client().get(f"/cli/deploy?ticket={watch.data['code']}")
        self.assertNotIn(b"<b>x</b>", page.content)


class TemplateTests(TestCase):
    def _app(self, platform, port):
        from accounts.models import Organization

        org, _ = Organization.objects.get_or_create(slug="o", defaults={"name": "o"})
        return App.objects.create(organization=org, name="a", slug="a", platform=platform, port=port)

    def test_node_nextjs_static_and_liara_manifest(self):
        from engine.source import ensure_dockerfile, read_manifest

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "package.json").write_text("{}", encoding="utf-8")
            (source / "package-lock.json").write_text("{}", encoding="utf-8")
            ensure_dockerfile(source, self._app("nextjs", 3000), {"platform": "nextjs"})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("FROM node:20-alpine", text)
            self.assertIn("npm ci", text)
            self.assertIn("npm run build", text)
            self.assertIn("next start", (source / "dockra-start.sh").read_text(encoding="utf-8"))
            (source / "liara.json").write_text('{"platform":"static","static":{"root":"dist","spa":true}}', encoding="utf-8")
            manifest = read_manifest(source)
            self.assertEqual(manifest["platform"], "static")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            App.objects.all().delete()
            ensure_dockerfile(source, self._app("static", 80), {"platform": "static", "static": {"root": "dist", "spa": True}})
            self.assertIn("COPY dist/ /usr/share/nginx/html/", (source / "Dockerfile").read_text(encoding="utf-8"))
            self.assertIn("/index.html", (source / "dockra-nginx.conf").read_text(encoding="utf-8"))

    def test_django_template_uses_pip_cache_and_collectstatic(self):
        from engine.source import ensure_dockerfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django\n", encoding="utf-8")
            ensure_dockerfile(source, self._app("django", 8000), {"django": {"collectStatic": True}})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("--mount=type=cache,target=/root/.cache/pip", text)
            self.assertNotIn("PIP_NO_CACHE_DIR", text)
            start = (source / "dockra-start.sh").read_text(encoding="utf-8")
            self.assertIn("collectstatic --noinput", start)
            self.assertIn("--workers ${WEB_CONCURRENCY:-", start)

    def test_collectstatic_defaults_on_and_can_be_disabled(self):
        from engine.source import ensure_dockerfile

        app = self._app("django", 8000)

        def start(manifest):
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                ensure_dockerfile(source, app, manifest)
                return (source / "dockra-start.sh").read_text(encoding="utf-8")

        self.assertIn("collectstatic --noinput || echo", start({"platform": "django"}))
        self.assertNotIn("|| echo", start({"django": {"collectStatic": True}}))
        self.assertNotIn("collectstatic", start({"django": {"collectStatic": False}}))

    @override_settings(
        DOCKRA_PIP_INDEX_URL="https://mirror.example/simple",
        DOCKRA_PIP_EXTRA_INDEX_URL="https://extra.example/simple",
        DOCKRA_PIP_FALLBACK_MIRROR="https://fallback.example/simple",
    )
    def test_pip_extra_index_leads_the_fallback_chain(self):
        from engine.source import ensure_dockerfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
            ensure_dockerfile(source, self._app("django", 8000), {"platform": "django"})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertIn("PIP_INDEX_URL=https://mirror.example/simple", text)
            self.assertIn("PIP_EXTRA_INDEX_URL=https://extra.example/simple", text)
            # Fallback order: extra index, configured fallback mirror, then pypi.org.
            self.assertLess(text.index("|| PIP_INDEX_URL=https://extra.example/simple /venv/bin/pip install"),
                            text.index("|| PIP_INDEX_URL=https://fallback.example/simple /venv/bin/pip install"))
            self.assertIn("|| PIP_INDEX_URL=https://pypi.org/simple /venv/bin/pip install", text)

    @override_settings(
        DOCKRA_PIP_INDEX_URL="https://mirror.example/simple",
        DOCKRA_PIP_EXTRA_INDEX_URL="",
        DOCKRA_PIP_FALLBACK_MIRROR="https://fallback.example/simple",
    )
    def test_pip_falls_back_through_configured_mirror_then_pypi(self):
        from engine.source import ensure_dockerfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
            ensure_dockerfile(source, self._app("django", 8000), {"platform": "django"})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            mirrors = ["https://fallback.example/simple", "https://pypi.org/simple"]
            positions = [text.index(f"|| PIP_INDEX_URL={url} /venv/bin/pip install") for url in mirrors]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("--retries 5 --timeout 60", text)
            # The last attempt (with a compiler) also uses the first fallback mirror.
            self.assertIn("&& PIP_INDEX_URL=https://fallback.example/simple /venv/bin/pip install", text)

    @override_settings(DOCKRA_PIP_INDEX_URL="https://mirror.example/simple", DOCKRA_PIP_EXTRA_INDEX_URL="", DOCKRA_PIP_FALLBACK_MIRROR="")
    def test_no_regional_mirrors_are_hardcoded(self):
        from engine.source import PIP_FALLBACK_MIRRORS, ensure_dockerfile

        self.assertEqual(PIP_FALLBACK_MIRRORS, ("https://pypi.org/simple",))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
            ensure_dockerfile(source, self._app("django", 8000), {"platform": "django"})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            indexes = {line.split("PIP_INDEX_URL=")[1].split()[0] for line in text.splitlines() if "PIP_INDEX_URL=" in line}
            self.assertEqual(indexes, {"https://mirror.example/simple", "https://pypi.org/simple"})

    @override_settings(DOCKRA_PIP_INDEX_URL="", DOCKRA_PIP_EXTRA_INDEX_URL="")
    def test_no_mirror_keeps_single_pip_path(self):
        from engine.source import ensure_dockerfile

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
            ensure_dockerfile(source, self._app("django", 8000), {"platform": "django"})
            text = (source / "Dockerfile").read_text(encoding="utf-8")
            self.assertNotIn("PIP_INDEX_URL=", text)
            self.assertEqual(text.count("/venv/bin/pip install"), 2)

    def test_django_serves_static_root_without_project_whitenoise(self):
        from engine.source import ensure_dockerfile

        app = self._app("django", 8000)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "requirements.txt").write_text("Django==4.2\n", encoding="utf-8")
            ensure_dockerfile(source, app, {"platform": "django"})
            self.assertIn("'whitenoise>=5,<7'", (source / "Dockerfile").read_text(encoding="utf-8"))
            self.assertIn("dockra_wsgi:application", (source / "dockra-start.sh").read_text(encoding="utf-8"))
            self.assertIn("\"MEDIA_ROOT\", \"MEDIA_URL\"", (source / "dockra_wsgi.py").read_text(encoding="utf-8"))
            (source / "requirements.txt").write_text("Django==4.2\nwhitenoise==6.6.0\n", encoding="utf-8")
            ensure_dockerfile(source, app, {"platform": "django"})
            self.assertNotIn("'whitenoise>=5,<7'", (source / "Dockerfile").read_text(encoding="utf-8"))

    def test_python_version_for_liara_projects(self):
        from engine.source import ensure_dockerfile

        def build(manifest, requirements="", files=None):
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                if requirements:
                    (source / "requirements.txt").write_text(requirements, encoding="utf-8")
                for name, text in (files or {}).items():
                    (source / name).write_text(text, encoding="utf-8")
                App.objects.all().delete()
                ensure_dockerfile(source, self._app("django", 8000), manifest)
                return (source / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.10-slim-bookworm", build({"django": {"pythonVersion": "3.10.4"}}))
        self.assertIn("FROM python:3.7-slim-bullseye", build({"pythonVersion": "3.7"}))
        self.assertIn("FROM python:3.9-slim-bookworm", build({}, files={"runtime.txt": "python-3.9.16\n"}))
        self.assertIn("FROM python:3.11-slim-bookworm", build({}, files={".python-version": "3.11\n"}))
        self.assertIn("FROM python:3.9-slim-bookworm", build({}, "Django==2.2.28\npsycopg2-binary\n"))
        self.assertIn("FROM python:3.10-slim-bookworm", build({}, "django~=3.2.0\n"))
        self.assertIn("FROM python:3.12-slim-bookworm", build({}, "Django==4.2.16\n"))
        self.assertIn("FROM python:3.12-slim-bookworm", build({}, "Django>=3.0\n"))
        pinned = build({}, "Django==3.2\ngunicorn==20.1.0\n")
        self.assertIn("-r requirements.txt 'whitenoise>=5,<7' \\", pinned)
        self.assertNotIn("gunicorn>=22", pinned)
        self.assertIn("'gunicorn>=22,<24'", build({}, "Django==4.2\n"))
        with self.assertRaisesMessage(RuntimeError, "is not supported"):
            build({"django": {"pythonVersion": "2.7"}})

    def test_write_does_not_touch_hardlinked_mirror(self):
        from engine.source import _write

        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "mirror.txt"
            original.write_text("user Dockerfile", encoding="utf-8")
            linked = Path(tmp) / "build.txt"
            linked.hardlink_to(original)
            _write(linked, "generated")
            self.assertEqual(original.read_text(encoding="utf-8"), "user Dockerfile")


class ZeroDowntimeTests(TestCase):
    def setUp(self):
        from accounts.models import Organization

        org = Organization.objects.create(name="o", slug="o")
        self.app = App.objects.create(organization=org, name="a", slug="a", platform="django", port=8000, current_image="img:v1")

    def _driver(self, healthy: bool):
        from engine.drivers import DockerDriver

        calls = []

        def fake_run(args, check=True, **kwargs):
            calls.append(args)
            out = b""
            if args[:2] == ["docker", "ps"]:
                out = b"old1\n"
            if args[:2] == ["docker", "inspect"] and "dockra.release" in args[3]:
                out = b"oldrelease\n"
            return mock.Mock(returncode=0, stdout=out, stderr=b"")

        driver = DockerDriver()
        ready = mock.Mock(side_effect=None if healthy else RuntimeError("boom"))
        patches = [
            mock.patch("engine.drivers._run", side_effect=fake_run),
            mock.patch.object(DockerDriver, "_wait_ready", ready),
        ]
        return driver, calls, patches

    def test_old_containers_removed_only_after_new_release_is_ready(self):
        driver, calls, patches = self._driver(healthy=True)
        with patches[0], patches[1]:
            driver._launch(self.app, "img:v2")
        run_at = next(i for i, c in enumerate(calls) if c[:3] == ["docker", "run", "-d"])
        stop_at = next(i for i, c in enumerate(calls) if c[:2] == ["docker", "stop"])
        self.assertLess(run_at, stop_at)
        self.assertIn("old1", calls[stop_at])

    def test_failed_release_keeps_old_containers(self):
        driver, calls, patches = self._driver(healthy=False)
        with patches[0], patches[1], self.assertRaises(RuntimeError):
            driver._launch(self.app, "img:v2")
        self.assertFalse(any(c[:2] == ["docker", "stop"] for c in calls))
        removed = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
        self.assertTrue(removed and all(name.startswith("dockra-app-a-") for name in removed[0][3:]))

    def test_app_containers_cannot_gain_privileges(self):
        driver, calls, patches = self._driver(healthy=True)
        with patches[0], patches[1]:
            driver._launch(self.app, "img:v2")
        run = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertIn("no-new-privileges", run)


class DatabaseVersionTests(TestCase):
    def test_postgres_18_mounts_the_parent_directory(self):
        from databases.models import Database
        from engine.drivers import db_data_path

        def path(engine, version):
            return db_data_path(Database(engine=engine, version=version))

        self.assertEqual(path("postgres", "16"), "/var/lib/postgresql/data")
        self.assertEqual(path("postgres", "13.4"), "/var/lib/postgresql/data")
        self.assertEqual(path("postgres", "18"), "/var/lib/postgresql")
        self.assertEqual(path("postgres", "latest"), "/var/lib/postgresql/data")
        self.assertEqual(path("redis", "8.0"), "/data")

    def test_newer_redis_rdb_is_refused_before_touching_the_container(self):
        from databases.models import Database
        from engine.drivers import DockerDriver, _rdb_supported

        self.assertEqual(_rdb_supported("6.2"), 9)
        self.assertEqual(_rdb_supported("7.0"), 10)
        self.assertEqual(_rdb_supported("7.2-alpine"), 11)
        self.assertEqual(_rdb_supported("8.0"), 12)
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "dump.rdb"
            dump.write_bytes(b"REDIS0011" + b"\x00" * 16)
            database = Database(engine="redis", version="6.2", container_name="x", password="p")
            with mock.patch("engine.drivers._run") as run:
                with self.assertRaisesMessage(RuntimeError, "Redis 7.2 or later"):
                    DockerDriver().restore_database(database, dump)
                run.assert_not_called()

    def test_version_tag_is_validated(self):
        call_command("seed")
        client = APIClient()
        res = client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        bad = client.post("/api/databases", {"name": "pg", "engine": "postgres", "version": "16; rm -rf"}, format="json")
        self.assertEqual(bad.status_code, 400)
        good = client.post("/api/databases", {"name": "pg", "engine": "postgres", "version": "13"}, format="json")
        self.assertEqual(good.status_code, 201, good.content)
        self.assertEqual(good.data["version"], "13")


class DomainCheckSsrfTests(SimpleTestCase):
    def test_edge_probe_never_contacts_private_addresses(self):
        from apps_core import views as app_views

        with mock.patch("apps_core.views.http.client.HTTPConnection") as conn:
            for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "172.18.0.2", "192.168.1.1", "::1", "not-an-ip"):
                self.assertEqual(app_views.edge_server("app.example.com", ip), "")
        conn.assert_not_called()
