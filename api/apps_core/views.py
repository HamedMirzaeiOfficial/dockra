import http.client
import ipaddress
import os
import re
import secrets
import socket
import struct
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Membership
from apps_core.models import App, CronJob, Deployment, Disk, DiskBackup, Domain, EnvVar, Redirect
from apps_core.present import (
    app_payload,
    cron_payload,
    deployment_payload,
    disk_backup_payload,
    disk_payload,
    domain_payload,
    env_payload,
    redirect_payload,
)
from databases.models import Database
from engine import blobs
from engine.actions import backup_disk, change_power, deploy_app, enqueue, restore_disk, rollback_app, sync_app
from engine.metrics import RANGES, metrics_for, series_for
from engine.runner import services_status
from engine.source import DEFAULT_PORTS
from engine.models import Job
from engine.resources import QuotaExceeded, app_demand, ensure_capacity, parse_cpu, parse_range, snapshot

def default_hostname(slug: str) -> str:
    if settings.DOCKRA_APPS_DOMAIN:
        return f"{slug}.{settings.DOCKRA_APPS_DOMAIN}"
    return f"{slug}.{settings.PUBLIC_IP}.sslip.io"


def is_default_hostname(hostname: str) -> bool:
    if settings.DOCKRA_APPS_DOMAIN and hostname.endswith("." + settings.DOCKRA_APPS_DOMAIN):
        return True
    return hostname.endswith(".sslip.io")


SLUG_RE = re.compile(r"^[a-z](?:[a-z0-9-]{0,38}[a-z0-9])?$")
HOSTNAME_RE = re.compile(r"^(?=.{1,200}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")


SECRET_MASK = "••••••"


def _reveal(request) -> bool:
    return request.membership.role != Membership.Role.VIEWER


def _app(request, slug) -> App:
    return get_object_or_404(App, organization=request.membership.organization, slug=slug)


def _job(job: Job) -> dict:
    return {"id": job.id, "status": job.status, "log": job.log}


def _sync_running(request, app):
    job = sync_app(app, request.user)
    if job is not None and job.status == Job.Status.FAILED:
        lines = (job.log or "").strip().splitlines()
        reason = lines[-1] if lines else "unknown error"
        return Response({"detail": f"Saved, but could not be applied to the container: {reason}"}, status=502)
    return None


def _failed(job: Job):
    if job.status != Job.Status.FAILED:
        return None
    lines = (job.log or "").strip().splitlines()
    return Response({"detail": lines[-1] if lines else "The operation failed"}, status=400)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        from engine.runner import services_status

        services = services_status()
        healthy = all(item["ok"] for item in services)
        # 503 when any service is silent, for external monitors (e.g. Uptime Kuma).
        return Response(
            {"ok": healthy, "driver": settings.DOCKRA_DRIVER, "services": services}, status=200 if healthy else 503
        )


class DashboardView(APIView):
    def get(self, request):
        org = request.membership.organization
        apps = list(org.apps.all())
        domains = Domain.objects.filter(app__organization=org)
        databases = org.databases.all()
        running = [app for app in apps if app.status == App.Status.RUNNING]
        cpus = [metrics_for(app)["cpu"] for app in running]
        deployments = (
            Deployment.objects.filter(app__organization=org)
            .select_related("app", "created_by")
            .order_by("-created_at")[:8]
        )
        return Response(
            {
                "apps_total": len(apps),
                "apps_running": len(running),
                "databases_total": databases.count(),
                "domains_total": domains.count(),
                "domains_ssl": domains.filter(ssl_status=Domain.Ssl.ACTIVE).count(),
                "cpu_avg": round(sum(cpus) / len(cpus)) if cpus else 0,
                "capacity": snapshot(),
                "services": services_status(),
                "recent_deployments": [
                    {**deployment_payload(item), "app": item.app.slug, "app_name": item.app.name}
                    for item in deployments
                ],
                "apps": [{**app_payload(app), "metrics": metrics_for(app)} for app in apps],
            }
        )


class AppListCreate(APIView):
    def get(self, request):
        apps = request.membership.organization.apps.all()
        return Response([app_payload(app) for app in apps])

    def post(self, request):
        data = request.data
        name = (data.get("name") or "").strip()
        slug = (data.get("slug") or "").strip().lower()
        platform = data.get("platform") or App.Platform.DJANGO
        if not name:
            return Response({"detail": "Name is required"}, status=400)
        if not SLUG_RE.match(slug):
            return Response({"detail": "The ID may only contain lowercase letters, digits and dashes"}, status=400)
        if platform not in App.Platform.values:
            return Response({"detail": "Unsupported platform: django, node, nextjs, static or docker"}, status=400)
        if App.objects.filter(slug=slug).exists():
            return Response({"detail": "This ID is already taken"}, status=400)
        region = App.Region.LOCAL
        port = DEFAULT_PORTS.get(platform, 8000)
        try:
            instances = parse_range(data.get("instances") or 1, 1, 8, "Instances")
            cpu = parse_cpu(data.get("cpu") or "0.5")
            memory_mb = parse_range(data.get("memory_mb") or 512, 128, 8192, "Memory")
            port = parse_range(data.get("port") or port, 1, 65535, "Port")
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        app = App.objects.create(
            organization=request.membership.organization,
            name=name,
            slug=slug,
            platform=platform,
            region=region,
            status=App.Status.STOPPED,
            instances=instances,
            port=port,
            git_url=(data.get("git_url") or "").strip(),
            git_branch=(data.get("git_branch") or "").strip()[:120],
            runtime="",
            cpu=cpu,
            memory_mb=memory_mb,
            created_by=request.user,
        )
        Domain.objects.create(
            app=app,
            hostname=default_hostname(slug),
            kind=Domain.Kind.SSLIP,
            is_primary=True,
            ssl_status=Domain.Ssl.PENDING,
        )
        return Response(app_payload(app, detail=True), status=201)


class AppDetail(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        from engine.tls import sync_certificates

        sync_certificates(list(app.domains.all()))
        return Response(app_payload(app, _reveal(request), detail=True))

    def patch(self, request, slug):
        app = _app(request, slug)
        data = request.data
        try:
            if "instances" in data and data["instances"] != "":
                app.instances = parse_range(data["instances"], 1, 8, "Instances")
            if "cpu" in data and data["cpu"] != "":
                app.cpu = parse_cpu(data["cpu"])
            if "memory_mb" in data and data["memory_mb"] != "":
                app.memory_mb = parse_range(data["memory_mb"], 128, 8192, "Memory")
            if "port" in data and data["port"] != "":
                app.port = parse_range(data["port"], 1, 65535, "Port")
            if app.status in (App.Status.RUNNING, App.Status.DEPLOYING):
                ensure_capacity(*app_demand(app), skip_app_id=app.id)
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        if "git_url" in data:
            app.git_url = (data.get("git_url") or "").strip()
        if "git_branch" in data:
            app.git_branch = (data.get("git_branch") or "").strip()[:120]
        if "health_path" in data:
            health = (data.get("health_path") or "").strip()[:200]
            app.health_path = health if not health or health.startswith("/") else "/" + health
        if data.get("platform"):
            if data["platform"] not in App.Platform.values:
                return Response({"detail": "Unsupported platform"}, status=400)
            app.platform = data["platform"]
        if "name" in data and data.get("name"):
            app.name = data["name"].strip()
        app.save()
        if app.status == App.Status.RUNNING:
            job = enqueue(Job.Kind.APPLY_RESOURCES, {"app_id": app.id}, request.user, app.organization)
            failed = _failed(job)
            if failed:
                return failed
        return Response(app_payload(app, _reveal(request), detail=True))

    def delete(self, request, slug):
        if request.membership.role not in (Membership.Role.OWNER, Membership.Role.ADMIN):
            return Response({"detail": "Only an admin can delete an app"}, status=403)
        app = _app(request, slug)
        job = enqueue(Job.Kind.REMOVE_APP, {"app_id": app.id}, request.user, app.organization)
        failed = _failed(job)
        if failed:
            return failed
        if job.status == Job.Status.DONE:
            return Response(status=204)
        return Response({"job": _job(job)}, status=202)


class DeployView(APIView):
    def post(self, request, slug):
        app = _app(request, slug)
        source_path = ""
        tree = ""
        files = request.data.get("files") if hasattr(request.data, "get") else None
        if isinstance(files, list):
            try:
                rows = blobs.normalize_tree(files)
            except blobs.TreeError as exc:
                return Response({"detail": str(exc)}, status=400)
            absent = blobs.missing(rows)
            if absent:
                return Response({"detail": f"{len(absent)} files have not been uploaded yet", "missing": absent}, status=409)
            tree = blobs.save_tree(rows)
        upload = request.FILES.get("file")
        if upload is not None and not tree:
            folder = Path(settings.MEDIA_ROOT) / "sources"
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"{uuid.uuid4().hex}.tar.gz"
            temp = getattr(upload, "temporary_file_path", None)
            if temp:
                # The temporary file is on the same disk; move it without copying.
                os.replace(temp(), dest)
            else:
                with dest.open("wb") as handle:
                    for chunk in upload.chunks():
                        handle.write(chunk)
            source_path = str(dest)
        try:
            dep, job = deploy_app(
                app,
                request.user,
                message=(request.data.get("message") or "Deploy").strip(),
                git_url=(request.data.get("git_url") or "").strip(),
                source_path=source_path,
                tree=tree,
            )
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        failed = _failed(job)
        if failed:
            return failed
        code = 202 if job.status == Job.Status.QUEUED else 200
        return Response({"deployment": deployment_payload(dep), "job": _job(job)}, status=code)


class PowerView(APIView):
    action = Job.Kind.RESTART

    def post(self, request, slug):
        app = _app(request, slug)
        try:
            job = change_power(app, request.user, self.action)
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        failed = _failed(job)
        if failed:
            return failed
        code = 202 if job.status == Job.Status.QUEUED else 200
        return Response({"app": app_payload(app), "job": _job(job)}, status=code)


class RestartView(PowerView):
    action = Job.Kind.RESTART


class StopView(PowerView):
    action = Job.Kind.STOP


class StartView(PowerView):
    action = Job.Kind.START


class RollbackView(APIView):
    def post(self, request, slug):
        app = _app(request, slug)
        dep = get_object_or_404(Deployment, app=app, pk=request.data.get("deployment_id"))
        try:
            job = rollback_app(app, request.user, dep)
        except QuotaExceeded as exc:
            return Response({"detail": str(exc)}, status=400)
        failed = _failed(job)
        if failed:
            return failed
        code = 202 if job.status == Job.Status.QUEUED else 200
        return Response({"deployment": deployment_payload(dep), "job": _job(job), "app": app_payload(app)}, status=code)


class DeploymentList(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        rows = app.deployments.select_related("created_by").all()
        return Response([deployment_payload(item) for item in rows])


class DeploymentCancel(APIView):
    def post(self, request, slug, pk):
        app = _app(request, slug)
        dep = get_object_or_404(Deployment, app=app, pk=pk)
        if dep.status not in (Deployment.Status.QUEUED, Deployment.Status.BUILDING):
            return Response({"detail": "This deployment is not queued or building and cannot be canceled"}, status=400)
        from engine.runner import request_cancel

        state = request_cancel(dep)
        dep.refresh_from_db()
        app.refresh_from_db()
        code = 200 if state == "canceled" else 202
        return Response({"deployment": deployment_payload(dep), "state": state}, status=code)


class LogView(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        dep_id = request.query_params.get("deployment")
        if dep_id:
            dep = get_object_or_404(Deployment, app=app, pk=dep_id)
        else:
            dep = app.deployments.first()
        text = dep.log if dep else ""
        from engine.runner import run_rpc

        try:
            live = run_rpc(Job.Kind.LOGS, {"app_id": app.id, "tail": 400}, request.user, app.organization, 30).get("text", "")
        except RuntimeError as exc:
            live = f"(container log unavailable: {exc})"
        if live:
            text = (text + "\n" + live).strip()
        return Response({"deployment": dep.id if dep else None, "text": text, "updated_at": timezone.now().isoformat()})


class EnvView(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response([env_payload(item, _reveal(request)) for item in app.env_vars.all()])

    def put(self, request, slug):
        app = _app(request, slug)
        items = request.data.get("items") or []
        seen = []
        for item in items:
            key = str(item.get("key") or "").strip()
            if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                return Response({"detail": f"Invalid key: {key}"}, status=400)
            value = str(item.get("value") or "")
            if value == SECRET_MASK and app.env_vars.filter(key=key, is_secret=True).exists():
                # A client echoed back the masked placeholder; never overwrite the real secret with it.
                value = app.env_vars.get(key=key).value
            EnvVar.objects.update_or_create(
                app=app,
                key=key,
                defaults={"value": value, "is_secret": bool(item.get("is_secret"))},
            )
            seen.append(key)
        app.env_vars.exclude(key__in=seen).delete()
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response([env_payload(item, True) for item in app.env_vars.all()])


class DomainListCreate(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response([domain_payload(item) for item in app.domains.all()])

    def post(self, request, slug):
        app = _app(request, slug)
        hostname = (request.data.get("hostname") or "").strip().lower().rstrip(".")
        if not HOSTNAME_RE.match(hostname):
            return Response({"detail": "Invalid domain; use a bare hostname such as app.example.com (no http:// and no path)"}, status=400)
        if Domain.objects.filter(hostname=hostname).exists():
            return Response({"detail": "This domain is already registered"}, status=400)
        kind = Domain.Kind.SSLIP if is_default_hostname(hostname) else Domain.Kind.CUSTOM
        domain = Domain.objects.create(app=app, hostname=hostname, kind=kind, ssl_status=Domain.Ssl.PENDING)
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(domain_payload(domain), status=201)


class DomainIndex(APIView):
    def get(self, request):
        rows = list(Domain.objects.filter(app__organization=request.membership.organization).select_related("app"))
        from engine.tls import sync_certificates

        sync_certificates(rows)
        return Response([domain_payload(item) for item in rows])


def _skip_name(packet: bytes, pos: int) -> int:
    while True:
        size = packet[pos]
        if size & 0xC0 == 0xC0:
            return pos + 2
        if size == 0:
            return pos + 1
        pos += size + 1


def query_a(hostname: str, server: str, timeout: float = 1.5) -> list[str]:
    """Query an A record directly from a DNS server (UDP), independent of the container resolver."""
    ident = secrets.randbits(16)
    question = b"".join(bytes([len(part)]) + part.encode("idna") for part in hostname.split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + struct.pack(">HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        reply, _ = sock.recvfrom(4096)
    rid, flags, qd, an = struct.unpack(">HHHH", reply[:8])
    if rid != ident or flags & 0x000F:
        return []
    pos = 12
    for _ in range(qd):
        pos = _skip_name(reply, pos) + 4
    found = []
    for _ in range(an):
        pos = _skip_name(reply, pos)
        rtype, _cls, _ttl, size = struct.unpack(">HHIH", reply[pos : pos + 10])
        pos += 10
        if rtype == 1 and size == 4:
            found.append(socket.inet_ntoa(reply[pos : pos + 4]))
        pos += size
    return found


def resolve_ipv4(hostname: str) -> list[str]:
    try:
        rows = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        return sorted({row[4][0] for row in rows})
    except (socket.gaierror, UnicodeError, OSError):
        pass
    # The container resolver may point at a server that is unreachable from this network; ask other resolvers directly.
    for server in settings.DOCKRA_DNS_SERVERS:
        try:
            found = query_a(hostname, server)
        except (OSError, UnicodeError, struct.error, IndexError):
            continue
        if found:
            return sorted(set(found))
    return []


# ArvanCloud edge range; other CDNs are detected from the Server header.
ARVAN_NETWORKS = [ipaddress.ip_network("185.143.232.0/22")]


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def edge_server(hostname: str, ip: str) -> str:
    """Server header of the domain's HTTP response, e.g. ArvanCloud or cloudflare.

    The hostname is user-controlled, so its A record may point anywhere. The API container sits on the
    platform's internal network; probing private, loopback or link-local addresses would turn this check
    into an SSRF primitive, so only public addresses are ever contacted.
    """
    if not _is_public(ip):
        return ""
    conn = http.client.HTTPConnection(ip, 80, timeout=4)
    try:
        conn.request("HEAD", "/", headers={"Host": hostname, "User-Agent": "dockra-dns-check"})
        return conn.getresponse().getheader("Server") or ""
    except (OSError, http.client.HTTPException):
        return ""
    finally:
        conn.close()


def behind_cdn(hostname: str, found: list[str]) -> str:
    if any(ipaddress.ip_address(ip) in net for ip in found for net in ARVAN_NETWORKS):
        return "ArvanCloud"
    server = edge_server(hostname, found[0]).lower()
    if "arvan" in server:
        return "ArvanCloud"
    if "cloudflare" in server:
        return "Cloudflare"
    return ""


class DomainCheck(APIView):
    """Does the domain's A record point at this server's IP?"""

    def get(self, request, slug, pk):
        app = _app(request, slug)
        domain = get_object_or_404(Domain, app=app, pk=pk)
        found = resolve_ipv4(domain.hostname)
        expected = settings.PUBLIC_IP
        cdn = behind_cdn(domain.hostname, found) if found and expected not in found else ""
        if not found:
            state, message = "missing", "The domain does not resolve yet; add the record or wait a few minutes."
        elif expected in found:
            state, message = "ok", "The record is correct and points at this server."
        elif cdn:
            state = "proxied"
            message = (
                f"The {cdn} proxy is enabled and the domain points at its IPs ({', '.join(found)}). "
                "Either disable the proxy so the SSL certificate can be issued on this server, "
                f"or keep it on and set {cdn}'s connection to the origin to HTTP."
            )
        else:
            state = "mismatch"
            message = f"The domain points at {', '.join(found)}, not {expected}. If a CDN proxy is enabled for the record, disable it."
        return Response({"state": state, "message": message, "found": found, "expected": expected})


class DomainDetail(APIView):
    def patch(self, request, slug, pk):
        app = _app(request, slug)
        domain = get_object_or_404(Domain, app=app, pk=pk)
        if "redirect_https" in request.data:
            domain.redirect_https = bool(request.data.get("redirect_https"))
            domain.save(update_fields=["redirect_https"])
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(domain_payload(domain))

    def delete(self, request, slug, pk):
        app = _app(request, slug)
        domain = get_object_or_404(Domain, app=app, pk=pk)
        if domain.is_primary:
            return Response({"detail": "The app's primary domain cannot be removed"}, status=400)
        app.redirects.filter(source_host=domain.hostname).delete()
        domain.delete()
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(status=204)


class RedirectListCreate(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response([redirect_payload(item) for item in app.redirects.all()])

    def post(self, request, slug):
        from apps_core.validation import RedirectError, check_redirect

        app = _app(request, slug)
        try:
            fields = check_redirect(app, request.data)
        except RedirectError as exc:
            return Response({"detail": str(exc)}, status=400)
        redirect = Redirect.objects.create(app=app, **fields)
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(redirect_payload(redirect), status=201)


class RedirectDetail(APIView):
    def patch(self, request, slug, pk):
        from apps_core.validation import RedirectError, check_redirect

        app = _app(request, slug)
        redirect = get_object_or_404(Redirect, app=app, pk=pk)
        current = redirect_payload(redirect)
        try:
            fields = check_redirect(app, {**current, **request.data}, redirect=redirect)
        except RedirectError as exc:
            return Response({"detail": str(exc)}, status=400)
        for key, value in fields.items():
            setattr(redirect, key, value)
        redirect.save()
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(redirect_payload(redirect))

    def delete(self, request, slug, pk):
        app = _app(request, slug)
        get_object_or_404(Redirect, app=app, pk=pk).delete()
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(status=204)


class MetricsView(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        data = metrics_for(app)
        range_key = request.query_params.get("range") or "1h"
        if range_key not in RANGES:
            return Response({"detail": "Range must be 1h, 6h, 24h or 7d"}, status=400)
        data["series"] = series_for(app, range_key)
        return Response(data)


class DiskListCreate(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response([disk_payload(item) for item in app.disks.all()])

    def post(self, request, slug):
        from apps_core.validation import DiskError, check_disk

        app = _app(request, slug)
        name = (request.data.get("name") or "").strip()
        try:
            mount_to = check_disk(app, name, request.data.get("mount_to") or "")
            size = parse_range(request.data.get("size_gb") or 1, 1, 200, "Disk size")
            _disk_capacity(app, name, size)
        except (DiskError, QuotaExceeded) as exc:
            return Response({"detail": str(exc)}, status=400)
        disk = Disk.objects.create(app=app, name=name, mount_to=mount_to, size_gb=size)
        failed = _sync_running(request, app)
        if failed:
            return failed
        return Response(disk_payload(disk), status=201)


def _disk_capacity(app: App, name: str, size: int) -> None:
    others = sum(item.size_gb for item in app.disks.exclude(name=name))
    if app.status in (App.Status.RUNNING, App.Status.DEPLOYING):
        count = max(app.instances, 1)
        ensure_capacity(float(app.cpu) * count, app.memory_mb * count, others + size, skip_app_id=app.id)
    else:
        ensure_capacity(0, 0, others + size, skip_app_id=app.id)


class CronListCreate(APIView):
    def get(self, request, slug):
        app = _app(request, slug)
        return Response([cron_payload(item) for item in app.cron_jobs.all()])

    def post(self, request, slug):
        app = _app(request, slug)
        schedule = (request.data.get("schedule") or "").strip()
        command = (request.data.get("command") or "").strip()
        if len(schedule.split()) != 5 or not command:
            return Response({"detail": "A five-field schedule and a command are required"}, status=400)
        error = _cron_error(schedule)
        if error:
            return Response({"detail": error}, status=400)
        cron = CronJob.objects.create(app=app, schedule=schedule, command=command)
        return Response(cron_payload(cron), status=201)


def _cron_error(schedule: str) -> str:
    from engine.cron import validate

    try:
        validate(schedule)
    except ValueError:
        return "Invalid cron schedule"
    return ""


class CronDetail(APIView):
    def _cron(self, request, slug, pk):
        return get_object_or_404(CronJob, app=_app(request, slug), pk=pk)

    def patch(self, request, slug, pk):
        cron = self._cron(request, slug, pk)
        data = request.data
        if "schedule" in data:
            schedule = str(data.get("schedule") or "").strip()
            error = _cron_error(schedule)
            if error:
                return Response({"detail": error}, status=400)
            cron.schedule = schedule
        if "command" in data:
            command = str(data.get("command") or "").strip()
            if not command:
                return Response({"detail": "Command is required"}, status=400)
            cron.command = command[:400]
        if "enabled" in data:
            cron.enabled = bool(data.get("enabled"))
        cron.save()
        return Response(cron_payload(cron))

    def delete(self, request, slug, pk):
        self._cron(request, slug, pk).delete()
        return Response(status=204)


class DiskDetail(APIView):
    """PATCH the backup schedule; DELETE the disk: containers restart without it, then the data is permanently removed."""

    def patch(self, request, slug, name):
        """size_gb (grow only), mount_to and backup schedule. Changing size or path recreates containers without downtime."""
        from apps_core.validation import DiskError, check_disk

        app = _app(request, slug)
        disk = get_object_or_404(Disk, app=app, name=name)
        data = request.data
        changed = False
        try:
            if data.get("size_gb") not in (None, ""):
                size = parse_range(data["size_gb"], 1, 200, "Disk size")
                if size < disk.size_gb:
                    raise QuotaExceeded(f"Disks can only grow; the current size is {disk.size_gb} GB")
                if size != disk.size_gb:
                    _disk_capacity(app, disk.name, size)
                    disk.size_gb, changed = size, True
            if data.get("mount_to") not in (None, ""):
                mount = check_disk(app, disk.name, data["mount_to"], disk=disk)
                if mount != disk.mount_to:
                    disk.mount_to, changed = mount, True
            if "backup_hours" in data:
                raw = data["backup_hours"]
                disk.backup_hours = None if raw in (None, "") else parse_range(raw, 0, 24 * 30, "Backup interval")
            if "backup_keep" in data:
                raw = data["backup_keep"]
                disk.backup_keep = None if raw in (None, "") else parse_range(raw, 1, 30, "Backups to keep")
        except (DiskError, QuotaExceeded) as exc:
            return Response({"detail": str(exc)}, status=400)
        disk.save(update_fields=["size_gb", "mount_to", "backup_hours", "backup_keep"])
        if changed:
            failed = _sync_running(request, app)
            if failed:
                return failed
        return Response(disk_payload(disk))

    def delete(self, request, slug, name):
        app = _app(request, slug)
        disk = get_object_or_404(Disk, app=app, name=name)
        job = enqueue(Job.Kind.REMOVE_DISK, {"disk_id": disk.id}, request.user, app.organization)
        failed = _failed(job)
        if failed:
            return failed
        return Response({"job": _job(job)}, status=202 if job.status == Job.Status.QUEUED else 200)


class DiskBackupList(APIView):
    """Backups of a disk, including those of a previously deleted disk with the same name."""

    def get(self, request, slug, name):
        app = _app(request, slug)
        return Response([disk_backup_payload(item) for item in app.disk_backups.filter(disk_name=name)])

    def post(self, request, slug, name):
        app = _app(request, slug)
        disk = get_object_or_404(Disk, app=app, name=name)
        backup = DiskBackup.objects.create(app=app, disk=disk, disk_name=disk.name, kind=DiskBackup.Kind.MANUAL)
        job = backup_disk(backup, request.user)
        failed = _failed(job)
        if failed:
            return failed
        backup.refresh_from_db()
        return Response(disk_backup_payload(backup), status=201)


class DiskBackupDetail(APIView):
    write_role = "admin"

    def delete(self, request, slug, name, bid):
        from engine.disk_backup import delete_backup

        backup = get_object_or_404(DiskBackup, app=_app(request, slug), disk_name=name, pk=bid)
        delete_backup(backup)
        return Response(status=204)


class DiskBackupDownload(APIView):
    def get(self, request, slug, name, bid):
        if request.membership.role == Membership.Role.VIEWER:
            return Response({"detail": "Viewers cannot download backups"}, status=403)
        backup = get_object_or_404(
            DiskBackup, app=_app(request, slug), disk_name=name, pk=bid, status=DiskBackup.Status.READY
        )
        path = Path(backup.path)
        if not path.is_file():
            return Response({"detail": "The backup file is not on the server"}, status=404)
        return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


class DiskBackupRestore(APIView):
    """Replace the disk contents with a backup; a backup of the current state is taken automatically first."""

    write_role = "admin"

    def post(self, request, slug, name, bid):
        app = _app(request, slug)
        disk = get_object_or_404(Disk, app=app, name=name)
        backup = get_object_or_404(DiskBackup, app=app, disk_name=name, pk=bid, status=DiskBackup.Status.READY)
        job = restore_disk(disk, backup, request.user)
        failed = _failed(job)
        if failed:
            return failed
        return Response({"job": _job(job)}, status=202 if job.status == Job.Status.QUEUED else 200)


class MonitoringView(APIView):
    def get(self, request):
        apps = request.membership.organization.apps.all()
        db_rows = Database.objects.filter(organization=request.membership.organization)
        return Response(
            {
                "apps": [{**app_payload(app), "metrics": metrics_for(app)} for app in apps],
                "databases": [
                    {"id": item.id, "name": item.name, "engine": item.engine, "status": item.status, "memory_mb": item.memory_mb}
                    for item in db_rows
                ],
            }
        )
