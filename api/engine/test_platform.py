"""Platform guarantees that cut across modules: secrets, tenant isolation, job concurrency and recovery."""

from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Membership, Organization
from apps_core.models import App, Deployment, EnvVar
from engine.models import Job


def _client(username: str, password: str) -> APIClient:
    client = APIClient()
    res = client.post("/api/auth/login", {"username": username, "password": password}, format="json")
    assert res.status_code == 200, res.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
    return client


class _Base(TestCase):
    def setUp(self):
        call_command("seed")
        self.admin = _client("admin", "dockra")
        self.admin.post("/api/apps", {"name": "Shop", "slug": "shop"}, format="json")

    def _member(self, username: str, role: str) -> APIClient:
        made = self.admin.post("/api/members", {"username": username, "password": f"{username}-pass-1", "role": role}, format="json")
        self.assertEqual(made.status_code, 201, made.content)
        return _client(username, f"{username}-pass-1")


class SecretHandlingTests(_Base):
    def test_secret_values_are_encrypted_at_rest(self):
        self.admin.put("/api/apps/shop/env", {"items": [{"key": "API_KEY", "value": "s3cr3t-value", "is_secret": True}]}, format="json")
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM apps_core_envvar WHERE key = %s", ["API_KEY"])
            stored = cursor.fetchone()[0]
        self.assertNotIn("s3cr3t-value", stored)
        self.assertEqual(EnvVar.objects.get(key="API_KEY").value, "s3cr3t-value")

    def test_viewers_see_masked_secrets_and_cannot_write(self):
        self.admin.put("/api/apps/shop/env", {"items": [
            {"key": "API_KEY", "value": "s3cr3t-value", "is_secret": True},
            {"key": "DEBUG", "value": "0", "is_secret": False},
        ]}, format="json")
        viewer = self._member("vera", "viewer")
        rows = {row["key"]: row["value"] for row in viewer.get("/api/apps/shop/env").data}
        self.assertEqual(rows["DEBUG"], "0")
        self.assertNotIn("s3cr3t", rows["API_KEY"])
        denied = viewer.put("/api/apps/shop/env", {"items": []}, format="json")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(EnvVar.objects.count(), 2)

    def test_echoed_mask_never_overwrites_the_real_secret(self):
        self.admin.put("/api/apps/shop/env", {"items": [{"key": "API_KEY", "value": "s3cr3t-value", "is_secret": True}]}, format="json")
        self.admin.put("/api/apps/shop/env", {"items": [{"key": "API_KEY", "value": "••••••", "is_secret": True}]}, format="json")
        self.assertEqual(EnvVar.objects.get(key="API_KEY").value, "s3cr3t-value")


class TenantIsolationTests(_Base):
    def setUp(self):
        super().setUp()
        other = Organization.objects.create(name="Other", slug="other")
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("mallory", password="mallory-pass-1")
        Membership.objects.create(organization=other, user=user, role=Membership.Role.OWNER)
        self.outsider = _client("mallory", "mallory-pass-1")

    def test_other_organization_cannot_see_or_touch_apps(self):
        self.assertEqual(self.outsider.get("/api/apps").data, [])
        self.assertEqual(self.outsider.get("/api/apps/shop").status_code, 404)
        self.assertEqual(self.outsider.get("/api/apps/shop/env").status_code, 404)
        self.assertEqual(self.outsider.post("/api/apps/shop/deploy", {"message": "x"}, format="json").status_code, 404)
        self.assertEqual(self.outsider.delete("/api/apps/shop").status_code, 404)
        self.assertTrue(App.objects.filter(slug="shop").exists())

    def test_other_organization_cannot_reach_databases(self):
        db = self.admin.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json").data
        self.assertEqual(self.outsider.get(f"/api/databases/{db['id']}").status_code, 404)
        self.assertEqual(self.outsider.post(f"/api/databases/{db['id']}/link", {"app": "shop"}, format="json").status_code, 404)



@mock.patch("django.db.close_old_connections")
class MultiOrganizationWebSocketTests(_Base):
    """Terminal, live logs and tunnels use the membership of the target's own organization, not the user's first one."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        other = Organization.objects.create(name="Other", slug="other")
        self.user = get_user_model().objects.create_user("mallory", password="mallory-pass-1")
        # Created first, so it is the user's "first" membership.
        Membership.objects.create(organization=other, user=self.user, role=Membership.Role.OWNER)
        self.home = Organization.objects.get(slug="dockra")
        self.app = App.objects.get(slug="shop")
        db = self.admin.post("/api/databases", {"name": "shop-db", "engine": "postgres"}, format="json").data
        self.database_id = db["id"]

    def _join(self, role):
        Membership.objects.create(organization=self.home, user=self.user, role=role)

    def test_developer_in_second_organization_gets_access(self, _close):
        from engine import terminal

        self._join(Membership.Role.DEVELOPER)
        target = {"u": self.user.id, "t": self.app.id}
        self.assertEqual(terminal._load_logs(target), (self.app, ""))
        # The fake driver has no container: access passed and only the container is missing.
        self.assertEqual(terminal._load_shell(target)[1], "The app container is not running")
        address, error = terminal._load_db({"u": self.user.id, "t": self.database_id})
        self.assertEqual(error, "")
        self.assertIsNotNone(address)

    def test_role_comes_from_the_targets_organization(self, _close):
        from engine import terminal

        # Owner elsewhere, viewer here: the viewer role applies.
        self._join(Membership.Role.VIEWER)
        self.assertEqual(terminal._load_shell({"u": self.user.id, "t": self.app.id})[1], "Viewers have no terminal access")
        self.assertEqual(terminal._load_db({"u": self.user.id, "t": self.database_id})[1], "Viewers have no database access")

    def test_non_member_is_still_denied(self, _close):
        from engine import terminal

        target = {"u": self.user.id, "t": self.app.id}
        self.assertEqual(terminal._load_logs(target)[1], "Access denied")
        self.assertEqual(terminal._load_shell(target)[1], "Access denied")
        self.assertEqual(terminal._load_db({"u": self.user.id, "t": self.database_id})[1], "Access denied")
        self.assertEqual(terminal._load_logs({"u": self.user.id, "t": 999999})[1], "Access denied")

@override_settings(DOCKRA_EAGER=False)
class JobConcurrencyTests(_Base):
    def test_two_deploys_of_one_app_never_run_together(self):
        from engine.runner import _claim

        self.admin.post("/api/apps", {"name": "Blog", "slug": "blog"}, format="json")
        for slug in ("shop", "shop", "blog"):
            res = self.admin.post(f"/api/apps/{slug}/deploy", {"message": "go"}, format="json")
            self.assertIn(res.status_code, (200, 201, 202), res.content)
        first = _claim(["build"])
        second = _claim(["build"])
        third = _claim(["build"])
        claimed = list(Job.objects.filter(pk__in=[first, second]).values_list("target", flat=True))
        # The second shop deploy is skipped while the first runs; the blog deploy is free to start.
        self.assertEqual(len(set(claimed)), 2)
        self.assertIsNone(third)
        self.assertEqual(Job.objects.filter(status=Job.Status.QUEUED).count(), 1)

    def test_a_job_is_claimed_exactly_once(self):
        from engine.runner import _claim

        self.admin.post("/api/apps/shop/deploy", {"message": "go"}, format="json")
        job = Job.objects.get(status=Job.Status.QUEUED)
        self.assertEqual(_claim(["build"]), job.id)
        # A second worker racing for the same row finds nothing left to claim.
        self.assertIsNone(_claim(["build"]))
        self.assertEqual(Job.objects.filter(status=Job.Status.RUNNING).count(), 1)


@override_settings(DOCKRA_EAGER=False, DOCKRA_JOB_STALE_SECONDS=60)
class FailedJobRecoveryTests(_Base):
    def test_jobs_of_a_dead_worker_fail_and_free_the_app(self):
        from engine.runner import _claim, recover_stale_jobs

        self.admin.post("/api/apps/shop/deploy", {"message": "go"}, format="json")
        job_id = _claim(["build"])
        Job.objects.filter(pk=job_id).update(heartbeat_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(recover_stale_jobs(), 1)
        job = Job.objects.get(pk=job_id)
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn("worker stopped", job.log)
        self.assertEqual(Deployment.objects.get(app__slug="shop").status, Deployment.Status.FAILED)
        # Recovery is idempotent: running it again changes nothing.
        self.assertEqual(recover_stale_jobs(), 0)
        # The app is no longer blocked: a retry is accepted and claimable.
        self.admin.post("/api/apps/shop/deploy", {"message": "retry"}, format="json")
        self.assertIsNotNone(_claim(["build"]))

    def test_failed_build_keeps_the_previous_release_live(self):
        from engine import runner

        with override_settings(DOCKRA_EAGER=True):
            self.admin.post("/api/apps/shop/deploy", {"message": "v1"}, format="json")
        good = Deployment.objects.get(app__slug="shop")
        self.assertEqual(good.status, Deployment.Status.RUNNING)
        self.admin.post("/api/apps/shop/deploy", {"message": "broken"}, format="json")
        job_id = runner._claim(["build"])
        with mock.patch.object(runner.get_driver().__class__, "build_and_run", side_effect=RuntimeError("build failed")), mock.patch("engine.runner.notify"):
            runner._execute(job_id)
        good.refresh_from_db()
        broken = Deployment.objects.exclude(pk=good.pk).get()
        self.assertEqual(broken.status, Deployment.Status.FAILED)
        self.assertEqual(good.status, Deployment.Status.RUNNING)
        self.assertEqual(App.objects.get(slug="shop").status, App.Status.RUNNING)


class CliCredentialFileTests(TestCase):
    def test_login_file_is_private_and_env_token_wins(self):
        import importlib.util
        import os
        import stat
        import tempfile
        from pathlib import Path

        from django.conf import settings

        spec = importlib.util.spec_from_file_location("dockra_cli", Path(settings.DOCKRA_CLI_DIR) / "dockra.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as home:
            cli.CONFIG = Path(home) / ".dockra.json"
            cli._save_login("https://api.example.com/", "secret-token")
            self.assertEqual(stat.S_IMODE(os.stat(cli.CONFIG).st_mode), 0o600)
            self.assertEqual(cli.load_config()["token"], "secret-token")
            with mock.patch.dict(os.environ, {"DOCKRA_TOKEN": "ci-token", "DOCKRA_API": "https://ci.example.com"}):
                self.assertEqual(cli.load_config(), {"api": "https://ci.example.com", "token": "ci-token"})
