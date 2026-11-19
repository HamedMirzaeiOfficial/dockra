"""Import a database from a backup file (e.g. exported from another PaaS) into a new database in one request; `dockra db import`.

The request body is the file itself (not multipart) so it streams straight to disk; options go in the query string.
With `url` instead of a file, the server downloads the backup itself (e.g. a provider's backup download link).
The panel and CLI upload the file in chunks via `/api/uploads` and then start here with `upload=<id>`.
"""

import math
import uuid

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from databases.models import Database
from databases.present import ENGINES, database_payload, name_error
from engine import dumps, uploads
from engine.actions import enqueue
from engine.models import Job
from engine.resources import QuotaExceeded, ensure_capacity, parse_cpu, parse_range
from config.i18n import tr

CHUNK = 1 << 20


def _disk_for(size: int, compressed: bool) -> int:
    """Default disk size: restored data and indexes are several times larger than the compressed file."""
    factor = 6 if compressed else 2
    return max(10, min(500, math.ceil(size * factor / 1e9) + 2))


def _resources(params, engine: str, length: int):
    if engine == Database.Engine.REDIS:
        return (
            parse_cpu(params.get("cpu") or "0.25", low="0.10", high="4"),
            parse_range(params.get("memory_mb") or 256, 64, 4096, "Memory"),
            parse_range(params.get("disk_gb") or 1, 1, 50, "Disk"),
        )
    return (
        parse_cpu(params.get("cpu") or "1", low="0.10", high="8"),
        parse_range(params.get("memory_mb") or 1024, 256, 16384, "Memory"),
        parse_range(params.get("disk_gb") or (_disk_for(length, True) if length else 10), 1, 500, "Disk"),
    )


class DatabaseImport(APIView):
    parser_classes = []

    def post(self, request):
        params = request.query_params
        name = (params.get("name") or "").strip().lower()
        error = name_error(name)
        if error:
            return Response({"detail": error}, status=400)
        engine = (params.get("engine") or "").strip()
        if engine and engine not in ENGINES:
            return Response({"detail": tr("Engine must be postgres, mysql, mariadb, mongodb or redis")}, status=400)
        version = (params.get("version") or "").strip()
        url = (params.get("url") or "").strip()
        uploaded = None
        length = int(request.META.get("CONTENT_LENGTH") or 0)
        if params.get("upload"):
            uploaded = uploads.find(request, params["upload"])
            if uploaded is None:
                return Response({"detail": uploads.MISSING}, status=400)
            length = uploaded.stat().st_size
        elif url:
            try:
                dumps.check_url(url)
            except dumps.DumpError as exc:
                return Response({"detail": str(exc)}, status=400)
        elif length <= 0:
            return Response({"detail": tr("A backup file or url is required")}, status=400)
        # Validate everything before reading the body so a large file is not uploaded for nothing. The CLI sends it compressed.
        try:
            resources = _resources(params, engine, length)
            ensure_capacity(float(resources[0]), resources[1], resources[2])
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)

        dest = uploads.folder() / f"import-{name}-{uuid.uuid4().hex}"
        info = {}
        if uploaded is not None:
            uploaded.replace(dest)
        elif not url:
            written = 0
            with dest.open("wb") as handle:
                while written < length:
                    chunk = request.stream.read(min(CHUNK, length - written))
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
            if written != length:
                dest.unlink(missing_ok=True)
                return Response({"detail": tr("Upload is incomplete; please try again")}, status=400)
        if not url:
            try:
                info = dumps.detect(dest)
            except dumps.DumpError as exc:
                if not engine:
                    dest.unlink(missing_ok=True)
                    return Response({"detail": str(exc)}, status=400)
            if not engine and info.get("engine") == Database.Engine.REDIS:
                resources = _resources(params, Database.Engine.REDIS, length)
        engine = engine or info.get("engine") or "postgres"
        cpu, memory_mb, disk_gb = resources
        if Database.objects.filter(name=name).exists():
            dest.unlink(missing_ok=True)
            return Response({"detail": tr("This name is already taken")}, status=400)

        database = Database.objects.create(
            organization=request.membership.organization,
            name=name,
            engine=engine,
            version=version or info.get("version") or ENGINES[engine],
            cpu=cpu,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
            region="local",
            status=Database.Status.PROVISIONING,
        )
        payload = {"database_id": database.id, "name": name, "path": str(dest), "engine": params.get("engine") or "", "version": version}
        if url:
            payload["url"] = url
        job = enqueue(Job.Kind.IMPORT_DB, payload, request.user, database.organization)
        database.refresh_from_db()
        return Response(
            {"database": database_payload(database, True, detail=True), "job": {"id": job.id, "status": job.status}, "detected": info},
            status=202,
        )


class DatabaseImportStatus(APIView):
    def get(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id, kind=Job.Kind.IMPORT_DB, organization=request.membership.organization)
        data = {"id": job.id, "status": job.status, "log": job.log, "result": job.result or {}}
        database = Database.objects.filter(pk=(job.payload or {}).get("database_id")).first()
        if database is not None:
            data["database"] = database_payload(database, request.membership.role != "viewer", detail=True)
        return Response(data)
