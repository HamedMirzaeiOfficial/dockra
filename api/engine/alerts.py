"""Periodic health checks for apps and the host. Each alert is notified once when it fires and once when it resolves."""

import http.client
import shutil
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from apps_core.models import App
from engine.models import Alert
from engine.notify import notify

_restarts: dict[str, list[tuple[float, int]]] = {}
_down: dict[str, int] = {}


def _set(kind: str, firing: bool, message: str, app: App | None = None, subject: str = "") -> None:
    current = Alert.objects.filter(kind=kind, app=app, subject=subject, status=Alert.Status.FIRING).first()
    name = app.slug if app else "server"
    if firing and current is None:
        Alert.objects.create(kind=kind, app=app, subject=subject, message=message[:400])
        notify(f"alert_{kind}", f"🚨 {name}: {message}", app=app.slug if app else None, kind=kind)
    elif firing and current is not None and current.message != message[:400]:
        current.message = message[:400]
        current.save(update_fields=["message"])
    elif not firing and current is not None:
        current.status = Alert.Status.RESOLVED
        current.resolved_at = timezone.now()
        current.save(update_fields=["status", "resolved_at"])
        notify(f"alert_{kind}_resolved", f"✅ {name}: {current.get_kind_display()} resolved", app=app.slug if app else None, kind=kind)


def _check_usage(app: App) -> None:
    window = settings.DOCKRA_ALERT_MINUTES
    samples = list(
        app.metric_samples.filter(at__gte=timezone.now() - timedelta(minutes=window + 1)).order_by("-at")[:window]
    )
    threshold = settings.DOCKRA_ALERT_PERCENT
    for kind, field, label in ((Alert.Kind.CPU, "cpu", "CPU"), (Alert.Kind.MEMORY, "ram", "RAM")):
        values = [getattr(row, field) for row in samples]
        if len(values) >= window and min(values) >= threshold:
            _set(kind, True, f"{label} above {threshold}% for the last {window} minutes", app)
        elif values and values[0] < threshold - 10:
            _set(kind, False, "", app)


def _check_errors(app: App) -> None:
    """5xx error rate over the last few minutes; no alert below 20 requests so a single error does not page anyone."""
    window = settings.DOCKRA_ALERT_MINUTES
    samples = list(
        app.metric_samples.filter(at__gte=timezone.now() - timedelta(minutes=window + 1)).values_list("requests", "errors_5xx")
    )
    requests = sum(row[0] for row in samples)
    errors = sum(row[1] for row in samples)
    limit = settings.DOCKRA_ERROR_RATE_PERCENT
    rate = errors * 100 / requests if requests else 0
    if requests >= 20 and rate >= limit:
        _set(Alert.Kind.ERRORS, True, f"{rate:.0f}% of requests returned 5xx in the last {window} minutes ({errors} of {requests})", app)
    elif rate < limit / 2:
        _set(Alert.Kind.ERRORS, False, "", app)


def _check_restarts(app: App, count: int | None) -> None:
    if count is None:
        return
    now = time.monotonic()
    history = [item for item in _restarts.get(app.slug, []) if now - item[0] <= 600]
    history.append((now, count))
    _restarts[app.slug] = history
    grown = count - history[0][1]
    if grown >= 3:
        _set(Alert.Kind.CRASH_LOOP, True, f"{grown} restarts in 10 minutes; check the logs", app)
    elif len(history) > 1 and grown == 0 and history[-1][0] - history[0][0] >= 540:
        _set(Alert.Kind.CRASH_LOOP, False, "", app)


def _check_disks(app: App) -> None:
    from engine.volumes import data_root

    for disk in app.disks.all():
        path = data_root() / "mnt" / f"dockra-disk-{app.slug}-{disk.name}" / "data"
        if not path.is_dir():
            continue
        usage = shutil.disk_usage(path)
        percent = round(usage.used * 100 / usage.total) if usage.total else 0
        _set(
            Alert.Kind.DISK,
            percent >= settings.DOCKRA_ALERT_PERCENT,
            f"Disk {disk.name} is {percent}% full",
            app,
            subject=disk.name,
        )


def _probe(hostname: str) -> bool:
    """Request through Traefik so it does not depend on external DNS. 5xx means the app is not responding."""
    target = urlsplit(settings.DOCKRA_PROBE_URL)
    try:
        conn = http.client.HTTPConnection(target.hostname, target.port or 80, timeout=5)
        conn.request("GET", "/", headers={"Host": hostname, "User-Agent": "dockra-uptime"})
        status = conn.getresponse().status
        conn.close()
        return status < 500
    except OSError:
        return False


def _check_up(app: App) -> None:
    if not settings.DOCKRA_PROBE_URL:
        return
    domain = app.domains.filter(is_primary=True).first() or app.domains.first()
    if domain is None:
        return
    if _probe(domain.hostname):
        _down[app.slug] = 0
        _set(Alert.Kind.DOWN, False, "", app)
        return
    _down[app.slug] = _down.get(app.slug, 0) + 1
    if _down[app.slug] >= 3:
        _set(Alert.Kind.DOWN, True, f"{domain.hostname} failed to respond three times in a row", app)


def _check_host() -> None:
    from engine.volumes import data_root

    usage = shutil.disk_usage(data_root())
    percent = round(usage.used * 100 / usage.total) if usage.total else 0
    _set(Alert.Kind.HOST_DISK, percent >= settings.DOCKRA_ALERT_PERCENT, f"Host disk is {percent}% full")
    _check_host_sharing()


def _check_host_sharing() -> None:
    """With overcommit, reservations exceed capacity; this alert is based on real host usage, not reservations."""
    from engine.resources import _memory_mb, host_memory_used, host_pressure

    used = host_memory_used()
    if used is not None:
        total = _memory_mb()
        percent = round(used * 100 / total) if total else 0
        limit = settings.DOCKRA_MEMORY_REAL_PERCENT
        if percent >= limit:
            _set(Alert.Kind.HOST_MEMORY, True, f"Host memory is {percent}% used ({used} of {total} MB)")
        elif percent < limit - 5:
            _set(Alert.Kind.HOST_MEMORY, False, "")
    pressure = host_pressure("cpu")
    if pressure is not None:
        limit = settings.DOCKRA_CPU_PRESSURE_PERCENT
        if pressure >= limit:
            _set(Alert.Kind.HOST_CPU, True, f"Processes waited for CPU {pressure:.0f}% of the last minute; apps only get their guaranteed share")
        elif pressure < limit / 2:
            _set(Alert.Kind.HOST_CPU, False, "")


def check_all() -> None:
    from engine.drivers import get_driver

    driver = get_driver()
    restarts = driver.restart_counts() if hasattr(driver, "restart_counts") else {}
    running = App.objects.filter(status=App.Status.RUNNING).prefetch_related("disks", "domains")
    for app in running:
        _check_usage(app)
        _check_errors(app)
        _check_restarts(app, restarts.get(app.slug))
        _check_disks(app)
        _check_up(app)
    # A stopped app does not keep alerts firing.
    stale = Alert.objects.filter(status=Alert.Status.FIRING, app__isnull=False).exclude(app__status=App.Status.RUNNING)
    stale.update(status=Alert.Status.RESOLVED, resolved_at=timezone.now())
    _check_host()
    Alert.objects.filter(status=Alert.Status.RESOLVED, resolved_at__lt=timezone.now() - timedelta(days=30)).delete()
