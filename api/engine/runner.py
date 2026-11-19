import os
import re
import threading
from contextlib import contextmanager
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from apps_core.models import App, Deployment
from audit.models import AuditLog
from databases.models import Backup, Database
from engine.drivers import BACKUP_SUFFIX, BuildCanceled, get_driver
from engine.models import Job
from engine.notify import notify
from engine.resources import app_demand, ensure_capacity
from engine.source import (
    apply_manifest,
    detect_runtime,
    discard_source,
    ensure_dockerfile,
    prepare_source,
    read_manifest,
    release_command,
)


def _touch_log(deployment: Deployment, line: str) -> None:
    deployment.log = (deployment.log + "\n" + line).strip()
    if len(deployment.log) > 120_000:
        deployment.log = deployment.log[-100_000:]
    deployment.save(update_fields=["log"])


def _audit(job: Job, action: str, target: str, detail: str = "") -> None:
    if job.organization_id is None:
        return
    AuditLog.objects.create(
        organization_id=job.organization_id,
        actor=job.actor,
        action=action,
        target=target,
        detail=detail[:400],
    )


def _fail_related(job: Job, message: str) -> None:
    payload = job.payload or {}
    if job.kind in (Job.Kind.DEPLOY, Job.Kind.ROLLBACK) and payload.get("deployment_id"):
        dep = Deployment.objects.select_related("app").filter(pk=payload["deployment_id"]).first()
        if dep:
            dep.status = Deployment.Status.FAILED
            dep.finished_at = timezone.now()
            dep.log = (dep.log + "\n" + message).strip()
            dep.save(update_fields=["status", "finished_at", "log"])
            app = dep.app
            # If the previous release is still up (zero-downtime deploy), the app has not gone down.
            alive = bool(app.current_image) and app.deployments.filter(status=Deployment.Status.RUNNING).exists()
            app.status = App.Status.RUNNING if alive else App.Status.FAILED
            app.save(update_fields=["status"])
            notify("deploy_failed", f"❌ {app.slug} v{dep.number} failed\n{message[-500:]}", app=app.slug)
    elif job.kind in (Job.Kind.BACKUP, Job.Kind.RESTORE) and payload.get("backup_id"):
        if job.kind == Job.Kind.BACKUP:
            Backup.objects.filter(pk=payload["backup_id"]).update(
                status=Backup.Status.FAILED, error=message[-2000:], finished_at=timezone.now()
            )
        name = Backup.objects.filter(pk=payload["backup_id"]).values_list("database__name", flat=True).first() or ""
        notify(f"{job.kind}_failed", f"❌ {job.get_kind_display()} {name} failed\n{message[-500:]}")
    elif job.kind == Job.Kind.DISK_BACKUP and payload.get("disk_backup_id"):
        from apps_core.models import DiskBackup

        DiskBackup.objects.filter(pk=payload["disk_backup_id"]).update(
            status=DiskBackup.Status.FAILED, error=message[-2000:], finished_at=timezone.now()
        )
        notify("disk_backup_failed", f"❌ disk backup failed\n{message[-500:]}")
    elif job.kind == Job.Kind.DISK_IMPORT:
        if payload.get("path"):
            Path(payload["path"]).unlink(missing_ok=True)
        if payload.get("ftp"):
            Job.objects.filter(pk=job.pk).update(payload={**payload, "ftp": "redacted"})
        notify("disk_import_failed", f"❌ disk import failed\n{message[-500:]}")
    elif job.kind == Job.Kind.DISK_RESTORE:
        notify("disk_restore_failed", f"❌ disk restore failed\n{message[-500:]}")
    elif job.kind in (Job.Kind.PROVISION_DB, Job.Kind.DB_START, Job.Kind.DB_RESIZE) and payload.get("database_id"):
        Database.objects.filter(pk=payload["database_id"]).update(status=Database.Status.FAILED)
    elif job.kind == Job.Kind.IMPORT_DB and payload.get("database_id"):
        # If the database was created and only the restore failed, it stays up so the restore can be retried.
        Database.objects.filter(pk=payload["database_id"], status=Database.Status.PROVISIONING).update(status=Database.Status.FAILED)
        if payload.get("path"):
            Path(payload["path"]).unlink(missing_ok=True)
        notify("import_db_failed", f"❌ import {payload.get('name', '')} failed\n{message[-500:]}")
    elif job.kind == Job.Kind.START and payload.get("app_id"):
        App.objects.filter(pk=payload["app_id"]).update(status=App.Status.FAILED)
    elif job.kind in (Job.Kind.RESTART, Job.Kind.SYNC) and payload.get("app_id"):
        app = App.objects.filter(pk=payload["app_id"]).first()
        if app and app.status == App.Status.DEPLOYING:
            app.status = App.Status.RUNNING if app.current_image else App.Status.FAILED
            app.save(update_fields=["status"])


def _settle_app_after_cancel(app: App) -> None:
    # If the previous release is still up the app stays on it; otherwise only the deployment status is released.
    if app.status != App.Status.DEPLOYING:
        return
    alive = bool(app.current_image) and app.deployments.filter(status=Deployment.Status.RUNNING).exists()
    app.status = App.Status.RUNNING if alive else App.Status.STOPPED
    app.save(update_fields=["status"])


def _cancel_related(job: Job) -> None:
    payload = job.payload or {}
    if job.kind not in (Job.Kind.DEPLOY, Job.Kind.ROLLBACK) or not payload.get("deployment_id"):
        return
    dep = Deployment.objects.select_related("app").filter(pk=payload["deployment_id"]).first()
    if not dep:
        return
    dep.status = Deployment.Status.CANCELED
    dep.finished_at = timezone.now()
    dep.log = (dep.log + "\nCanceled").strip()
    dep.save(update_fields=["status", "finished_at", "log"])
    _settle_app_after_cancel(dep.app)
    notify("deploy_canceled", f"🚫 {dep.app.slug} v{dep.number} canceled", app=dep.app.slug)


def request_cancel(dep: Deployment) -> str:
    """Cancel a queued or building deployment. Returns canceled or canceling."""
    jobs = list(
        Job.objects.filter(kind__in=[Job.Kind.DEPLOY, Job.Kind.ROLLBACK], status__in=[Job.Status.QUEUED, Job.Status.RUNNING])
        .filter(payload__deployment_id=dep.id)
    )
    Job.objects.filter(pk__in=[j.pk for j in jobs]).update(cancel_requested=True)
    running = any(j.status == Job.Status.RUNNING for j in jobs)
    if running or dep.status == Deployment.Status.BUILDING:
        # The job is running; the build watcher thread kills it and the status then becomes CANCELED.
        return "canceling"
    # Still queued: finish it right here so no worker picks it up.
    Job.objects.filter(pk__in=[j.pk for j in jobs], status=Job.Status.QUEUED).update(
        status=Job.Status.FAILED, finished_at=timezone.now()
    )
    dep.status = Deployment.Status.CANCELED
    dep.finished_at = timezone.now()
    dep.log = (dep.log + "\nCanceled").strip()
    dep.save(update_fields=["status", "finished_at", "log"])
    _settle_app_after_cancel(dep.app)
    notify("deploy_canceled", f"🚫 {dep.app.slug} v{dep.number} canceled", app=dep.app.slug)
    return "canceled"


def _deploy(job: Job) -> None:
    dep = Deployment.objects.select_related("app").get(pk=job.payload["deployment_id"])
    app = dep.app
    dep.status = Deployment.Status.BUILDING
    dep.save(update_fields=["status"])
    if settings.DOCKRA_DRIVER == "docker":
        _touch_log(dep, "Preparing a Docker image")
    else:
        _touch_log(dep, "Preparing the build without Docker; this API is not the server driver")
    try:
        source = prepare_source(dep, on_log=lambda line: _touch_log(dep, line))
        manifest = read_manifest(source)
        apply_manifest(app, manifest)
        app.refresh_from_db()
        ensure_capacity(*app_demand(app), skip_app_id=app.id)
        ensure_dockerfile(source, app, manifest)
        declared_runtime = detect_runtime(source)
        if app.instances < 1:
            app.instances = 1
            app.save(update_fields=["instances"])
        _touch_log(dep, f"Building image dockra/{app.slug}:v{dep.number}")
        pending: list[str] = []
        last_save = time.monotonic()

        def on_build_line(line: str) -> None:
            nonlocal last_save
            pending.append(line)
            now = time.monotonic()
            if len(pending) >= 15 or now - last_save >= 1:
                _touch_log(dep, "\n".join(pending))
                pending.clear()
                last_save = now

        try:
            image = get_driver().build_and_run(
                app,
                dep,
                source,
                on_log=on_build_line,
                release=release_command(manifest),
                should_cancel=lambda: Job.objects.filter(pk=job.pk, cancel_requested=True).exists(),
            )
        finally:
            if pending:
                _touch_log(dep, "\n".join(pending))
    finally:
        discard_source(dep)
    Deployment.objects.filter(
        app=app,
        status__in=[Deployment.Status.RUNNING, Deployment.Status.BUILDING, Deployment.Status.QUEUED],
        number__lt=dep.number,
    ).update(status=Deployment.Status.SUPERSEDED)
    dep.image = image
    dep.status = Deployment.Status.RUNNING
    dep.finished_at = timezone.now()
    dep.log = (dep.log + "\nRelease is running").strip()
    dep.save(update_fields=["image", "status", "finished_at", "log"])
    app.current_image = image
    app.status = App.Status.RUNNING
    found = get_driver().python_version(app) if settings.DOCKRA_DRIVER == "docker" else ""
    if not found:
        found = declared_runtime
    fields = ["current_image", "status"]
    if found:
        app.runtime = found
        fields.append("runtime")
    app.save(update_fields=fields)
    get_driver().prune_images(app)
    _audit(job, "deploy", app.slug, f"v{dep.number}")
    elapsed = (dep.finished_at - dep.created_at).total_seconds()
    notify("deploy_succeeded", f"✅ {app.slug} v{dep.number} is running ({elapsed:.0f}s)", app=app.slug)


def _lifecycle(job: Job, status: str, action: str) -> None:
    app = App.objects.get(pk=job.payload["app_id"])
    driver = get_driver()
    if job.kind == Job.Kind.RESTART:
        driver.restart(app)
    elif job.kind == Job.Kind.STOP:
        driver.stop(app)
    elif job.kind == Job.Kind.START:
        if app.instances < 1:
            app.instances = 1
            app.save(update_fields=["instances"])
        ensure_capacity(*app_demand(app), skip_app_id=app.id)
        driver.start(app)
    app.status = status
    app.save(update_fields=["status", "instances"])
    _audit(job, action, app.slug)


def _sync(job: Job) -> None:
    app = App.objects.get(pk=job.payload["app_id"])
    if app.status == App.Status.RUNNING:
        get_driver().sync(app)


def _rollback(job: Job) -> None:
    dep = Deployment.objects.select_related("app").get(pk=job.payload["deployment_id"])
    app = dep.app
    if not dep.image:
        dep.image = f"dockra/{app.slug}:v{dep.number}"
        dep.save(update_fields=["image"])
    if app.instances < 1:
        app.instances = 1
        app.save(update_fields=["instances"])
    ensure_capacity(*app_demand(app), skip_app_id=app.id)
    image = get_driver().build_and_run(app, dep, None)
    Deployment.objects.filter(app=app).exclude(pk=dep.pk).exclude(status=Deployment.Status.FAILED).update(
        status=Deployment.Status.ROLLED_BACK
    )
    dep.status = Deployment.Status.RUNNING
    dep.image = image
    dep.finished_at = timezone.now()
    dep.save(update_fields=["status", "image", "finished_at"])
    app.current_image = image
    app.status = App.Status.RUNNING
    app.save(update_fields=["current_image", "status", "instances"])
    _audit(job, "rollback", app.slug, f"v{dep.number}")


def _provision(job: Job) -> None:
    database = Database.objects.get(pk=job.payload["database_id"])
    ensure_capacity(float(database.cpu or 0), database.memory_mb, database.disk_gb, skip_db_id=database.id)
    info = get_driver().provision_database(database)
    for key, value in info.items():
        setattr(database, key, value)
    database.status = Database.Status.RUNNING
    database.save()
    _audit(job, "database created", database.name, database.engine)


def backup_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / "backups"


def backup_hours(database: Database) -> int:
    return settings.DOCKRA_AUTO_BACKUP_HOURS if database.backup_hours is None else database.backup_hours


def backup_keep(database: Database) -> int:
    return max(1, settings.DOCKRA_BACKUP_KEEP if database.backup_keep is None else database.backup_keep)


def _prune_backups(database: Database) -> None:
    """After a successful backup only the newest ready backups (automatic and manual) are kept and older failed ones are removed."""
    ready = list(database.backups.filter(status=Backup.Status.READY).order_by("-created_at"))
    for old in ready[backup_keep(database):]:
        if old.path:
            Path(old.path).unlink(missing_ok=True)
        old.delete()
    newest = ready[0] if ready else None
    if newest is not None:
        database.backups.filter(status=Backup.Status.FAILED, created_at__lt=newest.created_at).delete()


def _backup(job: Job) -> None:
    backup = Backup.objects.select_related("database").get(pk=job.payload["backup_id"])
    database = backup.database
    suffix = BACKUP_SUFFIX.get(database.engine, "dump")
    dest = backup_dir() / f"{database.name}-{backup.id}.{suffix}"
    size = get_driver().backup_database(database, dest)
    backup.status = Backup.Status.READY
    backup.size_bytes = size
    backup.path = str(dest)
    backup.finished_at = timezone.now()
    backup.save(update_fields=["status", "size_bytes", "path", "finished_at"])
    _prune_backups(database)
    _audit(job, "backup", database.name, f"{size} bytes")
    _offsite(backup, dest, f"databases/{database.name}/{dest.name}")


def _offsite(backup, path: Path, key: str) -> None:
    """Off-site copy; a failure never breaks the local backup but is notified."""
    from engine import offsite

    if not offsite.enabled():
        return
    try:
        remote = offsite.upload(path, key)
    except Exception as exc:
        notify("offsite_failed", f"⚠️ off-site backup failed: {key}\n{str(exc)[-300:]}")
        return
    if backup is not None:
        backup.remote_key = remote
        backup.save(update_fields=["remote_key"])


def panel_dir() -> Path:
    return backup_dir() / "panel"


def backup_panel() -> Path | None:
    """Daily backup of the platform's own database, with the same postgres image version as the db service."""
    import subprocess

    url = os.environ.get("DATABASE_URL", "")
    if settings.DOCKRA_DRIVER != "docker" or not url:
        return None
    folder = panel_dir()
    folder.mkdir(parents=True, exist_ok=True)
    newest = max(folder.glob("panel-*.dump"), key=lambda p: p.stat().st_mtime, default=None)
    if newest and time.time() - newest.stat().st_mtime < 20 * 3600:
        return None
    dest = folder / f"panel-{timezone.now().strftime('%Y%m%dT%H%M%S')}.dump"
    with dest.open("wb") as handle:
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", settings.DOCKRA_NETWORK, settings.DOCKRA_PANEL_PG_IMAGE,
             "pg_dump", "-Fc", url],
            stdout=handle,
            stderr=subprocess.PIPE,
            timeout=1800,
        )
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        notify("panel_backup_failed", f"⚠️ panel backup failed\n{proc.stderr.decode('utf-8', 'replace')[-300:]}")
        return None
    for old in sorted(folder.glob("panel-*.dump"), reverse=True)[settings.DOCKRA_BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
    _offsite(None, dest, f"panel/{dest.name}")
    return dest


def _restore(job: Job) -> None:
    payload = job.payload
    database = Database.objects.get(pk=payload["database_id"])
    if payload.get("backup_id"):
        backup = Backup.objects.get(pk=payload["backup_id"], database=database)
        src = Path(backup.path)
        label = f"backup {backup.id}"
    else:
        src = Path(payload["path"])
        label = "uploaded file"
    step = _job_step(job)
    step(f"Restoring {database.name} from {label}…")
    try:
        if not payload.get("backup_id"):
            from engine import dumps

            dumps.unwrap(src)
        safety = _safety_backup(database, step)
        with _apps_paused(database, step):
            job.result = {**_restore_and_report(database, src, step), "safety_backup_id": safety.id}
        job.save(update_fields=["result"])
    finally:
        if not payload.get("backup_id"):
            src.unlink(missing_ok=True)
    _audit(job, "backup restored", database.name, label)


def _safety_backup(database: Database, step) -> Backup:
    """Before replacing data; if it fails the restore is not performed so there is always a way back."""
    step("Taking a safety backup of the current database…")
    backup = Backup.objects.create(database=database, kind=Backup.Kind.MANUAL)
    dest = backup_dir() / f"{database.name}-{backup.id}.{BACKUP_SUFFIX.get(database.engine, 'dump')}"
    try:
        size = get_driver().backup_database(database, dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        backup.status, backup.error, backup.finished_at = Backup.Status.FAILED, str(exc)[-2000:], timezone.now()
        backup.save(update_fields=["status", "error", "finished_at"])
        raise RuntimeError(f"The safety backup failed; the database was not touched\n{exc}") from exc
    backup.status, backup.size_bytes, backup.path, backup.finished_at = Backup.Status.READY, size, str(dest), timezone.now()
    backup.save(update_fields=["status", "size_bytes", "path", "finished_at"])
    step(f"Safety backup {backup.id} is ready")
    return backup


@contextmanager
def _apps_paused(database: Database, step):
    """Linked apps stay stopped; an app writing during the restore creates duplicate rows and breaks primary key creation."""
    driver = get_driver()
    apps = [link.app for link in database.links.select_related("app") if link.app.status == App.Status.RUNNING]
    for app in apps:
        step(f"Stopping {app.slug} temporarily until the restore finishes…")
        driver.stop(app)
    try:
        yield
    finally:
        for app in apps:
            driver.start(app)
            step(f"{app.slug} started again")


def _job_step(job: Job):
    def step(line: str) -> None:
        job.log = (job.log + "\n" + line).strip()[-20_000:]
        job.save(update_fields=["log"])

    return step


def _restore_and_report(database: Database, path: Path, step) -> dict:
    """Restore, count errors and count tables/rows to compare with the source."""
    driver = get_driver()
    started = time.monotonic()
    output = driver.restore_database(database, path) or ""
    errors = [line.strip() for line in output.splitlines() if re.search(r"\bERROR\b|error:", line)]
    step(f"Data restored in {time.monotonic() - started:.0f} s")
    for line in errors[:5]:
        step(f"warning: {line[:300]}")
    if len(errors) > 5:
        step(f"… and {len(errors) - 5} more errors")
    summary = driver.db_summary(database)
    if errors and summary.get("unit") == "tables" and not summary.get("tables"):
        # psql without ON_ERROR_STOP returns zero even for an incompatible file; an empty database must not be reported as success.
        raise RuntimeError(f"No tables were restored and {len(errors)} errors occurred; the backup file is not compatible with this database\n{errors[0][:300]}")
    if summary:
        if summary["tables"]:
            step(f"Check: {summary['tables']} {summary['unit']}, about {summary['rows']} rows")
        else:
            step(f"Check: {summary['rows']} {summary['unit']}")
    return {"summary": summary, "errors": len(errors)}


def _import_db(job: Job) -> None:
    """Import from a backup file: download if needed, detect engine and version, create, restore and verify."""
    from engine import dumps

    payload = job.payload
    database = Database.objects.get(pk=payload["database_id"])
    driver = get_driver()

    step = _job_step(job)
    path = Path(payload["path"])
    try:
        if payload.get("url"):
            step("Downloading the backup to the server…")
            size = dumps.download(payload["url"], path)
            step(f"Downloaded ({size / 1_048_576:.1f} MB)")
        dumps.unwrap(path)
        info = dumps.detect(path)
        step(f"File format: {info['format']} ({info['engine']})")
        if payload.get("engine") and payload["engine"] != info["engine"]:
            if {payload["engine"], info["engine"]} != {"mysql", "mariadb"}:
                raise RuntimeError(f"The backup file is {info['engine']}, not {payload['engine']}")
        engine = payload.get("engine") or info["engine"]
        version = payload.get("version") or info["version"]
        if dumps.needs_newer(engine, version, info["version"]):
            raise RuntimeError(f"This backup needs version {info['version']} or later; pass a higher --version or leave it empty")
        if database.engine != engine or database.version != version:
            database.engine, database.version = engine, version
            database.save(update_fields=["engine", "version"])

        step(f"Creating {database.get_engine_display()} {version} database…")
        ensure_capacity(float(database.cpu or 0), database.memory_mb, database.disk_gb, skip_db_id=database.id)
        for key, value in driver.provision_database(database).items():
            setattr(database, key, value)
        database.status = Database.Status.RUNNING
        database.save()

        step("Importing data…")
        with _apps_paused(database, step):
            report = _restore_and_report(database, path, step)
        job.result = {"database_id": database.id, "format": info["format"], **report}
        job.save(update_fields=["result"])
        _audit(job, "database imported", database.name, f"{info['format']} -> {engine} {version}")
    finally:
        path.unlink(missing_ok=True)


def _disk_import(job: Job) -> None:
    """Disk files from an archive, link or FTP; with replace, a safety backup is taken first and then the disk is emptied."""
    from apps_core.models import Disk, DiskBackup
    from engine import disk_backup, disk_import, dumps

    payload = dict(job.payload)
    step = _job_step(job)
    disk = Disk.objects.select_related("app").get(pk=payload["disk_id"])
    path = Path(payload["path"]) if payload.get("path") else None
    try:
        root = disk_backup.disk_data(disk)
        if payload.get("replace") and payload.get("ftp"):
            # A broken FTP server or a wrong path must not leave the disk empty.
            disk_import.probe_ftp(payload["ftp"])
        if payload.get("replace") and any(root.iterdir()):
            step("Backing up the current disk before replacing it…")
            safety = DiskBackup.objects.create(app=disk.app, disk=disk, disk_name=disk.name, kind=DiskBackup.Kind.SAFETY)
            try:
                disk_backup.make_backup(safety)
            except Exception as exc:
                safety.status = DiskBackup.Status.FAILED
                safety.error = str(exc)[-2000:]
                safety.save(update_fields=["status", "error"])
                raise RuntimeError(f"The pre-replace backup failed; the disk was not touched\n{exc}") from exc
            disk_import.clear(root)
            step("Disk emptied")
        if payload.get("ftp"):
            report = disk_import.import_ftp(payload["ftp"], root, step, workers=int(payload.get("workers") or 8))
        else:
            if payload.get("url"):
                step("Downloading the archive to the server…")
                size = dumps.download(payload["url"], path)
                step(f"Downloaded ({size / 1_048_576:.1f} MB)")
            own = tuple(n for n in {disk.name, PurePosixPath(disk.mount_to or '').name} if n)
            report = disk_import.import_archive(path, root, step, own)
        from apps_core.extra import _give_to_app

        _give_to_app(root)
        job.result = report
        job.save(update_fields=["result"])
        _audit(job, "disk files imported", disk.app.slug, f"{disk.name}: {report['files']} files")
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
        if payload.get("ftp"):
            # The FTP password is not kept in the jobs table.
            job.payload = {**payload, "ftp": "redacted"}
            job.save(update_fields=["payload"])


def _disk_backup(job: Job) -> None:
    from apps_core.models import DiskBackup
    from engine import disk_backup

    backup = DiskBackup.objects.select_related("app", "disk").get(pk=job.payload["disk_backup_id"])
    disk_backup.make_backup(backup)
    disk_backup.prune(backup.app, backup.disk_name)
    _audit(job, "disk backup", backup.app.slug, f"{backup.disk_name}: {backup.size_bytes} bytes")
    _offsite(backup, Path(backup.path), f"disks/{backup.app.slug}/{backup.disk_name}/{Path(backup.path).name}")


def _disk_restore(job: Job) -> None:
    """The app is stopped while files are swapped so it never sees half old and half new files."""
    from apps_core.models import Disk, DiskBackup
    from engine import disk_backup

    disk = Disk.objects.select_related("app").get(pk=job.payload["disk_id"])
    backup = DiskBackup.objects.get(pk=job.payload["disk_backup_id"], app=disk.app)
    app = disk.app
    driver = get_driver()
    running = app.status == App.Status.RUNNING
    if running:
        driver.stop(app)
    try:
        safety = disk_backup.restore(backup, disk)
    finally:
        if running:
            driver.start(app)
    _audit(job, "disk restored", app.slug, f"{disk.name} from backup {backup.id} (previous state: backup {safety.id})")


def _apply_resources(job: Job) -> None:
    app = App.objects.get(pk=job.payload["app_id"])
    if app.status == App.Status.RUNNING:
        get_driver().apply_resources(app)


def _remove_app(job: Job) -> None:
    """Remove containers, disks, logs and the mirror, then the app itself."""
    import shutil

    app = App.objects.filter(pk=job.payload["app_id"]).first()
    if app is None:
        return
    get_driver().remove_app(app)
    mirror = Path(settings.WORK_ROOT) / "mirror"
    shutil.rmtree(mirror / app.slug, ignore_errors=True)
    (mirror / f"{app.slug}.state.json").unlink(missing_ok=True)
    for backup in app.disk_backups.exclude(path=""):
        Path(backup.path).unlink(missing_ok=True)
    slug = app.slug
    app.delete()
    _audit(job, "app removed", slug)


def _remove_disk(job: Job) -> None:
    from apps_core.models import Disk

    disk = Disk.objects.select_related("app").filter(pk=job.payload["disk_id"]).first()
    if disk is None:
        return
    app = disk.app
    name = disk.name
    disk.delete()
    # Containers are restarted without this disk first, then the volume is removed.
    if app.status == App.Status.RUNNING:
        get_driver().sync(app)
    get_driver().remove_disk(app, type("Disk", (), {"name": name})())
    _audit(job, "disk removed", app.slug, name)


def _remove_db(job: Job) -> None:
    database = Database.objects.filter(pk=job.payload["database_id"]).first()
    if database is None:
        return
    get_driver().stop_db_admin(database)
    get_driver().remove_database(database)
    for backup in database.backups.exclude(path=""):
        Path(backup.path).unlink(missing_ok=True)
    name = database.name
    database.delete()
    _audit(job, "database removed", name)


def _db_power(job: Job) -> None:
    database = Database.objects.get(pk=job.payload["database_id"])
    driver = get_driver()
    if job.kind == Job.Kind.DB_STOP:
        driver.stop_db_admin(database)
        driver.stop_database(database)
        database.status = Database.Status.STOPPED
        database.admin_until = None
        action = "database stopped"
    else:
        ensure_capacity(float(database.cpu or 0), database.memory_mb, database.disk_gb, skip_db_id=database.id)
        if job.kind == Job.Kind.DB_RESIZE:
            info = driver.provision_database(database, password=database.password)
            for key, value in info.items():
                setattr(database, key, value)
            action = "database resized"
        else:
            driver.start_database(database)
            action = "database started"
        database.status = Database.Status.RUNNING
    database.save()
    _audit(job, action, database.name)


def _db_admin(job: Job) -> None:
    from engine.dbadmin import tool_name

    database = Database.objects.get(pk=job.payload["database_id"])
    if database.status != Database.Status.RUNNING:
        raise RuntimeError("The database is not running")
    get_driver().start_db_admin(database, job.payload["base"])
    _audit(job, "database admin opened", database.name, tool_name(database))


def _db_admin_stop(job: Job) -> None:
    database = Database.objects.filter(pk=job.payload["database_id"]).first()
    if database is not None:
        get_driver().stop_db_admin(database)


def close_idle_db_admins() -> int:
    """Admin UIs that have received no requests for a while are closed so they do not hold host memory."""
    from engine.actions import enqueue

    count = 0
    for database in Database.objects.filter(admin_until__lt=timezone.now()):
        Database.objects.filter(pk=database.pk).update(admin_until=None)
        enqueue(Job.Kind.DB_ADMIN_STOP, {"database_id": database.id}, None, database.organization)
        count += 1
    return count


def _exec(job: Job) -> None:
    app = App.objects.get(pk=job.payload["app_id"])
    output, code = get_driver().exec(app, job.payload["command"])
    job.result = {"output": output[-200_000:], "exit_code": code}
    job.save(update_fields=["result"])


def _logs(job: Job) -> None:
    tail = int(job.payload.get("tail") or 400)
    if job.payload.get("database_id"):
        database = Database.objects.get(pk=job.payload["database_id"])
        job.result = {"text": get_driver().database_logs(database, tail=tail)}
    else:
        app = App.objects.get(pk=job.payload["app_id"])
        job.result = {"text": get_driver().container_logs(app, tail=tail)}
    job.save(update_fields=["result"])


def _volume(job: Job) -> None:
    from engine.volumes import ensure_volume

    job.result = {"path": ensure_volume(job.payload["name"], int(job.payload["size_gb"]))}
    job.save(update_fields=["result"])


HANDLERS = {
    Job.Kind.DEPLOY: _deploy,
    Job.Kind.ROLLBACK: _rollback,
    Job.Kind.PROVISION_DB: _provision,
    Job.Kind.BACKUP: _backup,
    Job.Kind.RESTORE: _restore,
    Job.Kind.SYNC: _sync,
    Job.Kind.APPLY_RESOURCES: _apply_resources,
    Job.Kind.REMOVE_APP: _remove_app,
    Job.Kind.REMOVE_DISK: _remove_disk,
    Job.Kind.REMOVE_DB: _remove_db,
    Job.Kind.DB_STOP: _db_power,
    Job.Kind.DB_START: _db_power,
    Job.Kind.DB_RESIZE: _db_power,
    Job.Kind.EXEC: _exec,
    Job.Kind.LOGS: _logs,
    Job.Kind.VOLUME: _volume,
    Job.Kind.DISK_BACKUP: _disk_backup,
    Job.Kind.DISK_RESTORE: _disk_restore,
    Job.Kind.DB_ADMIN: _db_admin,
    Job.Kind.IMPORT_DB: _import_db,
    Job.Kind.DISK_IMPORT: _disk_import,
    Job.Kind.DB_ADMIN_STOP: _db_admin_stop,
    Job.Kind.RESTART: lambda job: _lifecycle(job, App.Status.RUNNING, "restart"),
    Job.Kind.STOP: lambda job: _lifecycle(job, App.Status.STOPPED, "stop"),
    Job.Kind.START: lambda job: _lifecycle(job, App.Status.RUNNING, "start"),
}


class _Heartbeat:
    """Refresh the job's timestamp while it runs so a dead worker can be detected."""

    def __init__(self, job_id: int):
        self.job_id = job_id
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if connection.vendor == "sqlite":
            return self
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def _loop(self):
        try:
            while not self.stop.wait(15):
                Job.objects.filter(pk=self.job_id).update(heartbeat_at=timezone.now())
        finally:
            connection.close()

    def __exit__(self, *exc):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def process_job(job_id: int) -> None:
    now = timezone.now()
    updated = Job.objects.filter(pk=job_id, status=Job.Status.QUEUED).update(
        status=Job.Status.RUNNING, started_at=now, heartbeat_at=now
    )
    if updated:
        _execute(job_id)


def _execute(job_id: int) -> None:
    job = Job.objects.get(pk=job_id)
    handler = HANDLERS.get(job.kind)
    try:
        if handler is None:
            raise RuntimeError("Unknown job kind")
        with _Heartbeat(job.pk):
            handler(job)
    except BuildCanceled:
        job.status = Job.Status.FAILED
        job.log = (job.log + "\nCanceled").strip()
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "log", "finished_at"])
        _cancel_related(job)
        return
    except Exception as exc:
        job.status = Job.Status.FAILED
        job.log = (job.log + "\n" + str(exc)).strip()
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "log", "finished_at"])
        _fail_related(job, str(exc))
        return
    job.status = Job.Status.DONE
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])


def _claim(queues=None) -> int | None:
    """Oldest queued job whose target (app or database) has no other running job."""
    rows = Job.objects.filter(status=Job.Status.QUEUED)
    if queues:
        rows = rows.filter(queue__in=list(queues))
    busy = set(Job.objects.filter(status=Job.Status.RUNNING).exclude(target="").values_list("target", flat=True))
    for job_id, target in rows.order_by("id").values_list("id", "target")[:200]:
        if target and target in busy:
            continue
        now = timezone.now()
        claimed = Job.objects.filter(pk=job_id, status=Job.Status.QUEUED).update(
            status=Job.Status.RUNNING, started_at=now, heartbeat_at=now
        )
        if claimed:
            return job_id
        busy.add(target)
    return None


def process_next_job(queues=None) -> bool:
    if connection.vendor == "postgresql":
        from django.db import transaction

        # Several builders at once: selecting and claiming a job happens under a short lock.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(7302)")
            job_id = _claim(queues)
    else:
        job_id = _claim(queues)
    if job_id is None:
        return False
    _execute(job_id)
    return True


def recover_stale_jobs() -> int:
    """Jobs whose worker died are marked failed so the app does not get stuck."""
    cutoff = timezone.now() - timedelta(seconds=settings.DOCKRA_JOB_STALE_SECONDS)
    stale = Job.objects.filter(status=Job.Status.RUNNING, heartbeat_at__lt=cutoff)
    count = 0
    for job in stale:
        message = "The worker stopped in the middle of this job; please try again"
        claimed = Job.objects.filter(pk=job.pk, status=Job.Status.RUNNING).update(
            status=Job.Status.FAILED, finished_at=timezone.now(), log=(job.log + "\n" + message).strip()
        )
        if claimed:
            _fail_related(job, message)
            count += 1
    return count


def schedule_auto_backups() -> int:
    from engine.actions import backup_database

    now = timezone.now()
    retry = now - timedelta(minutes=settings.DOCKRA_BACKUP_RETRY_MINUTES)
    count = 0
    for database in Database.objects.filter(status=Database.Status.RUNNING):
        hours = backup_hours(database)
        if hours <= 0:
            continue
        backups = database.backups
        # A backup in progress, or a successful (automatic or manual) backup newer than one interval, means it is not due yet.
        if backups.filter(status=Backup.Status.RUNNING, created_at__gte=now - timedelta(hours=6)).exists():
            continue
        if backups.filter(status=Backup.Status.READY, created_at__gte=now - timedelta(hours=hours)).exists():
            continue
        # A failure does not block the next cycle; it is only retried after a short delay.
        if backups.filter(kind=Backup.Kind.AUTO, status=Backup.Status.FAILED, created_at__gte=retry).exists():
            continue
        backup = Backup.objects.create(database=database, status=Backup.Status.RUNNING, kind=Backup.Kind.AUTO)
        backup_database(backup, None)
        count += 1
    return count


def _schedule_disk_backups() -> int:
    from engine import disk_backup

    return disk_backup.schedule_auto()


def cleanup() -> None:
    from accounts.models import CliLogin, CliWatch
    from engine import blobs
    from engine.logs import prune

    from engine import disk_backup

    blobs.gc()
    prune()
    disk_backup.cleanup_orphans()
    old = timezone.now() - timedelta(hours=1)
    CliLogin.objects.filter(created_at__lt=old).delete()
    CliWatch.objects.filter(created_at__lt=old).delete()
    Job.objects.filter(status__in=[Job.Status.DONE, Job.Status.FAILED], finished_at__lt=timezone.now() - timedelta(days=30)).delete()
    sources = Path(settings.MEDIA_ROOT) / "sources"
    if sources.is_dir():
        cutoff = time.time() - 86_400
        for item in sources.iterdir():
            if item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)


def _sample_metrics() -> None:
    from engine.metrics import sample_all

    sample_all()


def _check_alerts() -> None:
    from engine.alerts import check_all

    check_all()


def _reconnect_routes() -> None:
    driver = get_driver()
    if hasattr(driver, "reconnect_routes"):
        driver.reconnect_routes()


class Maintenance:
    """Periodic tasks of the ops worker: stats, automatic backups, stuck jobs, cleanup."""

    TASKS = (
        ("recover", 30, recover_stale_jobs),
        ("routes", 30, _reconnect_routes),
        ("metrics", 60, _sample_metrics),
        ("alerts", 60, _check_alerts),
        ("dbadmin", 60, close_idle_db_admins),
        ("backups", 600, schedule_auto_backups),
        ("disk_backups", 600, _schedule_disk_backups),
        ("cleanup", 3600, cleanup),
        ("panel", 3600, backup_panel),
    )

    def __init__(self):
        self.last: dict[str, float] = {}

    def tick(self, log=None) -> None:
        now = time.monotonic()
        for name, every, func in self.TASKS:
            if now - self.last.get(name, 0) < every:
                continue
            self.last[name] = now
            try:
                func()
            except Exception as exc:
                if log:
                    log(f"{name}: {exc}")


class CronRunner:
    """Cron jobs run in a separate thread so a long cron never blocks the queue."""

    def __init__(self, workers: int = 4):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cron")
        self.running: set[int] = set()
        self.lock = threading.Lock()
        self.last_stamp = ""

    def tick(self) -> None:
        now = timezone.localtime()
        stamp = now.strftime("%Y%m%d%H%M")
        if stamp == self.last_stamp:
            return
        self.last_stamp = stamp
        for cron_id in due_crons(now):
            with self.lock:
                if cron_id in self.running:
                    continue
                self.running.add(cron_id)
            self.pool.submit(self._run, cron_id)

    def _run(self, cron_id: int) -> None:
        try:
            run_cron(cron_id)
        finally:
            with self.lock:
                self.running.discard(cron_id)
            close_old_connections()
            connection.close()


def due_crons(now) -> list[int]:
    from apps_core.models import CronJob
    from engine.cron import cron_matches

    stamp = now.strftime("%Y%m%d%H%M")
    due = []
    for cron in CronJob.objects.filter(enabled=True).select_related("app"):
        if cron.app.status != App.Status.RUNNING:
            continue
        try:
            if not cron_matches(cron.schedule, now):
                continue
        except ValueError:
            continue
        if cron.last_run_at and timezone.localtime(cron.last_run_at).strftime("%Y%m%d%H%M") == stamp:
            continue
        due.append(cron.id)
    return due


def run_cron(cron_id: int) -> None:
    from apps_core.models import CronJob

    cron = CronJob.objects.select_related("app").get(pk=cron_id)
    cron.last_run_at = timezone.now()
    cron.save(update_fields=["last_run_at"])
    try:
        output = get_driver().exec_cron(cron.app, cron.command)
        cron.last_status = "ok"
        cron.last_output = (output or "")[-2000:]
    except Exception as exc:
        cron.last_status = "failed"
        cron.last_output = str(exc)[-2000:]
    cron.save(update_fields=["last_status", "last_output"])


def process_due_crons() -> None:
    for cron_id in due_crons(timezone.localtime()):
        run_cron(cron_id)


def run_rpc(kind: str, payload: dict, user, organization, timeout: float) -> dict:
    """Hand a short job to the terminal service and wait for the answer (the API itself has no Docker access)."""
    from engine.actions import enqueue

    job = enqueue(kind, payload, user, organization)
    deadline = time.monotonic() + timeout
    while job.status in (Job.Status.QUEUED, Job.Status.RUNNING):
        if time.monotonic() > deadline:
            Job.objects.filter(pk=job.pk, status=Job.Status.QUEUED).delete()
            raise RuntimeError("The terminal service did not respond; check its status")
        time.sleep(0.2)
        job.refresh_from_db()
    try:
        if job.status == Job.Status.FAILED:
            lines = (job.log or "").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "The operation failed")
        return job.result or {}
    finally:
        job.delete()


SERVICE_STALE_SECONDS = 90


def beat(name: str, kind: str, queues: str = "") -> None:
    from engine.models import ServiceBeat

    ServiceBeat.objects.update_or_create(name=name, defaults={"kind": kind, "queues": queues, "last_seen": timezone.now()})


def forget(name: str) -> None:
    from engine.models import ServiceBeat

    ServiceBeat.objects.filter(name=name).delete()


def services_status() -> list[dict]:
    from engine.models import ServiceBeat

    cutoff = timezone.now() - timedelta(seconds=SERVICE_STALE_SECONDS)
    return [
        {
            "name": row.name,
            "kind": row.kind,
            "queues": row.queues,
            "last_seen": row.last_seen.isoformat(),
            "ok": row.last_seen >= cutoff,
        }
        for row in ServiceBeat.objects.all()
    ]


def _forget_replaced() -> None:
    """Every platform update recreates service containers with a new hostname; a silent row whose container is gone raises no alert."""
    import subprocess

    from engine.models import ServiceBeat

    if settings.DOCKRA_DRIVER != "docker":
        return
    cutoff = timezone.now() - timedelta(seconds=SERVICE_STALE_SECONDS)
    silent = list(ServiceBeat.objects.filter(last_seen__lt=cutoff).values_list("name", flat=True))
    if not silent:
        return
    try:
        proc = subprocess.run(["docker", "ps", "-aq"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0:
        return
    # Each service container's hostname is the first 12 characters of its id (docker ps -q).
    existing = set(proc.stdout.decode().split())
    gone = [name for name in silent if name.partition("@")[2] not in existing]
    ServiceBeat.objects.filter(name__in=gone).delete()


def watch_services() -> None:
    """Every service watches the others; a silent one raises an alert and very old rows (e.g. a removed builder) are cleaned up."""
    from engine.alerts import _set
    from engine.models import Alert, ServiceBeat

    ServiceBeat.objects.filter(last_seen__lt=timezone.now() - timedelta(days=1)).delete()
    _forget_replaced()
    live = set()
    for row in services_status():
        live.add(row["name"])
        _set(Alert.Kind.SERVICE, not row["ok"], f"{row['name']} ({row['kind']}) has not reported for more than {SERVICE_STALE_SECONDS} seconds", subject=row["name"][:120])
    Alert.objects.filter(kind=Alert.Kind.SERVICE, status=Alert.Status.FIRING).exclude(subject__in=live).update(
        status=Alert.Status.RESOLVED, resolved_at=timezone.now()
    )
