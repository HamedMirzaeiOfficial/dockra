import re
import uuid
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Membership
from apps_core.models import App, EnvVar
from databases.models import Backup, Database, DatabaseLink
from engine import uploads
from engine.actions import backup_database, enqueue, provision_database, restore_database, sync_app
from engine.drivers import DB_SPECS
from engine.models import Job
from engine.resources import QuotaExceeded, ensure_capacity, parse_cpu, parse_range

ENGINES = {
    Database.Engine.POSTGRES: "16",
    Database.Engine.MYSQL: "8.0",
    Database.Engine.MARIADB: "11.4",
    Database.Engine.MONGODB: "7",
    Database.Engine.REDIS: "7.4",
}


def name_error(name: str) -> str:
    if not name or not name.replace("-", "").isalnum() or not name[0].isalpha() or len(name) > 40:
        return "Invalid database name"
    if Database.objects.filter(name=name).exists():
        return "This name is already taken"
    return ""


def _reveal(request) -> bool:
    return request.membership.role != Membership.Role.VIEWER


def _url(database: Database) -> str:
    password = quote(database.password or "")
    user = quote(database.username or "")
    if database.engine == Database.Engine.REDIS:
        return f"redis://:{password}@{database.host}:{database.port}/0"
    if database.engine in (Database.Engine.MYSQL, Database.Engine.MARIADB):
        return f"mysql://{user}:{password}@{database.host}:{database.port}/{database.db_name}"
    if database.engine == Database.Engine.MONGODB:
        return f"mongodb://{user}:{password}@{database.host}:{database.port}/{database.db_name}?authSource=admin"
    return f"postgres://{user}:{password}@{database.host}:{database.port}/{database.db_name}"


def _backup(item: Backup) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "kind_label": item.get_kind_display(),
        "status": item.status,
        "status_label": item.get_status_display(),
        "size_bytes": item.size_bytes,
        "offsite": bool(item.remote_key),
        "created_at": item.created_at.isoformat(),
        "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        "error": item.error.strip().splitlines()[-1][:300] if item.error.strip() else "",
    }


def database_payload(database: Database, reveal: bool, detail: bool = False) -> dict:
    last = database.backups.filter(status=Backup.Status.READY).first()
    data = {
        "id": database.id,
        "name": database.name,
        "engine": database.engine,
        "engine_label": database.get_engine_display(),
        "version": database.version,
        "status": database.status,
        "status_label": database.get_status_display(),
        "region": "local",
        "region_label": "This server",
        "memory_mb": database.memory_mb,
        "cpu": float(database.cpu or 0),
        "disk_gb": database.disk_gb,
        "host": database.host,
        "port": database.port,
        "db_name": database.db_name,
        "username": database.username if reveal else "",
        "backup_count": database.backups.filter(status=Backup.Status.READY).count(),
        "backup_hours": database.backup_hours,
        "backup_keep": database.backup_keep,
        "backup_default_hours": settings.DOCKRA_AUTO_BACKUP_HOURS,
        "backup_default_keep": settings.DOCKRA_BACKUP_KEEP,
        "last_backup": _backup(last) if last else None,
        "links": [{"app": link.app.slug, "app_name": link.app.name, "env_key": link.env_key} for link in database.links.select_related("app")],
        "created_at": database.created_at.isoformat(),
    }
    if detail and reveal:
        data["password"] = database.password
        data["url"] = _url(database) if database.host else ""
    if detail:
        data["backups"] = [_backup(item) for item in database.backups.all()[:20]]
    return data


class DatabaseListCreate(APIView):
    def get(self, request):
        from engine.metrics import database_metrics_for

        rows = request.membership.organization.databases.all()
        return Response(
            [{**database_payload(item, _reveal(request)), "metrics": database_metrics_for(item)} for item in rows]
        )

    def post(self, request):
        name = (request.data.get("name") or "").strip().lower()
        engine = request.data.get("engine") or Database.Engine.POSTGRES
        if engine not in ENGINES:
            return Response({"detail": "Engine must be postgres, mysql, mariadb, mongodb or redis"}, status=400)
        error = name_error(name)
        if error:
            return Response({"detail": error}, status=400)
        version = str(request.data.get("version") or ENGINES[engine]).strip()
        # Official image tag; numeric so the version is known (data path and backup compatibility depend on it).
        if not re.fullmatch(r"\d+(\.\d+){0,2}(-[a-z0-9.]+)?", version):
            return Response({"detail": "Version must be a numeric tag of the official image, e.g. 16, 7.2 or 10.11"}, status=400)
        try:
            if engine == Database.Engine.REDIS:
                cpu = parse_cpu(request.data.get("cpu") or "0.25", low="0.10", high="4")
                memory_mb = parse_range(request.data.get("memory_mb") or 256, 64, 4096, "Memory")
                disk_gb = parse_range(request.data.get("disk_gb") or 1, 1, 50, "Disk")
            else:
                cpu = parse_cpu(request.data.get("cpu") or "0.50", low="0.10", high="8")
                memory_mb = parse_range(request.data.get("memory_mb") or 1024, 256, 16384, "Memory")
                disk_gb = parse_range(request.data.get("disk_gb") or 10, 1, 500, "Disk")
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        database = Database.objects.create(
            organization=request.membership.organization,
            name=name,
            engine=engine,
            version=version,
            cpu=cpu,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
            region="local",
        )
        try:
            provision_database(database, request.user)
        except QuotaExceeded as exc:
            database.delete()
            return Response({"detail": str(exc)}, status=400)
        database.refresh_from_db()
        return Response(database_payload(database, True, detail=True), status=201)


def _limits(engine: str) -> dict:
    if engine == Database.Engine.REDIS:
        return {"cpu": ("0.10", "4"), "memory_mb": (64, 4096), "disk_gb": (1, 50)}
    return {"cpu": ("0.10", "8"), "memory_mb": (256, 16384), "disk_gb": (1, 500)}


def _job_reply(job, database, request):
    if job.status == Job.Status.FAILED:
        lines = (job.log or "").strip().splitlines()
        return Response({"detail": lines[-1] if lines else "The operation failed"}, status=400)
    database.refresh_from_db()
    code = 202 if job.status == Job.Status.QUEUED else 200
    return Response({"database": database_payload(database, _reveal(request), detail=True), "job": {"id": job.id, "status": job.status}}, status=code)


class DatabasePower(APIView):
    kind = Job.Kind.DB_STOP

    def post(self, request, pk):
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        if self.kind == Job.Kind.DB_STOP and database.status != Database.Status.RUNNING:
            return Response({"detail": "The database is not running"}, status=400)
        if self.kind == Job.Kind.DB_START:
            if database.status == Database.Status.RUNNING:
                return Response({"detail": "The database is already running"}, status=400)
            try:
                ensure_capacity(float(database.cpu or 0), database.memory_mb, database.disk_gb, skip_db_id=database.id)
            except QuotaExceeded as exc:
                return Response({"detail": str(exc)}, status=400)
        job = enqueue(self.kind, {"database_id": database.id}, request.user, database.organization)
        return _job_reply(job, database, request)


class DatabaseStop(DatabasePower):
    kind = Job.Kind.DB_STOP


class DatabaseStart(DatabasePower):
    kind = Job.Kind.DB_START


class DatabaseDetail(APIView):
    def get(self, request, pk):
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        return Response(database_payload(database, _reveal(request), detail=True))

    def patch(self, request, pk):
        """Change CPU, memory and disk (grow only) and the backup schedule. The container is recreated with the same data and password."""
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        limits = _limits(database.engine)
        data = request.data
        if "backup_hours" in data or "backup_keep" in data:
            try:
                if "backup_hours" in data:
                    raw = data["backup_hours"]
                    database.backup_hours = None if raw in (None, "") else parse_range(raw, 0, 24 * 30, "Backup interval")
                if "backup_keep" in data:
                    raw = data["backup_keep"]
                    database.backup_keep = None if raw in (None, "") else parse_range(raw, 1, 30, "Backups to keep")
            except QuotaExceeded as exc:
                return Response({"detail": str(exc)}, status=400)
            database.save(update_fields=["backup_hours", "backup_keep"])
            if not any(key in data for key in ("cpu", "memory_mb", "disk_gb")):
                return Response({"database": database_payload(database, _reveal(request), detail=True)})
        try:
            cpu = parse_cpu(data["cpu"], *limits["cpu"]) if data.get("cpu") not in (None, "") else database.cpu
            memory = parse_range(data["memory_mb"], *limits["memory_mb"], "Memory") if data.get("memory_mb") not in (None, "") else database.memory_mb
            disk = parse_range(data["disk_gb"], *limits["disk_gb"], "Disk") if data.get("disk_gb") not in (None, "") else database.disk_gb
            if disk < database.disk_gb:
                raise QuotaExceeded("Disks can only grow, not shrink")
            if database.status == Database.Status.RUNNING:
                ensure_capacity(float(cpu), memory, disk, skip_db_id=database.id)
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        changed = (cpu, memory, disk) != (database.cpu, database.memory_mb, database.disk_gb)
        database.cpu, database.memory_mb, database.disk_gb = cpu, memory, disk
        database.save(update_fields=["cpu", "memory_mb", "disk_gb"])
        if changed and database.status == Database.Status.RUNNING:
            job = enqueue(Job.Kind.DB_RESIZE, {"database_id": database.id}, request.user, database.organization)
            return _job_reply(job, database, request)
        return Response({"database": database_payload(database, _reveal(request), detail=True)})

    def delete(self, request, pk):
        if request.membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
            return Response({"detail": "Only an admin can delete a database"}, status=403)
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        job = enqueue(Job.Kind.REMOVE_DB, {"database_id": database.id}, request.user, database.organization)
        if job.status == Job.Status.FAILED:
            return Response({"detail": (job.log or "Deletion failed").strip().splitlines()[-1]}, status=502)
        if job.status == Job.Status.DONE:
            return Response(status=204)
        return Response({"job": {"id": job.id, "status": job.status}}, status=202)


class BackupCreate(APIView):
    def post(self, request, pk):
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        if database.status != Database.Status.RUNNING:
            return Response({"detail": "The database is not running"}, status=400)
        backup = Backup.objects.create(database=database, status=Backup.Status.RUNNING)
        job = backup_database(backup, request.user)
        backup.refresh_from_db()
        return Response({"backup": _backup(backup), "job": {"id": job.id, "status": job.status}}, status=200)


class DatabaseLinkView(APIView):
    def post(self, request, pk):
        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        app = get_object_or_404(App, organization=request.membership.organization, slug=request.data.get("app"))
        default_key = DB_SPECS[database.engine]["env"]
        env_key = (request.data.get("env_key") or default_key).strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", env_key):
            return Response({"detail": f"Invalid variable name: {env_key}"}, status=400)
        taken = DatabaseLink.objects.filter(app=app, env_key=env_key).exclude(database=database).select_related("database").first()
        if taken is not None:
            # Without this the previous database URL was silently overwritten and the app connected to a different database.
            return Response(
                {"detail": f"{env_key} in this app is already linked to database {taken.database.name}; choose another variable name, e.g. {env_key}_2"},
                status=400,
            )
        DatabaseLink.objects.update_or_create(database=database, app=app, defaults={"env_key": env_key})
        EnvVar.objects.update_or_create(
            app=app,
            key=env_key,
            defaults={"value": _url(database), "is_secret": True},
        )
        job = sync_app(app, request.user)
        if job is not None and job.status == Job.Status.FAILED:
            return Response({"detail": f"URL saved, but could not be applied to the container: {job.log.strip().splitlines()[-1]}"}, status=502)
        return Response(database_payload(database, _reveal(request), detail=True))


def _database(request, pk) -> Database:
    return get_object_or_404(Database, organization=request.membership.organization, pk=pk)


class BackupDetail(APIView):
    def delete(self, request, pk, bid):
        backup = get_object_or_404(Backup, database=_database(request, pk), pk=bid)
        if backup.path:
            Path(backup.path).unlink(missing_ok=True)
        backup.delete()
        return Response(status=204)


class BackupDownload(APIView):
    def get(self, request, pk, bid):
        if not _reveal(request):
            return Response({"detail": "Viewers cannot download backups"}, status=403)
        backup = get_object_or_404(Backup, database=_database(request, pk), pk=bid, status=Backup.Status.READY)
        path = Path(backup.path)
        if not path.is_file():
            return Response({"detail": "The backup file is not on the server"}, status=404)
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


class RestoreView(APIView):
    """Restore from one of this panel's backups or from an uploaded file (e.g. another provider's export)."""

    write_role = "admin"

    def post(self, request, pk):
        database = _database(request, pk)
        if database.status != Database.Status.RUNNING:
            return Response({"detail": "The database is not running"}, status=400)
        upload = request.FILES.get("file")
        upload_id = request.data.get("upload") or request.query_params.get("upload")
        if upload_id:
            # A file that arrived in chunks via /api/uploads.
            uploaded = uploads.find(request, upload_id)
            if uploaded is None:
                return Response({"detail": uploads.MISSING}, status=400)
            dest = uploads.folder() / f"{database.name}-{uuid.uuid4().hex}"
            uploaded.replace(dest)
            job = restore_database(database, request.user, path=str(dest))
        elif upload is not None:
            folder = Path(settings.MEDIA_ROOT) / "restore"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{database.name}-{uuid.uuid4().hex}"
            with dest.open("wb") as handle:
                for chunk in upload.chunks():
                    handle.write(chunk)
            job = restore_database(database, request.user, path=str(dest))
        else:
            backup = get_object_or_404(
                Backup, database=database, pk=request.data.get("backup_id"), status=Backup.Status.READY
            )
            job = restore_database(database, request.user, backup=backup)
        if job.status == Job.Status.FAILED:
            lines = (job.log or "").strip().splitlines()
            return Response({"detail": lines[-1] if lines else "Restore failed"}, status=400)
        return Response({"job": {"id": job.id, "status": job.status}}, status=202 if job.status == Job.Status.QUEUED else 200)


class DatabaseMetrics(APIView):
    def get(self, request, pk):
        from engine.metrics import RANGES, database_metrics_for, series_for

        database = _database(request, pk)
        range_key = request.query_params.get("range") or "1h"
        if range_key not in RANGES:
            return Response({"detail": "Range must be 1h, 6h, 24h or 7d"}, status=400)
        return Response({**database_metrics_for(database), "series": series_for(database, range_key)})


class DatabaseLogs(APIView):
    """Database container logs from the terminal service (the API itself has no Docker access)."""

    def get(self, request, pk):
        from django.utils import timezone

        from engine.runner import run_rpc

        database = _database(request, pk)
        try:
            tail = max(50, min(int(request.query_params.get("tail") or 400), 5000))
        except ValueError:
            tail = 400
        try:
            text = run_rpc(Job.Kind.LOGS, {"database_id": database.id, "tail": tail}, request.user, database.organization, 30)
            text = text.get("text", "")
        except RuntimeError as exc:
            text = f"(container log unavailable: {exc})"
        return Response({"text": text, "updated_at": timezone.now().isoformat()})
