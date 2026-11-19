from django.conf import settings
from django.db.models import Max

from apps_core.models import App, Deployment
from databases.models import Backup, Database
from engine.models import BUILD_KINDS, RPC_KINDS, Job
from engine.resources import app_demand, ensure_capacity
from engine.runner import process_job


def _target(kind: str, payload: dict) -> str:
    if payload.get("deployment_id"):
        slug = Deployment.objects.filter(pk=payload["deployment_id"]).values_list("app__slug", flat=True).first()
        return f"app:{slug}" if slug else ""
    if payload.get("app_id"):
        return f"app:{App.objects.filter(pk=payload['app_id']).values_list('slug', flat=True).first()}"
    if payload.get("disk_id"):
        from apps_core.models import Disk

        slug = Disk.objects.filter(pk=payload["disk_id"]).values_list("app__slug", flat=True).first()
        return f"app:{slug}" if slug else ""
    if payload.get("database_id"):
        return f"db:{payload['database_id']}"
    if payload.get("disk_backup_id"):
        # A backup only reads and never delays a deployment; a restore carries disk_id and takes the app lock.
        return ""
    if payload.get("backup_id"):
        db_id = Backup.objects.filter(pk=payload["backup_id"]).values_list("database_id", flat=True).first()
        return f"db:{db_id}" if db_id else ""
    return ""


def enqueue(kind: str, payload: dict, actor, organization) -> Job:
    if kind in RPC_KINDS:
        queue = Job.Queue.RPC
    else:
        queue = Job.Queue.BUILD if kind in BUILD_KINDS else Job.Queue.OPS
    job = Job.objects.create(
        # Immediate jobs have no target so they never wait behind a build or a long job of the same app.
        target="" if kind in RPC_KINDS else _target(kind, payload),
        kind=kind,
        queue=queue,
        payload=payload,
        actor=actor,
        organization=organization,
        status=Job.Status.QUEUED,
    )
    if settings.DOCKRA_EAGER:
        process_job(job.pk)
        job.refresh_from_db()
    return job


def deploy_app(app: App, user, message: str, git_url: str = "", source_path: str = "", tree: str = ""):
    ensure_capacity(*app_demand(app), skip_app_id=app.id)
    number = (app.deployments.aggregate(m=Max("number"))["m"] or 0) + 1
    dep = Deployment.objects.create(
        app=app,
        number=number,
        status=Deployment.Status.QUEUED,
        message=message or "Deploy",
        git_url="" if tree or source_path else (git_url or app.git_url),
        source_path=source_path,
        tree=tree,
        created_by=user,
    )
    app.status = App.Status.DEPLOYING
    app.save(update_fields=["status"])
    job = enqueue(Job.Kind.DEPLOY, {"deployment_id": dep.id}, user, app.organization)
    dep.refresh_from_db()
    app.refresh_from_db()
    return dep, job


def change_power(app: App, user, kind: str):
    if kind != Job.Kind.STOP:
        ensure_capacity(*app_demand(app, instances=max(app.instances, 1)), skip_app_id=app.id)
    app.status = App.Status.DEPLOYING if kind != Job.Kind.STOP else App.Status.STOPPED
    app.save(update_fields=["status"])
    job = enqueue(kind, {"app_id": app.id}, user, app.organization)
    app.refresh_from_db()
    return job


def sync_app(app: App, user):
    """env, domains or disks changed; containers are recreated in the background without downtime."""
    if app.status != App.Status.RUNNING:
        return None
    return enqueue(Job.Kind.SYNC, {"app_id": app.id}, user, app.organization)


def rollback_app(app: App, user, deployment: Deployment):
    ensure_capacity(*app_demand(app), skip_app_id=app.id)
    app.status = App.Status.DEPLOYING
    app.save(update_fields=["status"])
    job = enqueue(Job.Kind.ROLLBACK, {"deployment_id": deployment.id}, user, app.organization)
    deployment.refresh_from_db()
    app.refresh_from_db()
    return job


def provision_database(database: Database, user):
    ensure_capacity(float(database.cpu or 0), database.memory_mb, database.disk_gb, skip_db_id=database.id)
    database.status = Database.Status.PROVISIONING
    database.save(update_fields=["status"])
    job = enqueue(Job.Kind.PROVISION_DB, {"database_id": database.id}, user, database.organization)
    database.refresh_from_db()
    return job


def backup_database(backup: Backup, user):
    job = enqueue(Job.Kind.BACKUP, {"backup_id": backup.id}, user, backup.database.organization)
    backup.refresh_from_db()
    return job


def backup_disk(backup, user):
    job = enqueue(Job.Kind.DISK_BACKUP, {"disk_backup_id": backup.id}, user, backup.app.organization)
    backup.refresh_from_db()
    return job


def restore_disk(disk, backup, user):
    return enqueue(Job.Kind.DISK_RESTORE, {"disk_id": disk.id, "disk_backup_id": backup.id}, user, disk.app.organization)


def restore_database(database: Database, user, backup: Backup | None = None, path: str = ""):
    payload = {"database_id": database.id}
    if backup is not None:
        payload["backup_id"] = backup.id
    else:
        payload["path"] = path
    return enqueue(Job.Kind.RESTORE, payload, user, database.organization)
