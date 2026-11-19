import asyncio
import gzip
import http.server
import json
import shutil
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import security
from accounts.models import ApiToken, Organization, TwoFactor
from apps_core.models import App, Deployment
from engine.models import Alert, Job, MetricSample


class _Base(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.override = override_settings(
            DOCKRA_DATA_ROOT=str(self.tmp), MEDIA_ROOT=self.tmp / "media", WORK_ROOT=self.tmp / "work"
        )
        self.override.enable()
        call_command("seed")
        self.client = APIClient()
        self.token = self.client.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.client.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)


class AuthTests(_Base):
    def test_two_factor_flow(self):
        setup = self.client.post("/api/auth/2fa/setup", {}, format="json").data
        self.assertTrue(setup["otpauth_url"].startswith("otpauth://totp/"))
        row = TwoFactor.objects.get()
        code = security._code_at(setup["secret"], int(timezone.now().timestamp()) // 30)
        self.assertEqual(self.client.post("/api/auth/2fa/confirm", {"code": "000000"}, format="json").status_code, 400)
        confirmed = self.client.post("/api/auth/2fa/confirm", {"code": code}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        recovery = confirmed.data["recovery_codes"]
        anon = APIClient()
        need = anon.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.assertEqual(need.status_code, 400)
        self.assertTrue(need.data["otp_required"])
        # The same code has already been used and is not accepted again.
        reused = anon.post("/api/auth/login", {"username": "admin", "password": "dockra", "otp": code}, format="json")
        self.assertEqual(reused.status_code, 400)
        ok = anon.post("/api/auth/login", {"username": "admin", "password": "dockra", "otp": recovery[0]}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        again = anon.post("/api/auth/login", {"username": "admin", "password": "dockra", "otp": recovery[0]}, format="json")
        self.assertEqual(again.status_code, 400)
        row.refresh_from_db()
        self.assertEqual(len(row.recovery_hashes), 7)
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT secret FROM accounts_twofactor")
            self.assertTrue(cursor.fetchone()[0].startswith("enc:"))

    @override_settings(DOCKRA_LOGIN_MAX_FAILS=3)
    def test_login_throttle(self):
        anon = APIClient()
        for _ in range(3):
            self.assertEqual(anon.post("/api/auth/login", {"username": "admin", "password": "x"}, format="json").status_code, 400)
        blocked = anon.post("/api/auth/login", {"username": "admin", "password": "dockra"}, format="json")
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(blocked.data["retry_after"], 0)

    def test_token_expiry(self):
        made = self.client.post("/api/auth/tokens", {"name": "ci", "expires_days": 30}, format="json")
        self.assertIsNotNone(made.data["expires_at"])
        forever = self.client.post("/api/auth/tokens", {"name": "x", "expires_days": None}, format="json")
        self.assertIsNone(forever.data["expires_at"])
        cli = APIClient()
        cli.credentials(HTTP_AUTHORIZATION=f"Bearer {made.data['token']}")
        self.assertEqual(cli.get("/api/apps").status_code, 200)
        ApiToken.objects.filter(pk=made.data["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(cli.get("/api/apps").status_code, 401)

    def test_weak_member_password_rejected(self):
        res = self.client.post("/api/members", {"username": "dev", "password": "123456", "role": "developer"}, format="json")
        self.assertEqual(res.status_code, 400)


class TerminalTests(_Base):
    def test_ticket_is_single_use_and_kind_bound(self):
        from engine.terminal import make_ticket, read_ticket

        ticket = make_ticket("terminal", 1, 2)
        self.assertEqual(read_ticket(ticket)["t"], 2)
        self.assertIsNone(read_ticket(ticket))
        self.assertIsNone(read_ticket(ticket + "x"))

    def test_ticket_endpoint_urls(self):
        app = App.objects.get(slug="shop")
        app.status = App.Status.RUNNING
        app.save()
        res = self.client.post("/api/apps/shop/terminal", {}, format="json", HTTP_HOST="1.2.3.4:8000")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data["url"].startswith("ws://1.2.3.4:8001/ws/terminal?ticket="))
        res = self.client.post("/api/apps/shop/terminal", {}, format="json", HTTP_HOST="api.example.com", HTTP_X_FORWARDED_PROTO="https")
        self.assertTrue(res.data["url"].startswith("wss://api.example.com/ws/terminal?"))

    def test_pty_shell_round_trip(self):
        from engine import terminal

        real_popen = terminal.subprocess.Popen

        def local_sh(args, **kwargs):
            return real_popen(["sh"], **kwargs)

        class FakeWs:
            def __init__(self):
                self.sent = []
                self.inbox = [json.dumps({"type": "resize", "cols": 80, "rows": 20}), b"echo dockra-$((40+2))\n", b"exit 3\n"]

            async def send(self, data):
                self.sent.append(data)

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(0.3)
                if not self.inbox:
                    await asyncio.sleep(5)
                    raise StopAsyncIteration
                return self.inbox.pop(0)

        ws = FakeWs()
        with mock.patch.object(terminal.subprocess, "Popen", side_effect=local_sh):
            asyncio.run(asyncio.wait_for(terminal._shell(ws, "cid"), 10))
        output = b"".join(item for item in ws.sent if isinstance(item, bytes))
        self.assertIn(b"dockra-42", output)
        self.assertEqual(json.loads(ws.sent[-1]), {"type": "exit", "code": 3})

    def test_tunnel_pipes_bytes(self):
        from engine import terminal

        async def scenario():
            async def echo(reader, writer):
                writer.write(b"pong:" + await reader.read(100))
                await writer.drain()
                writer.close()

            server = await asyncio.start_server(echo, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]

            class FakeWs:
                sent = []

                async def send(self, data):
                    self.sent.append(data)

                def __aiter__(self):
                    self.items = [b"ping"]
                    return self

                async def __anext__(self):
                    if self.items:
                        return self.items.pop()
                    await asyncio.sleep(1)
                    raise StopAsyncIteration

            ws = FakeWs()
            await terminal._tunnel(ws, ("127.0.0.1", port))
            server.close()
            return ws.sent

        self.assertIn(b"pong:ping", asyncio.run(scenario()))


class LogAndReleaseTests(_Base):
    def test_archive_list_read_and_search(self):
        from engine.logs import app_dir

        folder = app_dir("shop")
        folder.mkdir(parents=True)
        with gzip.open(folder / "20261008T010000Z-ab12-c0ffee.log.gz", "wt") as handle:
            handle.write("2026 boot ok\n2026 ERROR database gone\n")
        rows = self.client.get("/api/apps/shop/logs/archive").data
        self.assertEqual(rows[0]["release"], "ab12")
        body = self.client.get(f"/api/apps/shop/logs/archive/{rows[0]['name']}", HTTP_ACCEPT="text/plain")
        self.assertIn(b"database gone", b"".join(body.streaming_content))
        self.assertEqual(self.client.get("/api/apps/shop/logs/archive/..%2Fx.log.gz").status_code, 404)
        found = self.client.get("/api/apps/shop/logs/search?q=error").data
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["source"], rows[0]["name"])

    def test_release_command_reaches_driver(self):
        from engine.source import release_command

        self.assertEqual(release_command({"release": ["python manage.py migrate", "echo ok"]}), "python manage.py migrate && echo ok")
        with mock.patch("engine.runner.read_manifest", return_value={"release": "python manage.py migrate"}):
            res = self.client.post("/api/apps/shop/deploy", {"message": "r"}, format="json")
        self.assertIn("(fake) release: python manage.py migrate", res.data["deployment"]["log"])

    def test_docker_release_failure_keeps_old_version(self):
        from engine.drivers import DockerDriver

        app = App.objects.get(slug="shop")
        driver = DockerDriver()
        with mock.patch("engine.drivers._stream", side_effect=RuntimeError("migrate failed")), \
                mock.patch("engine.drivers._run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")), \
                mock.patch.object(DockerDriver, "_launch") as launch:
            with self.assertRaises(RuntimeError) as caught:
                driver._release(app, "img", "python manage.py migrate")
            self.assertIn("the current release was not touched", str(caught.exception))
            launch.assert_not_called()


class AlertQueueTests(_Base):
    def test_cpu_alert_fires_and_resolves(self):
        from engine.alerts import _check_usage

        app = App.objects.get(slug="shop")
        now = timezone.now()
        for minute in range(5):
            MetricSample.objects.create(app=app, at=now - timedelta(minutes=minute), cpu=97, ram=10)
        with mock.patch("engine.alerts.notify") as sent:
            _check_usage(app)
            _check_usage(app)
        self.assertEqual(Alert.objects.filter(kind="cpu", status="firing").count(), 1)
        self.assertEqual(sent.call_count, 1)
        listed = self.client.get("/api/alerts").data
        self.assertEqual(listed[0]["kind"], "cpu")
        MetricSample.objects.create(app=app, at=timezone.now(), cpu=5, ram=10)
        with mock.patch("engine.alerts.notify"):
            _check_usage(app)
        self.assertEqual(Alert.objects.get(kind="cpu").status, "resolved")

    def test_crash_loop_detection(self):
        from engine import alerts

        app = App.objects.get(slug="shop")
        with mock.patch("engine.alerts.notify"):
            alerts._check_restarts(app, 1)
            alerts._check_restarts(app, 5)
        self.assertTrue(Alert.objects.filter(kind="crash_loop", status="firing").exists())

    def test_same_app_jobs_never_run_together(self):
        from engine.runner import _claim

        app = App.objects.get(slug="shop")
        dep = Deployment.objects.create(app=app, number=1)
        Job.objects.create(kind="deploy", queue="build", status="running", target="app:shop", payload={"deployment_id": dep.id})
        waiting = Job.objects.create(kind="deploy", queue="build", target="app:shop", payload={})
        other = Job.objects.create(kind="deploy", queue="build", target="app:blog", payload={})
        self.assertEqual(_claim(["build"]), other.id)
        self.assertIsNone(_claim(["build"]))
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "queued")

    def test_enqueue_sets_target(self):
        res = self.client.post("/api/apps/shop/restart", {}, format="json")
        self.assertEqual(Job.objects.get(pk=res.data["job"]["id"]).target, "app:shop")


class CancelDeployTests(_Base):
    def _queued(self, number, status="queued"):
        app = App.objects.get(slug="shop")
        app.status = "deploying"
        app.save(update_fields=["status"])
        dep = Deployment.objects.create(app=app, number=number, status=status)
        job_status = "running" if status == "building" else "queued"
        job = Job.objects.create(
            kind="deploy", queue="build", status=job_status, target="app:shop", payload={"deployment_id": dep.id}
        )
        return app, dep, job

    def test_cancel_queued_ends_it_and_frees_the_queue(self):
        from engine.runner import _claim

        app, dep, job = self._queued(1)
        res = self.client.post(f"/api/apps/shop/deployments/{dep.id}/cancel", {}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["state"], "canceled")
        dep.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(dep.status, "canceled")
        self.assertEqual(job.status, "failed")
        self.assertTrue(job.cancel_requested)
        self.assertIsNone(_claim(["build"]))

    def test_cancel_building_marks_canceling_and_worker_finalizes(self):
        from engine import runner
        from engine.drivers import BuildCanceled

        app, dep, job = self._queued(2, status="building")
        res = self.client.post(f"/api/apps/shop/deployments/{dep.id}/cancel", {}, format="json")
        self.assertEqual(res.status_code, 202, res.content)
        self.assertEqual(res.data["state"], "canceling")
        job.refresh_from_db()
        self.assertTrue(job.cancel_requested)
        dep.refresh_from_db()
        self.assertEqual(dep.status, "building")  # no worker has picked it up yet

        def boom(_job):
            raise BuildCanceled("Canceled")

        with mock.patch.dict(runner.HANDLERS, {"deploy": boom}), mock.patch("engine.runner.notify"):
            runner._execute(job.id)
        job.refresh_from_db(); dep.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(dep.status, "canceled")

    def test_cancel_rejects_finished_deployment(self):
        app = App.objects.get(slug="shop")
        dep = Deployment.objects.create(app=app, number=3, status="running")
        res = self.client.post(f"/api/apps/shop/deployments/{dep.id}/cancel", {}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_stream_kills_process_when_cancel_requested(self):
        from engine.drivers import _stream, BuildCanceled

        with self.assertRaises(BuildCanceled):
            _stream(["sh", "-c", "sleep 30"], should_cancel=lambda: True)


class OffsiteTests(TestCase):
    def test_signed_put_reaches_s3_endpoint(self):
        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                received["path"] = self.path
                received["auth"] = self.headers["Authorization"]
                received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.handle_request, daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.dump"
            path.write_bytes(b"backup-bytes")
            with override_settings(
                DOCKRA_S3_ENDPOINT=f"http://127.0.0.1:{server.server_port}",
                DOCKRA_S3_BUCKET="bk",
                DOCKRA_S3_ACCESS_KEY="AK",
                DOCKRA_S3_SECRET_KEY="SK",
                DOCKRA_S3_PREFIX="dockra",
            ):
                from engine import offsite

                key = offsite.upload(path, "databases/x/a.dump")
        server.server_close()
        self.assertEqual(key, "dockra/databases/x/a.dump")
        self.assertEqual(received["path"], "/bk/dockra/databases/x/a.dump")
        self.assertTrue(received["auth"].startswith("AWS4-HMAC-SHA256 Credential=AK/"))
        self.assertEqual(received["body"], b"backup-bytes")


class CliDistributionTests(TestCase):
    def test_install_scripts_version_and_binaries(self):
        from django.test import Client

        with tempfile.TemporaryDirectory() as tmp:
            served = Path(tmp) / "cli"
            served.mkdir()
            (served / "dockra.py").write_text('__version__ = "9.1.0"\nprint(1)\n', encoding="utf-8")
            bins = Path(tmp) / "bin"
            bins.mkdir()
            (bins / "dockra-windows.exe").write_bytes(b"MZ")
            with override_settings(DOCKRA_CLI_DIR=str(served), DOCKRA_CLI_BIN_DIR=str(bins)):
                client = Client()
                version = client.get("/cli/version").json()
                self.assertEqual(version, {"version": "9.1.0", "binaries": ["dockra-windows.exe"]})
                script = client.get("/cli/dockra.py")
                self.assertEqual(script["X-Dockra-Version"], "9.1.0")
                import hashlib

                body = b"".join(script.streaming_content)
                self.assertEqual(script["X-Dockra-Sha256"], hashlib.sha256(body).hexdigest())
                sh = client.get("/cli/install.sh", HTTP_HOST="10.0.0.5:8000").content.decode()
                self.assertIn('BASE="http://10.0.0.5:8000"', sh)
                self.assertIn('>> "$rc"', sh)
                home = client.get("/", HTTP_HOST="api.local")
                self.assertEqual(home.status_code, 200)
                self.assertIn("curl -fsSL http://api.local/cli/install.sh | sh", home.content.decode())
                # The page loads nothing external: no fonts, no third-party origins.
                self.assertIn("default-src 'none'", home["Content-Security-Policy"])
                self.assertNotIn("font-src", home["Content-Security-Policy"])
                ps1 = client.get("/cli/install.ps1", HTTP_HOST="api.local").content.decode()
                self.assertIn('$Base = "http://api.local"', ps1)
                self.assertEqual(client.get("/cli/bin/dockra-windows.exe").status_code, 200)
                self.assertEqual(client.get("/cli/bin/dockra-linux").status_code, 404)
                self.assertEqual(client.get("/cli/bin/evil.sh").status_code, 404)

    def test_repo_cli_version_matches_package(self):
        from accounts.cli_dist import cli_version

        self.assertRegex(cli_version(), r"^\d+\.\d+\.\d+$")


class IsolatedNetworkTests(_Base):
    @override_settings(DOCKRA_APPS_DOMAIN="apps.example.com")
    def test_internal_apps_domain(self):
        made = self.client.post("/api/apps", {"name": "Internal", "slug": "inner"}, format="json")
        self.assertEqual(made.data["domains"][0]["hostname"], "inner.apps.example.com")
        self.assertEqual(made.data["domains"][0]["kind"], "sslip")
        extra = self.client.post("/api/apps/inner/domains", {"hostname": "x.apps.example.com"}, format="json")
        self.assertEqual(extra.data["kind"], "sslip")

    def test_tls_without_lets_encrypt(self):
        from engine.drivers import DockerDriver

        app = App.objects.get(slug="shop")
        with override_settings(DOCKRA_TLS_RESOLVER=""):
            labels = DockerDriver()._labels(app, "r1")
        self.assertIn("traefik.http.routers.shop-s.tls=true", labels)
        self.assertFalse(any("certresolver" in item for item in labels))
        labels = DockerDriver()._labels(app, "r1")
        self.assertIn("traefik.http.routers.shop-s.tls.certresolver=le", labels)

    def test_volume_script_runs_on_host_without_downloads(self):
        from engine import volumes

        with override_settings(DOCKRA_DRIVER="docker", DOCKRA_RESERVED_DISK_GB=0), \
                mock.patch("engine.volumes.shutil.disk_usage", return_value=mock.Mock(free=10 ** 13)), \
                mock.patch("engine.volumes._docker", return_value=mock.Mock(returncode=0)) as docker:
            volumes.ensure_volume("dockra-disk-shop-media", 1)
        args = docker.call_args.args[0]
        self.assertEqual(args[:5], ["run", "--rm", "-i", "--privileged", "--pid=host"])
        self.assertIn("nsenter", args)
        self.assertNotIn("build", [call.args[0][0] for call in docker.call_args_list])
        self.assertIn("mkfs.ext4", docker.call_args.kwargs["script"])


class ManifestDiskTests(TestCase):
    def test_manifest_without_size_keeps_existing_disk_size(self):
        from apps_core.models import Disk
        from engine.source import apply_manifest

        org = Organization.objects.create(name="m", slug="m")
        app = App.objects.create(organization=org, name="m", slug="mdisk")
        Disk.objects.create(app=app, name="media", mount_to="/app/media", size_gb=10)
        apply_manifest(app, {"disks": [{"name": "media", "mountTo": "/app/media"}, {"name": "new", "mountTo": "/new"}]})
        self.assertEqual(Disk.objects.get(app=app, name="media").size_gb, 10)
        self.assertEqual(Disk.objects.get(app=app, name="new").size_gb, 1)
        apply_manifest(app, {"disks": [{"name": "media", "mountTo": "/app/media", "sizeGb": 20}]})
        self.assertEqual(Disk.objects.get(app=app, name="media").size_gb, 20)


class CpuAverageTests(SimpleTestCase):
    def test_cpu_is_averaged_between_samples_not_a_spike(self):
        from engine import drivers

        drivers._CPU_SEEN.clear()
        totals = iter([10 * 10**9, 16 * 10**9])
        clock = iter([100.0, 160.0, 161.0])
        with mock.patch.object(drivers, "_cpu_total_ns", lambda cid: next(totals)), mock.patch.object(drivers.time, "monotonic", lambda: next(clock)):
            self.assertEqual(drivers._cpu_average("abc", 100.0), 100.0)
            self.assertAlmostEqual(drivers._cpu_average("abc", 100.0), 10.0)
            # Back-to-back calls (alerts, then stats) do not move the baseline.
            self.assertAlmostEqual(drivers._cpu_average("abc", 100.0), 10.0)
        drivers._CPU_SEEN.clear()
        with mock.patch.object(drivers, "_cpu_total_ns", lambda cid: None):
            self.assertEqual(drivers._cpu_average("abc", 42.0), 42.0)
