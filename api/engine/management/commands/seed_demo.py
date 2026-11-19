"""Populate a local development instance with realistic sample data.

Everything is created through the public REST API (Django test client, signed in as the admin), so the
demo exercises the same validation, capacity checks and job pipeline as real usage. Usage history is the
only thing written directly, because it normally comes from the worker sampling `docker stats` every minute.

Refuses to run unless DOCKRA_DRIVER=fake: it must never touch a real server.
"""

import math
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.utils import timezone

APPS = [
    {"name": "Storefront", "slug": "storefront", "platform": "nextjs", "cpu": "1", "memory_mb": 1024},
    {"name": "Orders API", "slug": "orders-api", "platform": "django", "cpu": "0.5", "memory_mb": 512},
    {"name": "Docs site", "slug": "docs-site", "platform": "static", "cpu": "0.25", "memory_mb": 256},
    {"name": "Billing worker", "slug": "billing-worker", "platform": "node", "cpu": "0.5", "memory_mb": 512},
]
RELEASES = {
    "storefront": ["Initial release", "Add product search", "Faster image loading", "Checkout redesign"],
    "orders-api": ["Initial release", "Add order export endpoint", "Fix timezone in invoices"],
    "docs-site": ["Publish getting started guide", "Add API reference"],
    "billing-worker": ["Initial release"],
}
DATABASES = [
    {"name": "orders-db", "engine": "postgres", "version": "16", "link": "orders-api"},
    {"name": "cache", "engine": "redis", "version": "7.4", "link": "storefront"},
]


class Command(BaseCommand):
    help = "Fill a local fake-driver instance with sample apps, deployments, databases and usage history."

    def handle(self, *args, **options):
        if settings.DOCKRA_DRIVER != "fake":
            raise CommandError("seed_demo only runs with DOCKRA_DRIVER=fake")
        call_command("seed")
        from accounts.authentication import issue_access

        client = Client()
        admin = get_user_model().objects.get(username="admin")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {issue_access(admin).access_token}"}

        def call(method: str, path: str, data=None):
            response = getattr(client, method)(path, data or {}, content_type="application/json", **headers)
            if response.status_code >= 400:
                raise CommandError(f"{method.upper()} {path} -> {response.status_code}: {response.content[:300]!r}")
            return response.json() if response.content else {}

        for spec in APPS:
            if client.get(f"/api/apps/{spec['slug']}", **headers).status_code == 200:
                continue
            call("post", "/api/apps", spec)
            for message in RELEASES[spec["slug"]]:
                call("post", f"/api/apps/{spec['slug']}/deploy", {"message": message})
        call("put", "/api/apps/orders-api/env", {"items": [
            {"key": "DJANGO_SETTINGS_MODULE", "value": "orders.settings", "is_secret": False},
            {"key": "SECRET_KEY", "value": "demo-only-not-a-secret", "is_secret": True},
            {"key": "ALLOWED_HOSTS", "value": "*", "is_secret": False},
        ]})
        call("post", "/api/apps/storefront/disks", {"name": "media", "mount_to": "/app/public/uploads", "size_gb": 5})
        existing = {item["hostname"] for item in call("get", "/api/domains")}
        if "shop.example.com" not in existing:
            call("post", "/api/apps/storefront/domains", {"hostname": "shop.example.com"})
        call("post", "/api/apps/billing-worker/stop")

        known = {item["name"] for item in call("get", "/api/databases")}
        for spec in DATABASES:
            if spec["name"] in known:
                continue
            db = call("post", "/api/databases", {"name": spec["name"], "engine": spec["engine"], "version": spec["version"]})
            call("post", f"/api/databases/{db['id']}/link", {"app": spec["link"]})
            call("post", f"/api/databases/{db['id']}/backup")

        self._history()
        self.stdout.write(self.style.SUCCESS("Demo data ready. Sign in as admin (password from DOCKRA_ADMIN_PASSWORD, default: dockra)."))

    def _history(self):
        """Seven days of per-minute-like samples (one every 10 minutes) so charts have something to draw."""
        from apps_core.models import App
        from databases.models import Database
        from engine.models import MetricSample

        rng = random.Random(7)
        now = timezone.now()
        rows = []
        for app in App.objects.filter(status=App.Status.RUNNING):
            if MetricSample.objects.filter(app=app).exists():
                continue
            base = rng.uniform(8, 30)
            for step in range(7 * 24 * 6):
                at = now - timedelta(minutes=10 * step)
                wave = (math.sin(at.hour / 24 * 2 * math.pi - 1.5) + 1) / 2
                requests = int(40 + 260 * wave + rng.uniform(0, 30))
                rows.append(MetricSample(
                    app=app, at=at, cpu=min(100, int(base + 35 * wave + rng.uniform(0, 8))),
                    ram=int(35 + 20 * wave + rng.uniform(0, 5)), network_mbps=round(0.4 + 3 * wave + rng.uniform(0, 0.5), 2),
                    requests=requests, errors_5xx=int(requests * rng.choice([0, 0, 0, 0.004, 0.01])),
                    avg_ms=round(38 + 30 * wave + rng.uniform(0, 10), 1), p95_ms=round(120 + 90 * wave + rng.uniform(0, 40), 1),
                ))
        for database in Database.objects.all():
            if MetricSample.objects.filter(database=database).exists():
                continue
            for step in range(7 * 24 * 6):
                at = now - timedelta(minutes=10 * step)
                wave = (math.sin(at.hour / 24 * 2 * math.pi - 1.5) + 1) / 2
                rows.append(MetricSample(database=database, at=at, cpu=int(4 + 18 * wave + rng.uniform(0, 4)),
                                         ram=int(30 + 12 * wave), network_mbps=round(0.2 + wave, 2)))
        MetricSample.objects.bulk_create(rows, batch_size=2000)
