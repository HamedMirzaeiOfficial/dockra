import asyncio
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ApiToken, CliLogin, Membership
from apps_core.models import App, CronJob, Disk
from databases.models import Database
from engine.models import Alert, ServiceBeat


class _Base(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(
            DOCKRA_DATA_ROOT=str(self.tmp), MEDIA_ROOT=self.tmp / "media", WORK_ROOT=self.tmp / "work"
        )
        self.override.enable()
        call_command("seed")
        self.client = self.login("admin", "dockra")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, username, password):
        client = APIClient()
        res = client.post("/api/auth/login", {"username": username, "password": password}, format="json")
        assert res.status_code == 200, res.content
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        client.access = res.data["access"]
        return client

    def member(self, username, role):
        res = self.client.post("/api/members", {"username": username, "password": "Strong-pass-42", "role": role}, format="json")
        assert res.status_code == 201, res.content
        return res.data["id"]


class CliPageTests(_Base):
    def test_csrf_is_required_and_approval_warns(self):
        code = Client().post("/api/auth/cli/start").json()["code"]
        browser = Client(enforce_csrf_checks=True)
        page = browser.get(f"/cli?code={code}")
        self.assertIn("csrfmiddlewaretoken", page.content.decode())
        self.assertIn("default-src 'none'", page["Content-Security-Policy"])
        blocked = browser.post("/cli/login", {"code": code, "username": "admin", "password": "dockra"})
        self.assertEqual(blocked.status_code, 403)
        token = browser.cookies["csrftoken"].value
        signed = browser.post("/cli/login", {"code": code, "username": "admin", "password": "dockra", "csrfmiddlewaretoken": token})
        self.assertEqual(signed.status_code, 302)
        approve_page = browser.get(f"/cli?code={code}").content.decode()
        self.assertIn(code[-6:].upper(), approve_page)
        self.assertIn("do not approve", approve_page)
        token = browser.cookies["csrftoken"].value
        self.assertEqual(browser.post("/cli/approve", {"code": code, "csrfmiddlewaretoken": token}).status_code, 302)
        again = browser.post("/cli/approve", {"code": code, "csrfmiddlewaretoken": token})
        self.assertEqual(again.status_code, 410)
        self.assertEqual(ApiToken.objects.filter(name="cli").count(), 1)
        self.assertIsNotNone(CliLogin.objects.get(code=code).user_id)


class ForwardedForTests(TestCase):
    def test_header_only_trusted_from_proxy(self):
        from accounts import security

        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="1.2.3.4")
        security._proxy_cache = (0.0, set())
        with override_settings(DOCKRA_TRUSTED_PROXIES=[]):
            self.assertEqual(security._client_ip(request), "1.2.3.4")
        security._proxy_cache = (0.0, set())
        with override_settings(DOCKRA_TRUSTED_PROXIES=["1.2.3.4"]):
            self.assertEqual(security._client_ip(request), "6.6.6.6")
        security._proxy_cache = (0.0, set())


class SessionTests(_Base):
    def test_logout_and_password_change_revoke_sessions(self):
        other = self.login("admin", "dockra")
        self.assertEqual(self.client.post("/api/auth/logout", {}, format="json").status_code, 204)
        self.assertEqual(other.get("/api/apps").status_code, 401)
        client = self.login("admin", "dockra")
        weak = client.post("/api/auth/password", {"old_password": "dockra", "new_password": "123"}, format="json")
        self.assertEqual(weak.status_code, 400)
        changed = client.post("/api/auth/password", {"old_password": "dockra", "new_password": "New-strong-42"}, format="json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(client.get("/api/apps").status_code, 401)
        fresh = APIClient()
        fresh.credentials(HTTP_AUTHORIZATION=f"Bearer {changed.data['access']}")
        self.assertEqual(fresh.get("/api/apps").status_code, 200)

    def test_member_management_rules(self):
        dev_id = self.member("dev", "developer")
        owner = Membership.objects.get(role="owner")
        self.assertEqual(self.client.patch(f"/api/members/{owner.id}", {"role": "viewer"}, format="json").status_code, 400)
        admin_id = self.member("ops", "admin")
        ops = self.login("ops", "Strong-pass-42")
        self.assertEqual(ops.patch(f"/api/members/{dev_id}", {"role": "admin"}, format="json").status_code, 403)
        self.assertEqual(ops.patch(f"/api/members/{admin_id}", {"role": "viewer"}, format="json").status_code, 400)
        self.assertEqual(ops.patch(f"/api/members/{owner.id}", {"is_active": False}, format="json").status_code, 403)
        role = ops.patch(f"/api/members/{dev_id}", {"role": "viewer"}, format="json")
        self.assertEqual(role.data["role"], "viewer")
        dev = self.login("dev", "Strong-pass-42")
        token = dev.post("/api/auth/tokens", {"name": "ci"}, format="json").data["token"]
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(api.get("/api/apps").status_code, 200)
        off = ops.patch(f"/api/members/{dev_id}", {"is_active": False}, format="json")
        self.assertFalse(off.data["is_active"])
        self.assertEqual(api.get("/api/apps").status_code, 401)
        self.assertEqual(dev.get("/api/apps").status_code, 401)
        ops.patch(f"/api/members/{dev_id}", {"is_active": True}, format="json")
        reset = ops.post(f"/api/members/{dev_id}/password", {"password": "Another-pass-42"}, format="json")
        self.assertEqual(reset.status_code, 204)
        self.assertFalse(ApiToken.objects.filter(user__username="dev").exists())
        self.login("dev", "Another-pass-42")
        self.assertEqual(ops.delete(f"/api/members/{dev_id}").status_code, 204)
        self.assertFalse(get_user_model().objects.filter(username="dev").exists())
        listed = {row["username"]: row for row in self.client.get("/api/members").data}
        self.assertTrue(listed["admin"]["is_self"])


@override_settings(DOCKRA_HOST_CPU=16, DOCKRA_HOST_MEMORY_MB=65536, DOCKRA_HOST_DISK_GB=1000)
class ResourceTests(_Base):
    def test_cron_edit_and_delete(self):
        bad = self.client.post("/api/apps/shop/cron", {"schedule": "99 * * * *", "command": "x"}, format="json")
        self.assertEqual(bad.status_code, 400)
        made = self.client.post("/api/apps/shop/cron", {"schedule": "*/5 * * * *", "command": "echo hi"}, format="json")
        cron_id = made.data["id"]
        off = self.client.patch(f"/api/apps/shop/cron/{cron_id}", {"enabled": False}, format="json")
        self.assertFalse(off.data["enabled"])
        self.assertEqual(self.client.delete(f"/api/apps/shop/cron/{cron_id}").status_code, 204)
        self.assertFalse(CronJob.objects.exists())

    def test_disk_delete_and_no_shrink(self):
        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/m", "size_gb": 3}, format="json")
        shrink = self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/m", "size_gb": 1}, format="json")
        self.assertEqual(shrink.status_code, 400)
        removed = self.client.delete("/api/apps/shop/disks/media")
        self.assertEqual(removed.status_code, 200, removed.content)
        self.assertFalse(Disk.objects.exists())

    def test_database_stop_start_resize(self):
        db = self.client.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json").data
        stopped = self.client.post(f"/api/databases/{db['id']}/stop", {}, format="json")
        self.assertEqual(stopped.data["database"]["status"], "stopped")
        self.assertEqual(self.client.post(f"/api/databases/{db['id']}/stop", {}, format="json").status_code, 400)
        started = self.client.post(f"/api/databases/{db['id']}/start", {}, format="json")
        self.assertEqual(started.data["database"]["status"], "running")
        password = Database.objects.get().password
        bigger = self.client.patch(f"/api/databases/{db['id']}", {"memory_mb": 2048, "disk_gb": 20}, format="json")
        self.assertEqual(bigger.status_code, 200, bigger.content)
        row = Database.objects.get()
        self.assertEqual((row.memory_mb, row.disk_gb, row.password), (2048, 20, password))
        shrink = self.client.patch(f"/api/databases/{db['id']}", {"disk_gb": 5}, format="json")
        self.assertEqual(shrink.status_code, 400)

    def test_app_and_database_delete_go_through_jobs(self):
        db = self.client.post("/api/databases", {"name": "gone", "engine": "redis"}, format="json").data
        self.assertEqual(self.client.delete(f"/api/databases/{db['id']}").status_code, 204)
        self.assertEqual(self.client.delete("/api/apps/shop").status_code, 204)
        self.assertFalse(App.objects.exists())


class DockerFreeApiTests(_Base):
    def test_exec_and_logs_use_rpc_queue(self):
        from engine.models import Job

        with mock.patch("engine.runner.get_driver") as driver:
            driver.return_value.exec.return_value = ("hello\n__DOCKRA_CWD__/app", 0)
            driver.return_value.container_logs.return_value = "live line"
            out = self.client.post("/api/apps/shop/exec", {"command": "echo hello"}, format="json")
            logs = self.client.get("/api/apps/shop/logs")
        self.assertEqual(out.data["output"], "hello")
        self.assertEqual(out.data["cwd"], "/app")
        self.assertIn("live line", logs.data["text"])
        self.assertFalse(Job.objects.filter(queue=Job.Queue.RPC).exists())

    def test_api_views_never_call_docker_driver(self):
        import apps_core.extra
        import apps_core.views
        import databases.present

        for module in (apps_core.views, apps_core.extra, databases.present):
            self.assertFalse(hasattr(module, "get_driver"), module.__name__)


class ServiceWatchTests(_Base):
    def test_silent_service_alerts_and_health_fails(self):
        from engine.runner import beat, watch_services

        beat("worker@a", "worker", "ops")
        self.assertEqual(Client().get("/api/health").status_code, 200)
        ServiceBeat.objects.filter(name="worker@a").update(last_seen=timezone.now() - timedelta(minutes=5))
        health = Client().get("/api/health")
        self.assertEqual(health.status_code, 503)
        self.assertFalse(health.json()["services"][0]["ok"])
        with mock.patch("engine.alerts.notify") as sent:
            watch_services()
            watch_services()
        self.assertEqual(Alert.objects.filter(kind="service", status="firing").count(), 1)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(self.client.get("/api/dashboard").data["services"][0]["name"], "worker@a")
        beat("worker@a", "worker", "ops")
        with mock.patch("engine.alerts.notify"):
            watch_services()
        self.assertEqual(Alert.objects.get(kind="service").status, "resolved")

    @override_settings(DOCKRA_DRIVER="docker")
    def test_replaced_service_containers_are_forgotten(self):
        from engine.runner import beat, watch_services

        for name in ("worker@aaaaaaaaaaaa", "worker@bbbbbbbbbbbb", "worker@cccccccccccc"):
            beat(name, "worker", "ops")
        old = timezone.now() - timedelta(minutes=5)
        ServiceBeat.objects.exclude(name="worker@cccccccccccc").update(last_seen=old)
        listed = mock.Mock(returncode=0, stdout=b"bbbbbbbbbbbb\ncccccccccccc\n")
        with mock.patch("subprocess.run", return_value=listed), mock.patch("engine.alerts.notify"):
            watch_services()
        # The row of a container that no longer exists is removed; an existing silent container still raises an alert.
        self.assertEqual(
            sorted(ServiceBeat.objects.values_list("name", flat=True)), ["worker@bbbbbbbbbbbb", "worker@cccccccccccc"]
        )
        self.assertEqual(Alert.objects.filter(kind="service", status="firing").get().subject, "worker@bbbbbbbbbbbb")
        with mock.patch("subprocess.run", side_effect=OSError), mock.patch("engine.alerts.notify"):
            watch_services()
        self.assertEqual(ServiceBeat.objects.count(), 2)


class LiveLogTests(_Base):
    def test_logs_websocket_streams_and_stops(self):
        from engine import terminal

        app = App.objects.get(slug="shop")

        class FakeWs:
            def __init__(self):
                self.sent = []

            async def send(self, data):
                self.sent.append(data)

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(2)
                raise StopAsyncIteration

        ws = FakeWs()
        asyncio.run(asyncio.wait_for(terminal._logs(ws, app, 10), 5))
        self.assertIn(b"(fake) logs for shop", b"".join(item for item in ws.sent if isinstance(item, bytes)))
        self.assertEqual(json.loads(ws.sent[-1]), {"type": "exit"})


class NetworkIsolationTests(_Base):
    def _fake_docker(self, calls, inspect_ok=()):
        def fake_run(args, check=True, **kwargs):
            calls.append(args)
            out, code = b"", 0
            if args[:3] == ["docker", "network", "inspect"]:
                code = 0 if args[-1] in inspect_ok else 1
            if args[:2] == ["docker", "inspect"] and "com.docker.compose.project" in args[3]:
                out = b"selfid|dockra\n"
            if args[:2] == ["docker", "ps"] and "label=com.docker.compose.service=traefik" in args:
                out = b"traefikid\n"
            return mock.Mock(returncode=code, stdout=out, stderr=b"")

        return fake_run

    @override_settings(DOCKRA_HOST_CPU=16, DOCKRA_HOST_MEMORY_MB=65536, DOCKRA_HOST_DISK_GB=1000)
    def test_app_gets_own_network_with_traefik_self_and_linked_db_only(self):
        from engine.drivers import DockerDriver

        linked = self.client.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json").data
        self.client.post("/api/databases", {"name": "other-db", "engine": "postgres"}, format="json")
        self.client.post(f"/api/databases/{linked['id']}/link", {"app": "shop"}, format="json")
        app = App.objects.get(slug="shop")
        calls = []
        with mock.patch("engine.drivers._run", side_effect=self._fake_docker(calls)), \
                mock.patch.object(DockerDriver, "_wait_ready"):
            DockerDriver()._launch(app, "img:v1")
        create = next(c for c in calls if c[:3] == ["docker", "network", "create"])
        self.assertEqual(create[-1], "dockra-app-shop")
        connected = {(c[-2], c[-1]) for c in calls if c[:3] == ["docker", "network", "connect"]}
        self.assertIn(("dockra-app-shop", "traefikid"), connected)
        self.assertIn(("dockra-app-shop", "selfid"), connected)
        self.assertIn(("dockra-app-shop", Database.objects.get(name="shop-db").container_name), connected)
        other = Database.objects.get(name="other-db").container_name
        self.assertNotIn(other, {container for _network, container in connected})
        run = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertEqual(run[run.index("--network") + 1], "dockra-app-shop")
        self.assertIn("traefik.docker.network=dockra-app-shop", run)

    def test_removing_app_removes_its_network(self):
        from engine.drivers import DockerDriver

        app = App.objects.get(slug="shop")
        calls = []
        with mock.patch("engine.drivers._run", side_effect=self._fake_docker(calls, inspect_ok=("dockra-app-shop",))), \
                mock.patch("engine.logs.remove_app"):
            DockerDriver().remove_app(app)
        self.assertIn(["docker", "network", "rm", "dockra-app-shop"], calls)

    def test_database_volume_is_never_auto_created(self):
        from engine.drivers import DockerDriver

        made = self.client.post("/api/databases", {"name": "keep-db", "engine": "postgres"}, format="json").data
        database = Database.objects.get(pk=made["id"])
        calls = []
        with mock.patch("engine.drivers._run", side_effect=self._fake_docker(calls)), \
                mock.patch("engine.volumes.ensure_volume", return_value="/var/lib/dockra/mnt/dockra-dbvol-keep-db/data"), \
                mock.patch.object(DockerDriver, "_wait_db"):
            DockerDriver().provision_database(database, password="pw")
        run = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        # -v creates the missing source folder empty and the database initializes from scratch on it; --mount fails.
        self.assertNotIn("-v", run)
        target = run[run.index("--mount") + 1]
        self.assertEqual(
            target, 'type=bind,"source=/var/lib/dockra/mnt/dockra-dbvol-keep-db/data","target=/var/lib/postgresql/data"'
        )

    @override_settings(DOCKRA_HOST_CPU=16, DOCKRA_HOST_MEMORY_MB=65536, DOCKRA_HOST_DISK_GB=1000)
    def test_heal_restarts_containers_that_missed_their_volume(self):
        from engine.drivers import DockerDriver

        good = self.client.post("/api/databases", {"name": "good-db", "engine": "postgres"}, format="json").data
        bad = self.client.post("/api/databases", {"name": "bad-db", "engine": "postgres"}, format="json").data
        Database.objects.filter(pk=good["id"]).update(container_name="dockra-db-good-db", status="running")
        Database.objects.filter(pk=bad["id"]).update(container_name="dockra-db-bad-db", status="running")
        mounts = {
            "dockra-db-good-db": "50 40 7:3 /data /var/lib/postgresql/data rw - ext4 /dev/loop3 rw\n",
            "dockra-db-bad-db": "50 40 8:1 /var/lib/dockra/mnt/dockra-dbvol-bad-db/data /var/lib/postgresql/data rw - ext4 /dev/sda1 rw\n",
        }
        calls = []

        def fake_run(args, check=True, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "exec"]:
                return mock.Mock(returncode=0, stdout=mounts[args[2]].encode(), stderr=b"")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        logged = []
        with mock.patch("engine.drivers._run", side_effect=fake_run):
            DockerDriver().heal_volumes(logged.append)
        self.assertEqual([c for c in calls if c[:2] == ["docker", "restart"]], [["docker", "restart", "dockra-db-bad-db"]])
        self.assertEqual(len(logged), 1)


SAMPLE_1 = """# HELP traefik_service_requests_total How many HTTP requests processed on a service.
traefik_service_requests_total{code="200",method="GET",protocol="http",service="shop@docker"} 100
traefik_service_requests_total{code="502",method="GET",protocol="http",service="shop@docker"} 2
traefik_service_requests_total{code="200",method="GET",protocol="http",service="dockra-api@docker"} 50
traefik_service_request_duration_seconds_bucket{code="200",method="GET",protocol="http",service="shop@docker",le="0.05"} 50
traefik_service_request_duration_seconds_bucket{code="200",method="GET",protocol="http",service="shop@docker",le="0.1"} 90
traefik_service_request_duration_seconds_bucket{code="200",method="GET",protocol="http",service="shop@docker",le="+Inf"} 100
traefik_service_request_duration_seconds_sum{code="200",method="GET",protocol="http",service="shop@docker"} 5
traefik_service_request_duration_seconds_count{code="200",method="GET",protocol="http",service="shop@docker"} 100
"""
SAMPLE_2 = SAMPLE_1.replace("} 100\n", "} 200\n").replace("} 2\n", "} 12\n").replace('le="0.05"} 50', 'le="0.05"} 60').replace(
    'le="0.1"} 90', 'le="0.1"} 150').replace("_sum{code=\"200\",method=\"GET\",protocol=\"http\",service=\"shop@docker\"} 5", "_sum{code=\"200\",method=\"GET\",protocol=\"http\",service=\"shop@docker\"} 15")


class TrafficTests(_Base):
    def test_counters_become_per_minute_stats(self):
        from engine import traffic

        traffic._previous.clear()
        first = traffic.collect(SAMPLE_1)
        self.assertEqual(first["shop"]["requests"], 0)
        second = traffic.collect(SAMPLE_2)
        shop = second["shop"]
        self.assertEqual(shop["requests"], 110)
        self.assertEqual(shop["errors_5xx"], 10)
        self.assertEqual(shop["avg_ms"], 100.0)
        # 100 requests: 10 under 50ms, 60 up to 100ms, 30 above 100ms -> p95 at the top of the 0.1 bucket
        self.assertEqual(shop["p95_ms"], 100.0)
        restarted = traffic.collect(SAMPLE_1)
        self.assertEqual(restarted["shop"]["requests"], 102)
        traffic._previous.clear()

    def test_metrics_api_and_error_alert(self):
        from engine.alerts import _check_errors
        from engine.models import MetricSample

        app = App.objects.get(slug="shop")
        app.status = App.Status.RUNNING
        app.save()
        now = timezone.now()
        for minute in range(3):
            MetricSample.objects.create(app=app, at=now - timedelta(minutes=minute), requests=100, errors_5xx=20, avg_ms=40, p95_ms=120)
        data = self.client.get("/api/apps/shop/metrics?range=1h").data
        self.assertEqual(data["requests_per_min"], 100)
        self.assertEqual(data["error_rate"], 20.0)
        self.assertEqual(data["p95_ms"], 120)
        self.assertEqual(data["series"][-1]["requests"], 100)
        with mock.patch("engine.alerts.notify"):
            _check_errors(app)
        self.assertTrue(Alert.objects.filter(kind="errors", status="firing").exists())


# Tests must not depend on the free space of the machine running them.
@override_settings(DOCKRA_RESERVED_DISK_GB=0)
class DiskBackupTests(_Base):
    def _disk(self):
        from engine.volumes import volume_path

        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/app/media", "size_gb": 1}, format="json")
        root = volume_path("dockra-disk-shop-media", 1)
        (root / "photos").mkdir(exist_ok=True)
        (root / "photos" / "a.jpg").write_bytes(b"original")
        return root

    def test_backup_restore_keeps_a_safety_copy(self):
        root = self._disk()
        made = self.client.post("/api/apps/shop/disks/media/backups", {}, format="json")
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(made.data["status"], "ready")
        bid = made.data["id"]
        download = self.client.get(f"/api/apps/shop/disks/media/backups/{bid}/download")
        self.assertEqual(download.status_code, 200)

        # User mistake: files were changed and deleted.
        (root / "photos" / "a.jpg").write_bytes(b"broken")
        (root / "junk.txt").write_text("x")
        restored = self.client.post(f"/api/apps/shop/disks/media/backups/{bid}/restore", {}, format="json")
        self.assertIn(restored.status_code, (200, 202), restored.data)
        self.assertEqual((root / "photos" / "a.jpg").read_bytes(), b"original")
        self.assertFalse((root / "junk.txt").exists())
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["photos"])

        # The state before the restore was also backed up and can be restored.
        rows = self.client.get("/api/apps/shop/disks/media/backups").data
        safety = next(row for row in rows if row["kind"] == "safety")
        self.client.post(f"/api/apps/shop/disks/media/backups/{safety['id']}/restore", {}, format="json")
        self.assertEqual((root / "photos" / "a.jpg").read_bytes(), b"broken")
        self.assertTrue((root / "junk.txt").exists())

    def test_backups_survive_disk_removal_and_restore_into_new_disk(self):
        from apps_core.models import DiskBackup

        self._disk()
        bid = self.client.post("/api/apps/shop/disks/media/backups", {}, format="json").data["id"]
        self.client.delete("/api/apps/shop/disks/media")
        backup = DiskBackup.objects.get(pk=bid)
        self.assertIsNone(backup.disk)
        self.assertTrue(Path(backup.path).is_file())
        root = self._disk()
        (root / "photos" / "a.jpg").write_bytes(b"new disk")
        self.client.post(f"/api/apps/shop/disks/media/backups/{bid}/restore", {}, format="json")
        self.assertEqual((root / "photos" / "a.jpg").read_bytes(), b"original")

    def test_auto_backups_are_scheduled_and_pruned(self):
        from apps_core.models import DiskBackup
        from engine import disk_backup

        self._disk()
        with override_settings(DOCKRA_DISK_BACKUP_KEEP=2):
            self.assertEqual(disk_backup.schedule_auto(), 1)
            self.assertEqual(disk_backup.schedule_auto(), 0)
            for _ in range(3):
                DiskBackup.objects.update(created_at=timezone.now() - timedelta(days=2))
                disk_backup.schedule_auto()
        auto = DiskBackup.objects.filter(kind="auto", status="ready")
        self.assertEqual(auto.count(), 2)
        self.assertTrue(all(Path(item.path).is_file() for item in auto))
        self.assertEqual(len(list((self.tmp / "media" / "backups" / "disks").iterdir())), 2)

    def test_viewer_cannot_download_or_restore(self):
        self._disk()
        bid = self.client.post("/api/apps/shop/disks/media/backups", {}, format="json").data["id"]
        self.member("watcher", "viewer")
        viewer = self.login("watcher", "Strong-pass-42")
        self.assertEqual(viewer.get(f"/api/apps/shop/disks/media/backups/{bid}/download").status_code, 403)
        self.assertEqual(viewer.post(f"/api/apps/shop/disks/media/backups/{bid}/restore", {}, format="json").status_code, 403)

    def test_restore_rejects_paths_outside_disk_and_keeps_modes(self):
        import io
        import tarfile

        from apps_core.models import DiskBackup

        root = self._disk()
        (root / "photos").chmod(0o777)
        good = self.client.post("/api/apps/shop/disks/media/backups", {}, format="json").data["id"]
        (root / "photos").chmod(0o755)
        self.client.post(f"/api/apps/shop/disks/media/backups/{good}/restore", {}, format="json")
        self.assertEqual((root / "photos").stat().st_mode & 0o777, 0o777)

        evil = self.tmp / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            info = tarfile.TarInfo("../../escaped.txt")
            info.size = 3
            tar.addfile(info, io.BytesIO(b"bad"))
        backup = DiskBackup.objects.create(
            app=App.objects.get(slug="shop"), disk_name="media", status="ready", path=str(evil)
        )
        res = self.client.post(f"/api/apps/shop/disks/media/backups/{backup.id}/restore", {}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual((root / "photos" / "a.jpg").read_bytes(), b"original")
        self.assertFalse(any(self.tmp.rglob("escaped.txt")))
        self.assertFalse(any(p.name.startswith(".dockra-") for p in root.iterdir()))


class DiskBackupSpaceGuardTests(_Base):
    """The reserve is enforced: a backup that would eat into it is refused and leaves no half-written archive."""

    def test_backup_refused_when_it_would_eat_the_reserve(self):
        from collections import namedtuple

        from engine.volumes import volume_path

        self.client.post("/api/apps/shop/disks", {"name": "media", "mount_to": "/app/media", "size_gb": 1}, format="json")
        root = volume_path("dockra-disk-shop-media", 1)
        (root / "a.bin").write_bytes(b"x" * 4096)
        usage = namedtuple("usage", "total used free")(10 * 1024**3, 9 * 1024**3, 1024**3)
        with override_settings(DOCKRA_RESERVED_DISK_GB=1), mock.patch("engine.disk_backup.shutil.disk_usage", return_value=usage):
            made = self.client.post("/api/apps/shop/disks/media/backups", {}, format="json")
        self.assertEqual(made.status_code, 400)
        self.assertIn("Not enough free space", made.data["detail"])
