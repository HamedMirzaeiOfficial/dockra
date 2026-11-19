from django.conf import settings

from apps_core.models import App, CronJob, Deployment, Disk, Domain, EnvVar, Redirect
from engine.metrics import metrics_for


def _iso(value):
    return value.isoformat() if value else None


def deployment_payload(dep: Deployment) -> dict:
    actor = ""
    if dep.created_by_id and dep.created_by:
        actor = dep.created_by.get_full_name() or dep.created_by.username
    return {
        "id": dep.id,
        "number": dep.number,
        "version": f"v{dep.number}",
        "status": dep.status,
        "status_label": dep.get_status_display(),
        "message": dep.message,
        "image": dep.image,
        "created_at": _iso(dep.created_at),
        "finished_at": _iso(dep.finished_at),
        "actor": actor,
        "log": dep.log,
        "is_current": dep.status == Deployment.Status.RUNNING,
    }


def app_payload(app, reveal_secrets: bool = False, detail: bool = False) -> dict:
    data = {
        "id": app.id,
        "name": app.name,
        "slug": app.slug,
        "platform": app.platform,
        "platform_label": app.get_platform_display(),
        "region": App.Region.LOCAL,
        "region_label": "This server",
        "status": app.status,
        "status_label": app.get_status_display(),
        "instances": 0 if app.status in ("stopped", "failed") else app.instances,
        "desired_instances": app.instances,
        "port": app.port,
        "git_url": app.git_url,
        "git_branch": app.git_branch,
        "health_path": app.health_path,
        "webhook_enabled": bool(app.webhook_secret),
        "runtime": app.runtime,
        "cpu": float(app.cpu),
        "memory_mb": app.memory_mb,
        "current_image": app.current_image,
        "created_at": _iso(app.created_at),
    }
    if detail:
        data["metrics"] = metrics_for(app)
        data["env"] = [
            {
                "id": item.id,
                "key": item.key,
                "value": "••••••" if item.is_secret and not reveal_secrets else item.value,
                "is_secret": item.is_secret,
            }
            for item in app.env_vars.all()
        ]
        data["domains"] = [domain_payload(item) for item in app.domains.all()]
        data["redirects"] = [redirect_payload(item) for item in app.redirects.all()]
        data["disks"] = [disk_payload(item) for item in app.disks.all()]
        data["cron"] = [cron_payload(item) for item in app.cron_jobs.all()]
        data["pipeline"] = pipeline_for(app)
    return data


def pipeline_for(app) -> list[dict]:
    dep = app.deployments.order_by("-number").first()
    docker = settings.DOCKRA_DRIVER == "docker"
    status = dep.status if dep else ""
    log = dep.log if dep else ""
    has_source = bool(dep and (dep.tree or dep.source_path or dep.git_url or app.git_url))
    app_up = app.status == App.Status.RUNNING
    deploying = app.status == App.Status.DEPLOYING or status in (
        Deployment.Status.QUEUED,
        Deployment.Status.BUILDING,
    )
    failed = status == Deployment.Status.FAILED or app.status == App.Status.FAILED

    if not dep:
        source_state = "pending"
    elif status == Deployment.Status.QUEUED:
        source_state = "current"
    elif has_source:
        source_state = "done"
    elif status in (Deployment.Status.BUILDING, Deployment.Status.RUNNING, Deployment.Status.FAILED):
        source_state = "skipped"
    else:
        source_state = "pending"

    if not docker:
        build_state = "skipped"
        docker_state = "skipped"
    elif not dep:
        build_state = "pending"
        docker_state = "pending"
    elif status == Deployment.Status.BUILDING:
        build_state = "current"
        docker_state = "pending"
    elif failed:
        built = "Building image" in log
        build_state = "failed" if built or "Preparing a Docker image" in log else "pending"
        docker_state = "failed" if app.current_image or "Release is running" in log else "pending"
    elif app.current_image and app_up:
        build_state = "done"
        docker_state = "done"
    elif app.current_image:
        build_state = "done"
        docker_state = "pending"
    else:
        build_state = "pending"
        docker_state = "pending"

    domains = list(app.domains.all())
    if not domains:
        ssl_state = "pending"
    elif any(item.ssl_status == Domain.Ssl.ERROR for item in domains):
        ssl_state = "failed"
    elif all(item.ssl_status == Domain.Ssl.ACTIVE for item in domains):
        ssl_state = "done"
    elif deploying:
        ssl_state = "current"
    else:
        ssl_state = "pending"

    if app_up:
        run_state = "done"
    elif deploying:
        run_state = "current"
    elif failed:
        run_state = "failed"
    else:
        run_state = "pending"

    return [
        {"key": "source", "label": "Source", "state": source_state},
        {"key": "build", "label": "Build", "state": build_state},
        {"key": "docker", "label": "Docker", "state": docker_state},
        {"key": "ssl", "label": "SSL", "state": ssl_state},
        {"key": "run", "label": "Release", "state": run_state},
    ]


# Two-part suffixes that are not zones themselves; the zone is one label higher (example.co.uk).
_SECOND_LEVEL = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp", "com.br", "com.tr", "co.in", "co.za", "co.ir", "ac.ir",
}


def dns_zone(hostname: str) -> str:
    labels = hostname.split(".")
    size = 3 if ".".join(labels[-2:]) in _SECOND_LEVEL else 2
    return ".".join(labels[-size:])


def dns_records(hostname: str) -> list[dict]:
    """DNS record to create (at your DNS provider) for a custom domain."""
    zone = dns_zone(hostname)
    name = hostname[: -len(zone)].rstrip(".") or "@"
    return [{"type": "A", "name": name, "zone": zone, "fqdn": hostname, "value": settings.PUBLIC_IP, "ttl": 300}]


def domain_payload(domain: Domain) -> dict:
    return {
        "id": domain.id,
        "hostname": domain.hostname,
        "ssl_status": domain.ssl_status,
        "ssl_label": domain.get_ssl_status_display(),
        "is_primary": domain.is_primary,
        "redirect_https": domain.redirect_https,
        "kind": domain.kind,
        "kind_label": domain.get_kind_display(),
        "app": domain.app.slug,
        "app_name": domain.app.name,
        "created_at": _iso(domain.created_at),
        "dns": dns_records(domain.hostname) if domain.kind == Domain.Kind.CUSTOM else [],
    }


def redirect_payload(redirect: Redirect) -> dict:
    return {
        "id": redirect.id,
        "source_host": redirect.source_host,
        "source_path": redirect.source_path,
        "match": redirect.match,
        "match_label": redirect.get_match_display(),
        "target": redirect.target,
        "keep_path": redirect.keep_path,
        "permanent": redirect.permanent,
        "created_at": _iso(redirect.created_at),
    }


def disk_payload(disk: Disk) -> dict:
    return {
        "id": disk.id,
        "name": disk.name,
        "mount_to": disk.mount_to,
        "size_gb": disk.size_gb,
        "backup_hours": disk.backup_hours,
        "backup_keep": disk.backup_keep,
        "backup_default_hours": settings.DOCKRA_DISK_BACKUP_HOURS,
        "backup_default_keep": settings.DOCKRA_DISK_BACKUP_KEEP,
    }


def disk_backup_payload(backup) -> dict:
    return {
        "id": backup.id,
        "disk": backup.disk_name,
        "kind": backup.kind,
        "kind_label": backup.get_kind_display(),
        "status": backup.status,
        "status_label": backup.get_status_display(),
        "size_bytes": backup.size_bytes,
        "error": backup.error,
        "offsite": bool(backup.remote_key),
        "created_at": _iso(backup.created_at),
        "finished_at": _iso(backup.finished_at) if backup.finished_at else None,
    }


def cron_payload(cron: CronJob) -> dict:
    return {
        "id": cron.id,
        "schedule": cron.schedule,
        "command": cron.command,
        "enabled": cron.enabled,
        "last_run_at": _iso(cron.last_run_at),
        "last_status": cron.last_status,
        "last_output": cron.last_output,
    }


def env_payload(item: EnvVar, reveal_secrets: bool) -> dict:
    return {
        "id": item.id,
        "key": item.key,
        "value": "••••••" if item.is_secret and not reveal_secrets else item.value,
        "is_secret": item.is_secret,
    }
