from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps_core.models import App
from engine.drivers import cpu_burst, get_driver
from engine.models import MetricSample

RANGES = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
MAX_POINTS = 240
FIELDS = ("at", "cpu", "ram", "network_mbps", "requests", "errors_5xx", "avg_ms", "p95_ms")


def _shape(app: App, sample) -> dict:
    quiet = app.status in (App.Status.STOPPED, App.Status.FAILED)
    live = None if quiet else sample
    requests = live.requests if live else 0
    errors = live.errors_5xx if live else 0
    return {
        "cpu": live.cpu if live else 0,
        "cpu_cores": float(app.cpu),
        "cpu_burst": cpu_burst(app.cpu),
        "ram": live.ram if live else 0,
        "memory_mb": app.memory_mb,
        "network_mbps": live.network_mbps if live else 0.0,
        "instances": 0 if quiet else app.instances,
        "requests_per_min": requests,
        "errors_5xx_per_min": errors,
        "error_rate": round(errors * 100 / requests, 1) if requests else 0,
        "avg_ms": live.avg_ms if live else None,
        "p95_ms": live.p95_ms if live else None,
    }


def metrics_for(app: App) -> dict:
    """Read the latest per-minute sample from the worker (the API never runs docker stats itself)."""
    recent = None
    if app.status not in (App.Status.STOPPED, App.Status.FAILED):
        recent = app.metric_samples.filter(at__gte=timezone.now() - timedelta(minutes=3)).order_by("-at").first()
    return _shape(app, recent)


def database_metrics_for(database) -> dict:
    """Like metrics_for, for databases; disk usage is read directly from the volume."""
    from databases.models import Database

    live = None
    if database.status == Database.Status.RUNNING:
        live = database.metric_samples.filter(at__gte=timezone.now() - timedelta(minutes=3)).order_by("-at").first()
    return {
        "cpu": live.cpu if live else 0,
        "cpu_cores": float(database.cpu or 0.5),
        "cpu_burst": cpu_burst(database.cpu or 0.5),
        "ram": live.ram if live else 0,
        "memory_mb": database.memory_mb,
        "network_mbps": live.network_mbps if live else 0.0,
        **_database_disk(database),
    }


def _database_disk(database) -> dict:
    import shutil

    from engine.volumes import data_root

    path = data_root() / "mnt" / f"dockra-dbvol-{database.name}" / "data"
    if settings.DOCKRA_DRIVER != "docker" or not path.is_dir():
        return {"disk": None, "disk_used_mb": None, "disk_gb": database.disk_gb}
    usage = shutil.disk_usage(path)
    return {
        "disk": round(usage.used * 100 / usage.total) if usage.total else 0,
        "disk_used_mb": usage.used // (1024 * 1024),
        "disk_gb": database.disk_gb,
    }


def series_for(target, range_key: str) -> list[dict]:
    """Time series of an app or database; both have related_name=metric_samples."""
    span = RANGES.get(range_key, RANGES["1h"])
    since = timezone.now() - span
    rows = list(target.metric_samples.filter(at__gte=since).values_list(*FIELDS))
    if not rows:
        return []
    bucket = max(span.total_seconds() / MAX_POINTS, 60)
    out: list[dict] = []
    group: list = []
    start = None
    for row in rows:
        slot = int((row[0] - since).total_seconds() // bucket)
        if start is not None and slot != start:
            out.append(_avg(group))
            group = []
        start = slot
        group.append(row)
    if group:
        out.append(_avg(group))
    return out


def _mean(values) -> float | None:
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None


def _avg(group: list) -> dict:
    n = len(group)
    # Mean response time weighted by request count; p95 is the max of the samples (pessimistic).
    weighted = [(row[6], row[4]) for row in group if row[6] is not None and row[4]]
    total = sum(count for _, count in weighted)
    return {
        "t": group[-1][0].isoformat(),
        "cpu": round(sum(row[1] for row in group) / n),
        "ram": round(sum(row[2] for row in group) / n),
        "network_mbps": round(sum(row[3] for row in group) / n, 2),
        "requests": round(sum(row[4] for row in group) / n, 1),
        "errors_5xx": round(sum(row[5] for row in group) / n, 1),
        "avg_ms": round(sum(ms * count for ms, count in weighted) / total, 1) if total else None,
        "p95_ms": max((row[7] for row in group if row[7] is not None), default=None),
    }


def sample_all() -> int:
    """One sample for every running app: usage from docker stats and requests from Traefik."""
    from engine.traffic import scrape

    driver = get_driver()
    rows = driver.usage_all()
    traffic = scrape()
    now = timezone.now()
    created = []
    for app in App.objects.filter(status=App.Status.RUNNING):
        usage = driver.usage(app, rows) if rows else None
        hits = traffic.get(app.slug, {})
        if usage is None and not hits:
            continue
        usage = usage or {"cpu": 0, "ram": 0, "network_mbps": 0.0}
        created.append(
            MetricSample(
                app=app,
                at=now,
                cpu=usage["cpu"],
                ram=usage["ram"],
                network_mbps=usage["network_mbps"],
                requests=hits.get("requests", 0),
                errors_5xx=hits.get("errors_5xx", 0),
                avg_ms=hits.get("avg_ms"),
                p95_ms=hits.get("p95_ms"),
            )
        )
    from databases.models import Database

    for database in Database.objects.filter(status=Database.Status.RUNNING):
        usage = driver.database_usage(database, rows) if rows else None
        if usage is None:
            continue
        created.append(
            MetricSample(database=database, at=now, cpu=usage["cpu"], ram=usage["ram"], network_mbps=usage["network_mbps"])
        )
    MetricSample.objects.bulk_create(created)
    MetricSample.objects.filter(at__lt=now - timedelta(days=settings.DOCKRA_METRICS_DAYS)).delete()
    return len(created)
