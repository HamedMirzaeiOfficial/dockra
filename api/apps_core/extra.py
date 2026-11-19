import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import tarfile
import tempfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.http import FileResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps_core.models import App, Disk
from audit.models import AuditLog
from engine import blobs
from engine.actions import deploy_app
from engine.resources import QuotaExceeded
from config.i18n import tr

MAX_BLOB_BATCH = 2 * 1024 ** 3


def _app(request, slug) -> App:
    return get_object_or_404(App, organization=request.membership.organization, slug=slug)


def _audit(request, action: str, target: str, detail: str = "") -> None:
    AuditLog.objects.create(
        organization=request.membership.organization,
        actor=request.user,
        action=action,
        target=target,
        detail=detail[:400],
    )


class SyncPlan(APIView):
    """File list with hashes; the response lists hashes the server does not have yet and that must be uploaded."""

    def post(self, request, slug):
        _app(request, slug)
        try:
            rows = blobs.normalize_tree(request.data.get("files"))
        except blobs.TreeError as exc:
            return Response({"detail": str(exc)}, status=400)
        absent = blobs.missing(rows)
        need = {row["hash"]: row["size"] for row in rows}
        return Response({"missing": absent, "bytes": sum(need[item] for item in absent), "files": len(rows)})


class BlobUpload(APIView):
    """Raw body: tar or tar.gz whose member names are the sha256 of their content."""

    parser_classes = []

    def post(self, request, slug):
        _app(request, slug)
        length = int(request.META.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            return Response({"detail": tr("Request body is empty")}, status=400)
        if length > MAX_BLOB_BATCH:
            return Response({"detail": tr("Each bundle is limited to 2 GB")}, status=413)
        try:
            stored = blobs.store_tar(request.stream)
        except (blobs.TreeError, tarfile.TarError, EOFError, OSError) as exc:
            return Response({"detail": tr("File bundle is corrupt: {exc}", exc=exc)}, status=400)
        return Response({"stored": stored})


class PlainText(BaseRenderer):
    media_type = "text/plain"
    format = "txt"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data if isinstance(data, bytes) else str(data).encode()


def _rpc(request, kind, payload, timeout):
    from engine.runner import run_rpc

    return run_rpc(kind, payload, request.user, request.membership.organization, timeout)


class LogTicket(APIView):
    """Live logs come from the terminal service over WebSocket; the API itself has no Docker access."""

    write_role = "viewer"

    def post(self, request, slug):
        from engine.terminal import make_ticket, public_url

        app = _app(request, slug)
        if not _terminal_up():
            return Response({"detail": tr("The live log service is unavailable")}, status=503)
        try:
            tail = max(0, min(int(request.query_params.get("tail") or 200), 5000))
        except ValueError:
            tail = 200
        ticket = make_ticket("logs", request.user.id, app.id)
        return Response({"ticket": ticket, "url": public_url(request, "logs", ticket) + f"&tail={tail}"})


class LogArchiveList(APIView):
    def get(self, request, slug):
        from engine.logs import list_archive

        return Response(list_archive(_app(request, slug).slug))


class LogArchiveRead(APIView):
    renderer_classes = [JSONRenderer, PlainText]

    def get(self, request, slug, name):
        from engine.logs import archive_path, iter_lines

        app = _app(request, slug)
        path = archive_path(app.slug, name)
        if path is None:
            return Response({"detail": tr("Log not found")}, status=404)
        def chunks():
            buf = []
            size = 0
            for line in iter_lines(path):
                buf.append(line + "\n")
                size += len(line) + 1
                if size >= 64 * 1024:
                    yield "".join(buf)
                    buf, size = [], 0
            if buf:
                yield "".join(buf)

        response = StreamingHttpResponse(chunks(), content_type="text/plain; charset=utf-8")
        return response


class LogSearch(APIView):
    def get(self, request, slug):
        from engine.logs import search

        app = _app(request, slug)
        query = (request.query_params.get("q") or "").strip()
        if len(query) < 2:
            return Response({"detail": tr("Search needs at least two characters")}, status=400)
        try:
            limit = max(1, min(int(request.query_params.get("limit") or 500), 2000))
        except ValueError:
            limit = 500
        try:
            current = _rpc(request, "logs", {"app_id": app.id, "tail": 5000}, 30).get("text", "")
        except RuntimeError:
            current = ""
        return Response(search(app.slug, query, current, limit))


CWD_MARK = "__DOCKRA_CWD__"


class ExecView(APIView):
    def post(self, request, slug):
        app = _app(request, slug)
        command = str(request.data.get("command") or "").strip()
        if not command:
            return Response({"detail": tr("Command is empty")}, status=400)
        cwd = str(request.data.get("cwd") or "").strip()
        script = command
        if cwd:
            script = f"cd {shlex.quote(cwd)} 2>/dev/null; {command}"
        script += f"\n__code=$?; printf '\\n{CWD_MARK}%s' \"$(pwd)\"; exit $__code"
        from django.conf import settings

        try:
            result = _rpc(request, "exec", {"app_id": app.id, "command": script}, settings.DOCKRA_EXEC_TIMEOUT + 15)
        except RuntimeError as exc:
            return Response({"detail": str(exc)}, status=400)
        output, code = result.get("output", ""), result.get("exit_code", 1)
        new_cwd = cwd
        if CWD_MARK in output:
            output, _, tail = output.rpartition(CWD_MARK)
            new_cwd = tail.strip() or cwd
            output = output[:-1] if output.endswith("\n") else output
        _audit(request, "command run", app.slug, command)
        return Response({"output": output[-200_000:], "exit_code": code, "cwd": new_cwd})


def _hook_url(request, app: App, secret: str = "") -> str:
    base = request.build_absolute_uri(f"/api/hooks/{app.slug}")
    return f"{base}?token={secret}" if secret else base


class WebhookView(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response({"enabled": bool(app.webhook_secret), "url": _hook_url(request, app)})

    def post(self, request, slug):
        app = _app(request, slug)
        if not app.git_url:
            return Response({"detail": tr("Set the Git repository URL in the app settings first")}, status=400)
        secret = secrets.token_urlsafe(24)
        app.webhook_secret = secret
        app.save(update_fields=["webhook_secret"])
        _audit(request, "webhook", app.slug, "created or rotated")
        return Response({"enabled": True, "url": _hook_url(request, app, secret), "secret": secret})

    def delete(self, request, slug):
        app = _app(request, slug)
        app.webhook_secret = ""
        app.save(update_fields=["webhook_secret"])
        return Response(status=204)


def _hook_authorized(request, secret: str, body: bytes) -> bool:
    if not secret:
        return False
    signature = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    if signature.startswith("sha256="):
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[7:], expected)
    gitea = request.META.get("HTTP_X_GITEA_SIGNATURE", "")
    if gitea:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(gitea, expected)
    token = request.META.get("HTTP_X_GITLAB_TOKEN", "") or request.GET.get("token", "")
    return bool(token) and hmac.compare_digest(token, secret)


class HookReceive(APIView):
    """Push from GitHub, GitLab or Gitea; if the branch matches, a Git deployment is queued."""

    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = []

    def post(self, request, slug):
        app = App.objects.filter(slug=slug).first()
        body = request.body
        if app is None or not _hook_authorized(request, app.webhook_secret, body):
            return Response({"detail": tr("Invalid webhook signature")}, status=403)
        event = request.META.get("HTTP_X_GITHUB_EVENT") or request.META.get("HTTP_X_GITEA_EVENT") or ""
        if event == "ping":
            return Response({"ok": True, "pong": True})
        try:
            payload = json.loads(body.decode("utf-8") or "{}") if body else {}
        except (UnicodeDecodeError, ValueError):
            payload = {}
        ref = str(payload.get("ref") or "")
        if app.git_branch and ref and ref != f"refs/heads/{app.git_branch}":
            return Response({"ok": True, "skipped": f"branch {ref} does not match {app.git_branch}"})
        if not app.git_url:
            return Response({"detail": tr("The app has no Git URL")}, status=400)
        commit = payload.get("head_commit") or {}
        sha = str(payload.get("after") or commit.get("id") or "")[:7]
        title = str(commit.get("message") or "").splitlines()[0][:150] if commit.get("message") else ""
        message = " ".join(part for part in (f"webhook {sha}".strip(), title) if part)
        try:
            dep, _job = deploy_app(app, None, message=message, git_url=app.git_url)
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        AuditLog.objects.create(organization=app.organization, action="auto deploy", target=app.slug, detail=message[:400])
        return Response({"ok": True, "deployment": dep.id, "version": f"v{dep.number}"}, status=202)


def _disk_root(app: App, name: str, request=None) -> Path:
    """Mounted path of the disk; if it does not exist yet the terminal service creates it (the API has no Docker access)."""
    from engine.volumes import data_root

    disk = get_object_or_404(Disk, app=app, name=name)
    volume = f"dockra-disk-{app.slug}-{disk.name}"
    path = data_root() / "mnt" / volume / "data"
    if not path.is_dir():
        from engine.runner import run_rpc

        org = app.organization
        path = Path(run_rpc("volume", {"name": volume, "size_gb": disk.size_gb}, getattr(request, "user", None), org, 120)["path"])
    return path.resolve()


def _inside(root: Path, raw: str) -> Path:
    rel = str(raw or "").replace("\\", "/").strip("/")
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise PermissionError(tr("Path is outside the disk"))
    return target


def _rel(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return "/" if rel == "." else "/" + rel


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=dt_timezone.utc).isoformat()


def _give_to_app(path: Path) -> None:
    """Uploaded files are owned by uid 1000 inside the app container so the app can write to them."""
    items = [path] + (list(path.rglob("*")) if path.is_dir() else [])
    for item in items:
        try:
            os.chown(item, 1000, 1000, follow_symlinks=False)
        except (PermissionError, OSError, NotImplementedError):
            return


class DiskFiles(APIView):
    def get(self, request, slug, name):
        app = _app(request, slug)
        root = _disk_root(app, name, request)
        try:
            target = _inside(root, request.query_params.get("path"))
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=400)
        if not target.is_dir():
            return Response({"detail": tr("Folder not found")}, status=404)
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if stat.S_ISDIR(info.st_mode) else "file",
                    "size": info.st_size,
                    "modified": _iso(info.st_mtime),
                }
            )
        return Response({"path": _rel(root, target), "entries": entries})

    def delete(self, request, slug, name):
        app = _app(request, slug)
        root = _disk_root(app, name, request)
        try:
            target = _inside(root, request.query_params.get("path"))
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=400)
        if target == root:
            return Response({"detail": tr("The disk root cannot be deleted")}, status=400)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        else:
            return Response({"detail": tr("File not found")}, status=404)
        _audit(request, "disk file deleted", app.slug, f"{name}:{_rel(root, target.parent)}/{target.name}")
        return Response(status=204)


class DiskDownload(APIView):
    def get(self, request, slug, name):
        if request.membership.role == "viewer":
            return Response({"detail": tr("Viewers cannot download files")}, status=403)
        app = _app(request, slug)
        root = _disk_root(app, name, request)
        try:
            target = _inside(root, request.query_params.get("path"))
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=400)
        if target.is_file():
            return FileResponse(target.open("rb"), as_attachment=True, filename=target.name)
        if not target.is_dir():
            return Response({"detail": tr("File not found")}, status=404)
        from engine.volumes import data_root

        folder = data_root() / "tmp"
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".tar.gz", dir=folder)
        os.close(fd)
        with tarfile.open(tmp, "w:gz", compresslevel=3) as archive:
            archive.add(target, arcname=target.name if target != root else name)
        handle = open(tmp, "rb")
        try:
            # On Linux the open file stays valid until the response finishes.
            os.unlink(tmp)
        except OSError:
            pass
        label = target.name if target != root else name
        return FileResponse(handle, as_attachment=True, filename=f"{label}.tar.gz")


class DiskUpload(APIView):
    """multipart with a file field, or a raw tar/tar.gz body that is extracted into the target folder."""

    def post(self, request, slug, name):
        from engine import uploads

        app = _app(request, slug)
        uploaded = None
        if request.query_params.get("upload"):
            # A file that arrived in chunks via /api/uploads: with filename it is that file, without it a tar archive to extract.
            uploaded = uploads.find(request, request.query_params["upload"], allow_empty=bool(request.query_params.get("filename")))
            if uploaded is None:
                return Response({"detail": uploads.MISSING}, status=400)
        root = _disk_root(app, name, request)
        try:
            target = _inside(root, request.query_params.get("path"))
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=400)
        target.mkdir(parents=True, exist_ok=True)
        content_type = request.META.get("CONTENT_TYPE", "")
        if uploaded is not None and request.query_params.get("filename"):
            filename = re.sub(r"[/\\\x00]", "_", Path(request.query_params["filename"]).name) or "upload"
            dest = target / filename
            shutil.move(str(uploaded), dest)
            _give_to_app(dest)
            _audit(request, "disk file uploaded", app.slug, f"{name}:{_rel(root, dest)}")
            return Response({"ok": True, "path": _rel(root, dest)})
        if uploaded is not None:
            try:
                with tarfile.open(uploaded, mode="r:*") as archive:
                    for member in archive:
                        archive.extract(member, target, filter="data")
            except (tarfile.TarError, EOFError, OSError) as exc:
                return Response({"detail": tr("Invalid archive: {exc}", exc=exc)}, status=400)
            finally:
                uploaded.unlink(missing_ok=True)
            _give_to_app(target)
            _audit(request, "disk folder uploaded", app.slug, f"{name}:{_rel(root, target)}")
            return Response({"ok": True, "path": _rel(root, target)})
        if content_type.startswith("multipart/"):
            upload = request.FILES.get("file")
            if upload is None:
                return Response({"detail": tr("No file was sent")}, status=400)
            filename = re.sub(r"[/\\\x00]", "_", Path(upload.name).name) or "upload"
            dest = target / filename
            with dest.open("wb") as handle:
                for chunk in upload.chunks():
                    handle.write(chunk)
            _give_to_app(dest)
            _audit(request, "disk file uploaded", app.slug, f"{name}:{_rel(root, dest)}")
            return Response({"ok": True, "path": _rel(root, dest)})
        try:
            with tarfile.open(fileobj=request.stream, mode="r|*") as archive:
                for member in archive:
                    archive.extract(member, target, filter="data")
        except (tarfile.TarError, EOFError, OSError) as exc:
            return Response({"detail": tr("Invalid archive: {exc}", exc=exc)}, status=400)
        _give_to_app(target)
        _audit(request, "disk folder uploaded", app.slug, f"{name}:{_rel(root, target)}")
        return Response({"ok": True, "path": _rel(root, target)})


class DiskImport(APIView):
    """Import files into a disk: raw tar/zip body (?replace=1), or JSON with url or ftp. Runs in the background;
    poll status with GET /api/jobs/<id>."""

    parser_classes = []

    def post(self, request, slug, name):
        import uuid

        from django.conf import settings

        from accounts.permissions import ROLE_RANK
        from engine import disk_import, dumps, uploads
        from engine.actions import enqueue
        from engine.api import job_payload
        from engine.models import Job

        app = _app(request, slug)
        disk = get_object_or_404(Disk, app=app, name=name)
        content_type = request.META.get("CONTENT_TYPE", "")
        options = dict(request.query_params.items())
        if content_type.startswith("application/json"):
            try:
                options.update(json.loads(request.stream.read(1 << 16) or b"{}"))
            except (ValueError, TypeError):
                return Response({"detail": tr("Invalid JSON")}, status=400)
        replace = str(options.get("replace") or "").lower() in ("1", "true", "yes")
        if replace and ROLE_RANK[request.membership.role] < ROLE_RANK["admin"]:
            return Response({"detail": tr("Replacing the whole disk requires the admin role")}, status=403)
        payload = {"disk_id": disk.id, "replace": replace}
        try:
            if options.get("ftp"):
                disk_import.parse_ftp(str(options["ftp"]))
                payload["ftp"] = str(options["ftp"])
                payload["workers"] = max(1, min(16, int(options.get("workers") or 8)))
            elif options.get("url"):
                dumps.check_url(str(options["url"]))
                payload["url"] = str(options["url"])
        except (disk_import.DiskImportError, dumps.DumpError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=400)
        if "ftp" not in payload:
            folder = Path(settings.MEDIA_ROOT) / "restore"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"disk-{app.slug}-{disk.name}-{uuid.uuid4().hex}"
            payload["path"] = str(dest)
            if options.get("upload") and "url" not in payload:
                # An archive that arrived in chunks via /api/uploads.
                uploaded = uploads.find(request, options["upload"])
                if uploaded is None:
                    return Response({"detail": uploads.MISSING}, status=400)
                uploaded.replace(dest)
            elif "url" not in payload:
                length = int(request.META.get("CONTENT_LENGTH") or 0)
                if length <= 0:
                    return Response({"detail": tr("An archive, url or ftp is required")}, status=400)
                written = 0
                with dest.open("wb") as handle:
                    while written < length:
                        chunk = request.stream.read(min(1 << 20, length - written))
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                if written != length:
                    dest.unlink(missing_ok=True)
                    return Response({"detail": tr("Upload is incomplete; please try again")}, status=400)
        job = enqueue(Job.Kind.DISK_IMPORT, payload, request.user, app.organization)
        return Response({"job": job_payload(job)}, status=202)


class AlertList(APIView):
    def get(self, request):
        from engine.models import Alert

        status = request.query_params.get("status") or "firing"
        rows = Alert.objects.select_related("app").filter(app__organization=request.membership.organization) | Alert.objects.filter(app__isnull=True)
        if status in (Alert.Status.FIRING, Alert.Status.RESOLVED):
            rows = rows.filter(status=status)
        return Response(
            [
                {
                    "id": item.id,
                    "app": item.app.slug if item.app else None,
                    "app_name": item.app.name if item.app else None,
                    "kind": item.kind,
                    "kind_label": tr(item.get_kind_display()),
                    "message": item.message,
                    "status": item.status,
                    "started_at": item.started_at.isoformat(),
                    "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
                }
                for item in rows.order_by("-started_at")[:200]
            ]
        )


def _terminal_up() -> bool:
    import socket

    from django.conf import settings

    host = settings.DOCKRA_TERMINAL_HOST
    if not host:
        return True
    try:
        socket.create_connection((host, settings.DOCKRA_TERMINAL_PORT), timeout=1).close()
        return True
    except OSError:
        return False


class TerminalTicket(APIView):
    def post(self, request, slug):
        from engine.terminal import make_ticket, public_url

        app = _app(request, slug)
        if app.status != App.Status.RUNNING:
            return Response({"detail": tr("The app is not running")}, status=400)
        if not _terminal_up():
            return Response({"detail": tr("The terminal service is unavailable")}, status=503)
        ticket = make_ticket("terminal", request.user.id, app.id)
        return Response({"ticket": ticket, "url": public_url(request, "terminal", ticket)})


class TunnelTicket(APIView):
    def post(self, request, pk):
        from databases.models import Database
        from engine.terminal import make_ticket, public_url

        database = get_object_or_404(Database, organization=request.membership.organization, pk=pk)
        if database.status != Database.Status.RUNNING:
            return Response({"detail": tr("The database is not running")}, status=400)
        if not _terminal_up():
            return Response({"detail": tr("The terminal service is unavailable")}, status=503)
        ticket = make_ticket("tunnel", request.user.id, database.id)
        return Response({"ticket": ticket, "url": public_url(request, "tunnel", ticket)})
