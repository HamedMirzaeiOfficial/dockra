import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from django.conf import settings

from apps_core.models import App, CronJob, Disk
from engine import blobs
from engine.cron import split_cron

MANIFESTS = ("dockra.json", "liara.json")
DEFAULT_PORTS = {"django": 8000, "node": 3000, "nextjs": 3000, "static": 80, "docker": 8000}


def _safe_extract(archive: tarfile.TarFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError("The archive contains a disallowed path")
    # The data filter rejects links pointing outside the folder and device files.
    archive.extractall(dest, filter="data")


def prepare_source(deployment, on_log=None) -> Path | None:
    dest = Path(settings.WORK_ROOT) / str(deployment.id)
    if deployment.tree:
        files = blobs.load_tree(deployment.tree)
        mirror, written = blobs.sync_mirror(deployment.app.slug, files)
        if on_log:
            on_log(f"Source synced: {written} of {len(files)} files changed")
        shutil.rmtree(dest, ignore_errors=True)
        blobs.link_tree(mirror, dest)
        return dest
    if not deployment.source_path and not deployment.git_url:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    if deployment.source_path:
        archive_path = Path(deployment.source_path)
        try:
            with tarfile.open(archive_path, "r:*") as archive:
                _safe_extract(archive, dest)
        finally:
            # The archive is not needed after extraction; rollback uses the image.
            archive_path.unlink(missing_ok=True)
    else:
        cmd = ["git", "clone", "--depth", "1"]
        branch = getattr(deployment.app, "git_branch", "") or ""
        if branch:
            cmd += ["--branch", branch]
        proc = subprocess.run([*cmd, deployment.git_url, str(dest)], capture_output=True, timeout=600)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", "replace")
            raise RuntimeError(err.strip() or "git clone failed")
    children = [p for p in dest.iterdir() if p.name != ".git"]
    has_manifest = any((dest / name).exists() for name in MANIFESTS)
    if len(children) == 1 and children[0].is_dir() and not has_manifest and not (dest / "Dockerfile").exists():
        return children[0]
    return dest


def discard_source(deployment) -> None:
    shutil.rmtree(Path(settings.WORK_ROOT) / str(deployment.id), ignore_errors=True)


def read_manifest(source: Path | None) -> dict:
    if source is None:
        return {}
    for name in MANIFESTS:
        path = source / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _health_path(manifest: dict) -> str | None:
    check = manifest.get("healthCheck")
    if isinstance(check, dict) and "path" in check:
        raw = str(check.get("path") or "")
    elif isinstance(check, str):
        raw = check
    else:
        return None
    if raw and not raw.startswith("/"):
        raw = "/" + raw
    return raw[:200]


def apply_manifest(app: App, manifest: dict) -> None:
    if not manifest:
        return
    platform = manifest.get("platform")
    if platform in dict(App.Platform.choices):
        app.platform = platform
    if manifest.get("port"):
        app.port = int(manifest["port"])
    health = _health_path(manifest)
    if health is not None:
        app.health_path = health
    app.save(update_fields=["port", "platform", "health_path"])
    if "disks" in manifest:
        from apps_core.validation import DiskError, check_disk

        for item in manifest["disks"]:
            name = str(item.get("name") or "").strip()
            size = item.get("sizeGb") or item.get("size_gb") or item.get("size")
            mount = item.get("mountTo") or item.get("mount_to") or "/data"
            disk = app.disks.filter(name=name).first()
            try:
                if disk is None:
                    mount = check_disk(app, name, mount)
                    Disk.objects.create(app=app, name=name, mount_to=mount, size_gb=max(int(size or 1), 1))
                    continue
                if mount != disk.mount_to:
                    disk.mount_to = check_disk(app, name, mount, disk=disk)
                # A disk file never shrinks; a sizeGb smaller than the current size is ignored.
                if size and int(size) > disk.size_gb:
                    disk.size_gb = int(size)
                disk.save(update_fields=["mount_to", "size_gb"])
            except (DiskError, ValueError) as exc:
                raise RuntimeError(f"Disk {name or '?'} in dockra.json/liara.json: {exc}") from exc
    if "cron" in manifest:
        app.cron_jobs.all().delete()
        for line in manifest["cron"]:
            schedule, command = split_cron(line)
            CronJob.objects.create(app=app, schedule=schedule, command=command)


def release_command(manifest: dict) -> str:
    raw = manifest.get("release")
    if isinstance(raw, list):
        raw = " && ".join(str(item) for item in raw if str(item).strip())
    return str(raw or "").strip()


def detect_runtime(source: Path | None) -> str:
    if source is None:
        return ""
    path = source / "Dockerfile"
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.upper().startswith("FROM "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        image = parts[1]
        tail = image.rsplit("/", 1)[-1]
        if ":" not in tail:
            return tail
        name, tag = tail.split(":", 1)
        version = tag.split("-", 1)[0]
        if name in ("python", "node") and version:
            return version
        return f"{name}:{tag}"
    return ""


def _workers(memory_mb: int) -> int:
    return max(1, min(4, int(memory_mb or 512) // 200))


def _section(manifest: dict, name: str) -> dict:
    value = manifest.get(name)
    return value if isinstance(value, dict) else {}


PYTHON_VERSIONS = ("3.6", "3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
# Highest Python each Django release officially supports, for projects without pythonVersion.
# Each row: Django below this version -> this Python. E.g. 2.2 to 3.1 -> 3.9.
DJANGO_PYTHON = (
    ((2, 2), "3.7"), ((3, 2), "3.9"), ((4, 1), "3.10"), ((4, 2), "3.11"),
    ((5, 1), "3.12"), ((6, 0), "3.13"),
)


def _minor(raw: str) -> str:
    """Normalize '3.10.4', 'python-3.9.16' or '3.11' to '3.X'."""
    match = re.search(r"(?<![\d.])(3)\.(\d{1,2})(?:\.\d+)?\b", raw or "")
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def _django_pin(requirements: str) -> tuple[int, int] | None:
    match = re.search(r"^\s*django\s*(?:==|~=)\s*(\d+)\.(\d+)", requirements, re.IGNORECASE | re.MULTILINE)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _python_version(manifest: dict, source: Path | None = None, requirements: str = "") -> str:
    """Order: dockra.json/liara.json, runtime.txt, .python-version, Django version in requirements, then 3.12."""
    django = _section(manifest, "django")
    raw = str(manifest.get("pythonVersion") or django.get("pythonVersion") or "").strip()
    if raw:
        version = _minor(raw)
        if version not in PYTHON_VERSIONS:
            raise RuntimeError(f"pythonVersion \"{raw}\" is not supported; use one of {', '.join(PYTHON_VERSIONS)}")
        return version
    for name in ("runtime.txt", ".python-version"):
        path = source / name if source else None
        if path and path.is_file():
            version = _minor(path.read_text(encoding="utf-8", errors="replace")[:200])
            if version in PYTHON_VERSIONS:
                return version
    pin = _django_pin(requirements)
    if pin:
        for upto, version in DJANGO_PYTHON:
            if pin < upto:
                return version
    return "3.12"


def _python_base(version: str) -> str:
    # 3.6 and 3.7 have no bookworm images.
    return f"python:{version}-slim-{'bullseye' if version in ('3.6', '3.7') else 'bookworm'}"


def _node_version(manifest: dict) -> str:
    raw = str(manifest.get("nodeVersion") or _section(manifest, "node").get("version") or "20")
    return raw if re.fullmatch(r"\d{2}(\.\d{1,2})?", raw) else "20"


def _apt_packages(manifest: dict, requirements: str) -> list[str]:
    names = [str(item).strip().lower() for item in manifest.get("packages") or []]
    if re.search(r"^\s*psycopg2(?!-binary)\b", requirements, re.IGNORECASE | re.MULTILINE):
        names.append("libpq5")
    return sorted({name for name in names if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", name)})


def _mirror(value: str) -> str:
    return value if re.fullmatch(r"https?://[\w.-]+(:\d+)?(/[\w./-]*)?", value or "") else ""


def _write(path: Path, text: str, mode: int | None = None) -> None:
    # Files in the build folder are hardlinks of the mirror; unlink first so the mirror stays intact.
    path.unlink(missing_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        path.chmod(mode)


def uses_own_dockerfile(source: Path, manifest: dict, app: App | None = None) -> bool:
    platform = manifest.get("platform") or (app.platform if app else "")
    return platform == "docker" and (source / "Dockerfile").exists()


def _platform(app: App, manifest: dict) -> str:
    platform = manifest.get("platform") or app.platform or "django"
    return platform if platform in DEFAULT_PORTS else "django"


def ensure_dockerfile(source: Path | None, app: App, manifest: dict) -> None:
    if source is None or uses_own_dockerfile(source, manifest, app):
        return
    platform = _platform(app, manifest)
    if platform == "docker":
        raise RuntimeError("The platform is docker but the project has no Dockerfile")
    ignore = source / ".dockerignore"
    if not ignore.exists():
        _write(
            ignore,
            ".git\n.venv\nvenv\nenv\nnode_modules\n**/__pycache__\n**/*.pyc\n*.log\n.env\ndb.sqlite3\nmedia\nstaticfiles\n",
        )
    if platform in ("node", "nextjs"):
        text = _node_dockerfile(source, app, manifest, platform)
    elif platform == "static":
        text = _static_dockerfile(source, app, manifest)
    else:
        text = _django_dockerfile(source, app, manifest)
    _write(source / "Dockerfile", text)


# Regardless of DEBUG and project middleware, /static/ and /media/ are served from disk; nothing else sits behind gunicorn.
DJANGO_WSGI = """import importlib
import os
import sys
from urllib.parse import urlparse

application = importlib.import_module(os.environ.get("DOCKRA_WSGI") or "config.wsgi").application

try:
    from django.conf import settings
    from whitenoise import WhiteNoise

    def _serve(app, root_name, url_name, **options):
        root = getattr(settings, root_name, None)
        prefix = urlparse(getattr(settings, url_name, None) or "").path
        if not (root and prefix.startswith("/") and prefix != "/" and os.path.isdir(str(root))):
            print(f"dockra: {root_name} not served (missing or empty)", file=sys.stderr)
            return app
        return WhiteNoise(app, root=str(root), prefix=prefix, **options)

    application = _serve(application, "STATIC_ROOT", "STATIC_URL", max_age=3600)
    # Uploads appear after startup; autorefresh finds each request on disk.
    application = _serve(application, "MEDIA_ROOT", "MEDIA_URL", autorefresh=True, max_age=3600)
except Exception as exc:
    print(f"dockra: static files not served: {exc}", file=sys.stderr)
"""



# Last-resort index when the configured mirror fails (missing package, 402, outage). Extra mirrors for
# restricted networks are configured with DOCKRA_PIP_EXTRA_INDEX_URL / DOCKRA_PIP_FALLBACK_MIRROR.
PIP_FALLBACK_MIRRORS = ("https://pypi.org/simple",)


def _django_dockerfile(source: Path, app: App, manifest: dict) -> str:
    port = int(app.port or 8000)
    workers = _workers(app.memory_mb)
    req_path = source / "requirements.txt"
    requirements = req_path.read_text(encoding="utf-8", errors="replace") if req_path.exists() else ""
    version = _python_version(manifest, source, requirements)
    base = _python_base(version)
    django = _section(manifest, "django")
    release = []
    # On by default: staticfiles are not in the image (.dockerignore) and styles would disappear after each deployment.
    collect = django.get("collectStatic")
    if collect is None:
        release.append("python manage.py collectstatic --noinput || echo 'dockra: collectstatic failed' >&2")
    elif collect:
        release.append("python manage.py collectstatic --noinput")
    if django.get("compileMessages"):
        release.append("python manage.py compilemessages")
    start = manifest.get("start") or (
        "mod=$(find . -path './.git' -prune -o -path './.venv' -prune -o -name wsgi.py -print | head -1 "
        "| sed 's#^./##;s#/#.#g;s#.py$##'); "
        'export DOCKRA_WSGI="${mod:-config.wsgi}"; '
        # WEB_CONCURRENCY in the app env overrides the worker count; heavy apps stay under the memory cap with fewer workers.
        f"exec gunicorn --bind 0.0.0.0:{port} --workers ${{WEB_CONCURRENCY:-{workers}}} --timeout 60 --graceful-timeout 30 "
        "--access-logfile - --error-logfile - dockra_wsgi:application"
    )
    _write(source / "dockra_wsgi.py", DJANGO_WSGI)
    _write(source / "dockra-start.sh", "#!/bin/sh\nset -eu\n" + "".join(f"{cmd}\n" for cmd in release) + start + "\n", 0o755)
    # Keep the project's own gunicorn pin (e.g. gunicorn==20.1.0); two conflicting constraints would break pip.
    gunicorn = "" if re.search(r"^\s*gunicorn\b", requirements, re.IGNORECASE | re.MULTILINE) else (
        "'gunicorn>=20.1,<22'" if version == "3.6" else "'gunicorn>=22,<24'"
    )
    # The platform serves STATIC_ROOT itself, so the project does not need WhiteNoise.
    whitenoise = "" if re.search(r"^\s*whitenoise\b", requirements, re.IGNORECASE | re.MULTILINE) else "'whitenoise>=5,<7'"
    deps = " ".join(item for item in ("-r requirements.txt" if req_path.exists() else "", gunicorn, whitenoise) if item)
    copy_req = "COPY requirements.txt .\n" if req_path.exists() else ""
    packages = _apt_packages(manifest, requirements)
    apt = (
        "apt-get update && apt-get install -y --no-install-recommends "
        + " ".join(packages)
        + " && rm -rf /var/lib/apt/lists/* && "
        if packages
        else ""
    )
    apt_mirror = _mirror(getattr(settings, "DOCKRA_APT_MIRROR", ""))
    sources = (
        f"RUN sed -i 's#http://deb.debian.org#{apt_mirror}#g' /etc/apt/sources.list.d/debian.sources\n"
        if apt_mirror
        else ""
    )
    pip_mirror = _mirror(getattr(settings, "DOCKRA_PIP_INDEX_URL", ""))
    # Secondary index: if the primary mirror lacks a package or starts charging, pip falls back to it.
    pip_extra = _mirror(getattr(settings, "DOCKRA_PIP_EXTRA_INDEX_URL", ""))
    index = f" PIP_INDEX_URL={pip_mirror}" if pip_mirror else ""
    if pip_extra:
        index += f" PIP_EXTRA_INDEX_URL={pip_extra}"
    # When the primary mirror fails (missing package or 402), these sources are tried in order:
    # the configured extra index, the configured fallback mirror, and finally pypi.org.
    fallbacks: list[str] = []
    for url in (pip_extra, _mirror(getattr(settings, "DOCKRA_PIP_FALLBACK_MIRROR", "")), *PIP_FALLBACK_MIRRORS):
        if url and url not in fallbacks:
            fallbacks.append(url)
    # The pip cache survives between builds; more retries and a longer timeout tolerate slow networks.
    opts = "--no-compile --retries 5 --timeout 60"
    install = f"/venv/bin/pip install {opts} {deps}"
    chain = f"{install} \\\n"
    if pip_mirror:
        for url in fallbacks:
            chain += f" || PIP_INDEX_URL={url} {install} \\\n"
    last = f"PIP_INDEX_URL={fallbacks[0]} " if pip_mirror else ""
    pip = "RUN --mount=type=cache,target=/root/.cache/pip"
    return (
        f"FROM {base} AS deps\n"
        f"ENV PIP_DISABLE_PIP_VERSION_CHECK=1{index}\n"
        f"{sources}"
        "RUN python -m venv /venv\n"
        f"{copy_req}"
        f"{pip} {chain}"
        " || (apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \\\n"
        f"     && {last}{install})\n"
        "\n"
        f"FROM {base}\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 \\\n"
        "    PYTHONUNBUFFERED=1 \\\n"
        "    VIRTUAL_ENV=/venv \\\n"
        "    PATH=/venv/bin:$PATH\n"
        f"{sources}"
        f"RUN {apt}useradd --uid 1000 --create-home --shell /usr/sbin/nologin dockra \\\n"
        " && mkdir /app && chown dockra:dockra /app\n"
        "WORKDIR /app\n"
        "COPY --from=deps /venv /venv\n"
        "COPY --chown=1000:1000 . .\n"
        "USER dockra\n"
        f"EXPOSE {port}\n"
        "HEALTHCHECK --interval=30s --timeout=3s --start-period=25s --retries=3 \\\n"
        f'  CMD python -c "import socket; socket.create_connection((\'127.0.0.1\', {port}), 3).close()"\n'
        'CMD ["./dockra-start.sh"]\n'
    )


def _node_dockerfile(source: Path, app: App, manifest: dict, platform: str) -> str:
    port = int(app.port or 3000)
    version = _node_version(manifest)
    registry = _mirror(getattr(settings, "DOCKRA_NPM_REGISTRY", ""))
    env_registry = f" NPM_CONFIG_REGISTRY={registry}" if registry else ""
    lock = (source / "package-lock.json").exists()
    install = "npm ci" if lock else "npm install"
    node = _section(manifest, "node")
    build = manifest.get("build") if isinstance(manifest.get("build"), str) else node.get("build")
    if platform == "nextjs" and not build:
        build = "npm run build"
    start = manifest.get("start") or node.get("start") or (
        f"npx next start -H 0.0.0.0 -p {port}" if platform == "nextjs" else "npm start"
    )
    _write(source / "dockra-start.sh", "#!/bin/sh\nset -eu\nexec " + start + "\n", 0o755)
    build_line = f"RUN --mount=type=cache,target=/root/.npm {build}\n" if build else ""
    return (
        f"FROM node:{version}-alpine\n"
        f"ENV NODE_ENV=production PORT={port} NEXT_TELEMETRY_DISABLED=1{env_registry}\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        f"RUN --mount=type=cache,target=/root/.npm NODE_ENV=development {install}\n"
        "COPY . .\n"
        f"{build_line}"
        "RUN chown -R node:node /app\n"
        "USER node\n"
        f"EXPOSE {port}\n"
        "HEALTHCHECK --interval=30s --timeout=3s --start-period=25s --retries=3 \\\n"
        f"  CMD node -e \"require('net').connect({port}, '127.0.0.1').on('connect', () => process.exit(0)).on('error', () => process.exit(1))\"\n"
        'CMD ["./dockra-start.sh"]\n'
    )


def _static_dockerfile(source: Path, app: App, manifest: dict) -> str:
    port = int(app.port or 80)
    static = _section(manifest, "static")
    folder = str(static.get("root") or manifest.get("root") or ".").strip("/") or "."
    if not re.fullmatch(r"[\w./-]+", folder) or ".." in folder.split("/"):
        folder = "."
    spa = bool(static.get("spa"))
    fallback = "/index.html" if spa else "=404"
    _write(
        source / "dockra-nginx.conf",
        "server {\n"
        f"  listen {port};\n"
        "  root /usr/share/nginx/html;\n"
        "  index index.html index.htm;\n"
        f"  location / {{ try_files $uri $uri/ {fallback}; }}\n"
        "  gzip on;\n"
        "  gzip_types text/css application/javascript application/json image/svg+xml;\n"
        "}\n",
    )
    return (
        "FROM nginx:alpine\n"
        "COPY dockra-nginx.conf /etc/nginx/conf.d/default.conf\n"
        f"COPY {folder}/ /usr/share/nginx/html/\n"
        "RUN rm -f /usr/share/nginx/html/Dockerfile /usr/share/nginx/html/dockra-nginx.conf\n"
        f"EXPOSE {port}\n"
        "HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\\n"
        f"  CMD wget -q --spider http://127.0.0.1:{port}/ || exit 1\n"
    )
