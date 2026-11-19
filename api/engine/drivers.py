import gzip
import http.client
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings
from config.i18n import tr


# If host memory runs out (possible with overcommit), the kernel kills apps (0) first, then databases; platform services are -900.
DB_OOM_SCORE_ADJ = -500
# cgroup weight per guaranteed core. Roughly cpu.weight=100, the same weight as the Docker daemon (builds) and system services.
CPU_SHARES_PER_CORE = 2560


def cpu_burst(cpu) -> float:
    from engine.resources import host_cpu_total

    cpu = float(cpu)
    return round(min(max(cpu * settings.DOCKRA_CPU_BURST, cpu), max(host_cpu_total(), cpu)), 2)


def cpu_flags(cpu) -> list[str]:
    """An app's cores are a guaranteed share: under contention CPU is split by weight, and when the host is idle
    the app may use up to DOCKRA_CPU_BURST times its cores from idle capacity."""
    cpu = float(cpu)
    burst = cpu_burst(cpu)
    shares = min(262144, max(2, round(cpu * CPU_SHARES_PER_CORE)))
    return ["--cpus", f"{burst:.2f}", "--cpu-shares", str(shares)]


def container_flags(cpu, memory_mb) -> list[str]:
    mem = max(int(memory_mb), 32)
    pids = min(4096, max(64, mem // 4))
    return [
        *cpu_flags(cpu),
        "--memory", f"{mem}m",
        "--memory-swap", f"{mem}m",
        "--pids-limit", str(pids),
    ]


def _run(
    args: list[str],
    check: bool = True,
    input_bytes: bytes | None = None,
    timeout: float | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError(tr("docker command not found in PATH")) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(tr("The command took longer than {value} seconds and was stopped", value=int(timeout or 0))) from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"docker exited with code {proc.returncode}")
    return proc


class BuildCanceled(RuntimeError):
    """The build was stopped because the user canceled it."""


def _close_thread_db() -> None:
    # The watcher thread's database connection is thread-local; close it after every check so it does not leak.
    from django.db import connection

    connection.close()


def _stream(
    args: list[str], on_line=None, check: bool = True, timeout: float | None = None, env=None, should_cancel=None
) -> None:
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    except FileNotFoundError as exc:
        raise RuntimeError(tr("docker command not found in PATH")) from exc
    timer = None
    expired = threading.Event()
    canceled = threading.Event()
    if timeout:
        def kill():
            expired.set()
            proc.kill()

        timer = threading.Timer(timeout, kill)
        timer.daemon = True
        timer.start()
    watcher = None
    if should_cancel is not None:
        # Check for a cancel request every few seconds and kill the process if canceled.
        def watch():
            while not canceled.is_set():
                if proc.poll() is not None:
                    return
                try:
                    if should_cancel():
                        canceled.set()
                        proc.kill()
                        return
                except Exception:
                    pass
                finally:
                    _close_thread_db()
                time.sleep(2)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
    collected: list[str] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            collected.append(line)
            if len(collected) > 200:
                del collected[:100]
            if on_line and line:
                on_line(line)
        code = proc.wait()
    finally:
        if timer:
            timer.cancel()
        canceled_now = canceled.is_set()
        canceled.set()
        if watcher:
            watcher.join(timeout=1)
    if canceled_now:
        raise BuildCanceled("Build canceled")
    if expired.is_set():
        raise RuntimeError(tr("The build took longer than {value} seconds and was stopped", value=int(timeout)))
    if check and code != 0:
        err = "\n".join(line for line in collected[-40:] if line).strip()
        raise RuntimeError(err or f"docker exited with code {code}")


def _docker_env() -> dict:
    env = dict(os.environ)
    env["DOCKER_BUILDKIT"] = "1"
    return env


# Built-in Docker Hub mirrors; empty by default. Operators behind a blocked or rate-limited Docker Hub set
# DOCKRA_DOCKER_MIRRORS: base images are pulled from those mirrors and re-tagged with their original name.
DOCKER_HUB_MIRRORS: tuple[str, ...] = ()
HUB_HOSTS = ("docker.io", "index.docker.io", "registry-1.docker.io")


def _hub_path(image: str) -> str | None:
    """'python:3.11' -> 'library/python:3.11'; an image from another registry -> None."""
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        if first not in HUB_HOSTS:
            return None
        image = rest
    return image if "/" in image else f"library/{image}"


def base_images(dockerfile: str) -> list[str]:
    """FROM images of a Dockerfile, excluding scratch, stage names and images built from ARGs."""
    stages: set[str] = set()
    images: list[str] = []
    for raw in dockerfile.splitlines():
        parts = raw.split()
        if not parts or parts[0].upper() != "FROM":
            continue
        args = [part for part in parts[1:] if not part.startswith("--")]
        if not args:
            continue
        image = args[0]
        skip = "$" in image or image.lower() == "scratch" or image.lower() in stages
        if len(args) >= 3 and args[1].upper() == "AS":
            stages.add(args[2].lower())
        if not skip and image not in images:
            images.append(image)
    return images


def _docker_mirrors() -> list[str]:
    mirrors: list[str] = []
    for url in [*settings.DOCKRA_DOCKER_MIRRORS.split(), *DOCKER_HUB_MIRRORS]:
        host = url.split("://", 1)[-1].strip("/")
        if host and host not in mirrors:
            mirrors.append(host)
    return mirrors


def _try(args: list[str], timeout: float) -> bool:
    try:
        return _run(args, check=False, timeout=timeout).returncode == 0
    except RuntimeError:
        return False


def ensure_image(image: str, on_log=None) -> None:
    """If the image is not on the host: a regular pull first (using daemon mirrors), then the configured Docker Hub mirrors.
    Raises nothing if all fail, so docker build/run reports the original error itself."""
    if _try(["docker", "image", "inspect", image], 30):
        return
    if _try(["docker", "pull", "-q", image], 300):
        return
    path = _hub_path(image)
    if path is None or "@" in image:
        return
    for mirror in _docker_mirrors():
        ref = f"{mirror}/{path}"
        if on_log:
            on_log(f"Pulling {image} from {mirror}")
        if _try(["docker", "pull", "-q", ref], 600) and _try(["docker", "tag", ref, image], 30):
            _run(["docker", "rmi", ref], check=False)
            return
    if on_log:
        on_log(f"Could not pull {image} from Docker Hub or mirrors ({', '.join(_docker_mirrors())})")


def ensure_base_images(source_dir, on_log=None) -> None:
    path = Path(source_dir) / "Dockerfile"
    if not path.exists():
        return
    for image in base_images(path.read_text(encoding="utf-8", errors="replace")):
        ensure_image(image, on_log)


def _opener(path: Path):
    with path.open("rb") as handle:
        magic = handle.read(2)
    return gzip.open if magic == b"\x1f\x8b" else open


def _plain_copy(src: Path) -> Path:
    """If the file is gzip, write the decompressed copy next to it."""
    if _opener(src) is open:
        return src
    out = src.with_name(src.name + ".raw")
    with gzip.open(src, "rb") as reader, out.open("wb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
    return out


DB_SPECS = {
    "postgres": {"port": 5432, "data": "/var/lib/postgresql/data", "image": "postgres", "env": "DATABASE_URL"},
    "mysql": {"port": 3306, "data": "/var/lib/mysql", "image": "mysql", "env": "DATABASE_URL"},
    "mariadb": {"port": 3306, "data": "/var/lib/mysql", "image": "mariadb", "env": "DATABASE_URL"},
    "mongodb": {"port": 27017, "data": "/data/db", "image": "mongo", "env": "MONGODB_URI"},
    "redis": {"port": 6379, "data": "/data", "image": "redis", "env": "REDIS_URL"},
}
# Highest RDB file version each Redis release loads; a newer file sends the container into a crash loop.
RDB_MAX = (((7, 0), 10), ((7, 2), 11), ((7, 4), 12))
RDB_MIN_REDIS = {10: "7.0", 11: "7.2", 12: "7.4"}


def _bind(source: str, target: str) -> list[str]:
    """With -v Docker creates an empty folder if the loop disk is not mounted yet and the database initializes from scratch on it.
    --mount fails in that case instead, so the container never starts on the wrong data."""
    return ["--mount", f'type=bind,"source={source}","target={target}"']


def _version_tuple(version: str) -> tuple[int, int] | None:
    """'16', '7.2-alpine' or '10.11.6' -> (16, 0), (7, 2), (10, 11). latest and unknown tags -> None."""
    match = re.match(r"^(\d+)(?:\.(\d+))?", version or "")
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def db_data_path(database) -> str:
    # From postgres 18 the official image keeps data in /var/lib/postgresql/<version>/docker and rejects the old mount.
    # A non-numeric tag (latest) keeps the old path so existing databases are not moved.
    if database.engine == "postgres":
        number = _version_tuple(database.version)
        if number and number >= (18, 0):
            return "/var/lib/postgresql"
    return DB_SPECS[database.engine]["data"]


def _rdb_version(path: Path) -> int | None:
    with _opener(path)(path, "rb") as handle:
        head = handle.read(9)
    if head[:5] != b"REDIS" or not head[5:9].isdigit():
        return None
    return int(head[5:9])


def _rdb_supported(version: str) -> int | None:
    number = _version_tuple(version)
    if number is None:
        return None
    # Redis 5 and 6 read up to RDB 9.
    return max([rdb for since, rdb in RDB_MAX if number >= since], default=9)


# Statements from a source dump (e.g. another provider) that refer to the source's own database, roles or GTIDs and
# either fail on the target or write into a different database. Equivalent of --no-owner --no-privileges for plain SQL.
PG_SQL_DROP = re.compile(
    rb"^(\\connect\b|\\c |CREATE DATABASE |DROP DATABASE |ALTER DATABASE |CREATE ROLE |ALTER ROLE |DROP ROLE |"
    rb"COMMENT ON DATABASE |GRANT |REVOKE |ALTER DEFAULT PRIVILEGES |ALTER [A-Z ]+ .* OWNER TO [^ ;]+;\s*$)"
)
MYSQL_SQL_DROP = re.compile(rb"^(CREATE DATABASE |USE `|SET @@GLOBAL\.GTID_PURGED)")

BACKUP_SUFFIX = {"postgres": "dump", "mysql": "sql", "mariadb": "sql", "mongodb": "archive.gz", "redis": "rdb"}


def db_user(database) -> str:
    user = database.name.replace("-", "_")
    return user[:32] if database.engine in ("mysql", "mariadb") else user


class FakeDriver:
    def build_and_run(self, app, deployment, source_dir, on_log=None, release: str = "", should_cancel=None) -> str:
        if on_log:
            on_log(f"image dockra/{app.slug}:v{deployment.number}")
            if release:
                on_log(f"(fake) release: {release}")
        return f"dockra/{app.slug}:v{deployment.number}"

    def restart(self, app) -> None:
        return None

    def stop(self, app) -> None:
        return None

    def start(self, app) -> None:
        return None

    def remove_app(self, app) -> None:
        return None

    def container_logs(self, app, tail: int = 400) -> str:
        return ""

    def stream_logs(self, app, tail: int = 200, max_seconds: int = 3600):
        yield f"(fake) logs for {app.slug}\n"

    def exec(self, app, command: str, timeout: int | None = None) -> tuple[str, int]:
        return f"(fake) {command.splitlines()[0] if command else ''}\n", 0

    def provision_database(self, database, password: str | None = None) -> dict:
        if database.engine not in DB_SPECS:
            raise RuntimeError(tr("Unsupported database engine"))
        spec = DB_SPECS[database.engine]
        user = "default" if database.engine == "redis" else db_user(database)
        return {
            "host": f"{database.name}.internal",
            "port": spec["port"],
            "username": user,
            "password": password or secrets.token_urlsafe(18),
            "db_name": "0" if database.engine == "redis" else user,
            "container_name": f"dockra-db-{database.name}",
        }

    def backup_database(self, database, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"fake backup {database.name}\n", encoding="utf-8")
        return dest.stat().st_size

    def restore_database(self, database, src: Path) -> str:
        if not src.exists():
            raise RuntimeError(tr("Backup file not found"))
        return "(fake) restored"

    def exec_cron(self, app, command: str) -> str:
        return f"(fake) {command}"

    def apply_resources(self, app) -> None:
        return None

    def sync(self, app) -> None:
        return None

    def usage(self, app):
        return None

    def usage_all(self) -> dict:
        return {}

    def database_usage(self, database, rows: dict | None = None):
        return None

    def database_logs(self, database, tail: int = 400) -> str:
        return f"(fake) logs for {database.name}\n"

    def restart_counts(self) -> dict:
        return {}

    def prune_images(self, app, keep: int = 3) -> None:
        return None

    def remove_database(self, database) -> None:
        return None

    def stop_database(self, database) -> None:
        return None

    def start_database(self, database) -> None:
        return None

    def start_db_admin(self, database, base: str) -> None:
        return None

    def stop_db_admin(self, database) -> None:
        return None

    def db_summary(self, database) -> dict:
        return {}

    def remove_disk(self, app, disk) -> None:
        return None

    def heal_volumes(self, log=None) -> None:
        return None

    def sync_resources(self, log=None) -> None:
        return None


class DockerDriver:
    def __init__(self):
        self.network = settings.DOCKRA_NETWORK

    def _ensure_network(self) -> None:
        proc = _run(["docker", "network", "inspect", self.network], check=False)
        if proc.returncode != 0:
            _run(["docker", "network", "create", self.network])

    # Network isolation: each app has its own network. Traefik and the current worker/builder join it,
    # and each database joins only the networks of apps linked to it. Apps cannot reach each other, other databases,
    # the platform database or the platform services (which live on the dockra network).

    @staticmethod
    def app_network(app) -> str:
        return f"dockra-app-{app.slug}"

    def _connect(self, network: str, container: str, alias: str = "") -> None:
        cmd = ["docker", "network", "connect"]
        if alias:
            cmd += ["--alias", alias]
        proc = _run([*cmd, network, container], check=False)
        err = ((proc.stderr or b"") + (proc.stdout or b"")).decode("utf-8", "replace")
        if proc.returncode != 0 and "already exists" not in err:
            raise RuntimeError(err.strip() or f"Connecting {container} to {network} failed")

    def _self_container(self) -> dict:
        """This worker's or builder's own container (the Docker hostname is the container id)."""
        proc = _run(
            ["docker", "inspect", "-f", '{{.Id}}|{{index .Config.Labels "com.docker.compose.project"}}', socket.gethostname()],
            check=False,
        )
        if proc.returncode != 0:
            return {}
        cid, _, project = proc.stdout.decode().strip().partition("|")
        return {"id": cid, "project": project} if cid else {}

    def _traefik_containers(self, project: str) -> list[str]:
        cmd = ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=traefik"]
        if project:
            cmd += ["--filter", f"label=com.docker.compose.project={project}"]
        proc = _run(cmd, check=False)
        return [line.strip() for line in proc.stdout.decode().splitlines() if line.strip()]

    def reconnect_routes(self) -> None:
        """A recreated Traefik (e.g. after a compose change while updating the platform) is not on the app networks
        and every site times out; missing networks are reconnected."""
        wanted = set()
        for label in ("dockra.app", "dockra.dbadmin"):
            proc = _run(["docker", "network", "ls", "--filter", f"label={label}", "--format", "{{.Name}}"], check=False)
            wanted.update(line.strip() for line in proc.stdout.decode().splitlines() if line.strip())
        if not wanted:
            return
        for traefik in self._traefik_containers(self._self_container().get("project", "")):
            proc = _run(["docker", "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", traefik], check=False)
            have = set(proc.stdout.decode().split())
            for network in sorted(wanted - have):
                self._connect(network, traefik)

    def _ensure_app_network(self, app) -> str:
        from databases.models import Database

        network = self.app_network(app)
        if _run(["docker", "network", "inspect", network], check=False).returncode != 0:
            _run(["docker", "network", "create", "--label", f"dockra.app={app.slug}", network])
        me = self._self_container()
        for traefik in self._traefik_containers(me.get("project", "")):
            self._connect(network, traefik)
        if me:
            # Needed to health-check the new release; the worker and builder do not listen on any port.
            self._connect(network, me["id"])
        linked = Database.objects.filter(links__app=app, status=Database.Status.RUNNING).exclude(container_name="")
        for database in linked:
            try:
                self._connect(network, database.container_name, alias=database.container_name)
            except RuntimeError:
                # A database whose container is missing does not stop the deployment; starting the database reconnects it.
                continue
        return network

    def connect_database(self, database) -> None:
        """Connect the database to the networks of all linked apps (after create or recreate)."""
        for link in database.links.select_related("app"):
            network = self.app_network(link.app)
            if _run(["docker", "network", "inspect", network], check=False).returncode == 0:
                self._connect(network, database.container_name, alias=database.container_name)
        from engine.dbadmin import container_name as admin_name

        # An open admin UI (pgAdmin, ...) must reach the database again after its container is recreated.
        network = admin_name(database)
        if _run(["docker", "network", "inspect", network], check=False).returncode == 0:
            self._connect(network, database.container_name, alias=database.container_name)

    def start_db_admin(self, database, base: str) -> None:
        """Start the admin tool container on its own network (only Traefik and this database)."""
        from engine import dbadmin

        name = dbadmin.container_name(database)
        proc = _run(["docker", "inspect", "-f", '{{.State.Running}}|{{index .Config.Labels "dockra.dbadmin.base"}}', name], check=False)
        if proc.returncode == 0 and proc.stdout.decode().strip() == f"true|{base}":
            self._connect(name, database.container_name, alias=database.container_name)
            return
        _run(["docker", "rm", "-f", name], check=False)
        if _run(["docker", "network", "inspect", name], check=False).returncode != 0:
            _run(["docker", "network", "create", "--label", f"dockra.dbadmin={database.name}", name])
        for traefik in self._traefik_containers(self._self_container().get("project", "")):
            self._connect(name, traefik)
        self._connect(name, database.container_name, alias=database.container_name)
        tool = dbadmin.spec(database, base)
        cmd = [
            "docker", "run", "-d", "--name", name, "--network", name,
            "--restart", "unless-stopped", *container_flags(tool["cpu"], tool["memory_mb"]),
            "--log-opt", "max-size=5m", "--log-opt", "max-file=1",
            "--label", "dockra.role=dbadmin", "--label", f"dockra.dbadmin.base={base}",
        ]
        for label in dbadmin.labels(database, base, tool):
            cmd += ["--label", label]
        for key, value in tool["env"].items():
            cmd += ["-e", f"{key}={value}"]
        if tool.get("entrypoint"):
            cmd += ["--entrypoint", tool["entrypoint"][0]]
        # The first time the image is pulled; from the daemon mirror, configured mirrors or offline.sh.
        ensure_image(tool["image"])
        _run([*cmd, tool["image"], *tool.get("args", [])], timeout=900)
        for _ in range(180):
            state = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False).stdout.decode().strip()
            if state != "true":
                logs = _run(["docker", "logs", "--tail", "20", name], check=False)
                text = ((logs.stdout or b"") + (logs.stderr or b"")).decode("utf-8", "replace").strip()
                raise RuntimeError(tr("{value} did not start: {value2}", value=dbadmin.tool_name(database), value2=text[-600:]))
            net = _run(["docker", "exec", name, "cat", "/proc/net/tcp", "/proc/net/tcp6"], check=False)
            if dbadmin.listening((net.stdout or b"").decode(), tool["port"]):
                return
            time.sleep(1)
        raise RuntimeError(tr("{value} was not ready in time", value=dbadmin.tool_name(database)))

    def stop_db_admin(self, database) -> None:
        from engine.dbadmin import container_name as admin_name

        name = admin_name(database)
        _run(["docker", "rm", "-f", name], check=False)
        proc = _run(["docker", "network", "inspect", "-f", "{{range .Containers}}{{.Name}} {{end}}", name], check=False)
        if proc.returncode != 0:
            return
        for container in proc.stdout.decode().split():
            _run(["docker", "network", "disconnect", "-f", name, container], check=False)
        _run(["docker", "network", "rm", name], check=False)

    def _remove_app_network(self, app) -> None:
        network = self.app_network(app)
        proc = _run(["docker", "network", "inspect", "-f", "{{range .Containers}}{{.Name}} {{end}}", network], check=False)
        if proc.returncode != 0:
            return
        for container in proc.stdout.decode().split():
            _run(["docker", "network", "disconnect", "-f", network, container], check=False)
        _run(["docker", "network", "rm", network], check=False)

    def _labels(self, app, release: str) -> list[str]:
        from engine.redirects import redirect_labels

        domains = list(app.domains.all())
        labels = [
            "traefik.enable=true",
            f"traefik.docker.network={self.app_network(app)}",
            "dockra.role=app",
            f"dockra.app={app.slug}",
            f"dockra.release={release}",
        ]
        if not domains:
            return labels
        slug = app.slug

        def rule(items):
            return " || ".join(f"Host(`{item.hostname}`)" for item in items)

        plain = [item for item in domains if not item.redirect_https]
        forced = [item for item in domains if item.redirect_https]
        if plain:
            labels += [
                f"traefik.http.routers.{slug}.rule={rule(plain)}",
                f"traefik.http.routers.{slug}.entrypoints=web",
                f"traefik.http.routers.{slug}.service={slug}",
            ]
        if forced:
            labels += [
                f"traefik.http.middlewares.{slug}-https.redirectscheme.scheme=https",
                f"traefik.http.middlewares.{slug}-https.redirectscheme.permanent=true",
                f"traefik.http.routers.{slug}-r.rule={rule(forced)}",
                f"traefik.http.routers.{slug}-r.entrypoints=web",
                f"traefik.http.routers.{slug}-r.middlewares={slug}-https",
                f"traefik.http.routers.{slug}-r.service={slug}",
            ]
        labels += [
            f"traefik.http.routers.{slug}-s.rule={rule(domains)}",
            f"traefik.http.routers.{slug}-s.entrypoints=websecure",
            f"traefik.http.routers.{slug}-s.tls=true",
            f"traefik.http.routers.{slug}-s.service={slug}",
            f"traefik.http.services.{slug}.loadbalancer.server.port={app.port}",
        ]
        if settings.DOCKRA_TLS_RESOLVER:
            # Without a resolver (isolated network), Traefik picks the matching certificate from the certs folder.
            labels.append(f"traefik.http.routers.{slug}-s.tls.certresolver={settings.DOCKRA_TLS_RESOLVER}")
        return labels + redirect_labels(app, domains)

    def _env_args(self, app) -> tuple[list[str], Path | None]:
        """Variables are passed with --env-file so they never show up in the host's process list."""
        lines, args = [], []
        for item in app.env_vars.all():
            if "\n" in item.value or "\r" in item.value:
                args += ["-e", f"{item.key}={item.value}"]
            else:
                lines.append(f"{item.key}={item.value}")
        if not lines:
            return args, None
        from engine.volumes import data_root

        folder = data_root() / "tmp"
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="env-", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.chmod(name, 0o600)
        return ["--env-file", name, *args], Path(name)

    def _containers(self, app, running_only: bool = False, release: str | None = None) -> list[str]:
        cmd = ["docker", "ps", "-q" if running_only else "-aq", "--filter", f"label=dockra.app={app.slug}"]
        if release:
            cmd += ["--filter", f"label=dockra.release={release}"]
        proc = _run(cmd, check=False)
        return [line.strip() for line in proc.stdout.decode().splitlines() if line.strip()]

    def _release_of(self, cid: str) -> str:
        proc = _run(["docker", "inspect", "-f", '{{index .Config.Labels "dockra.release"}}', cid], check=False)
        return proc.stdout.decode().strip()

    def _remove_existing(self, app, keep_release: str | None = None) -> None:
        ids = self._containers(app)
        releases = {cid: self._release_of(cid) for cid in ids}
        stale = [cid for cid in ids if not keep_release or releases[cid] != keep_release]
        if not stale:
            return
        # A short grace period to finish in-flight requests.
        _run(["docker", "stop", "-t", "10", *stale], check=False)
        from engine.logs import archive_container

        for cid in stale:
            archive_container(app.slug, cid, releases[cid])
        _run(["docker", "rm", "-f", *stale], check=False)

    def build_and_run(self, app, deployment, source_dir, on_log=None, release: str = "", should_cancel=None) -> str:
        if source_dir is None and not deployment.image and not app.current_image:
            raise RuntimeError(tr("No build source (archive or git) exists"))
        self._ensure_network()
        image = deployment.image or f"dockra/{app.slug}:v{deployment.number}"
        if source_dir is not None:
            ensure_base_images(source_dir, on_log)
            _stream(
                ["docker", "build", "--progress=plain", "-t", image, str(source_dir)],
                on_log,
                timeout=settings.DOCKRA_BUILD_TIMEOUT,
                env=_docker_env(),
                should_cancel=should_cancel,
            )
        elif app.current_image and not deployment.image:
            image = app.current_image
        from engine.locks import app_lock

        with app_lock(app.id):
            if release:
                self._release(app, image, release, on_log)
            self._launch(app, image, on_log)
        from engine.tls import sync_certificates

        sync_certificates(list(app.domains.all()), force=True)
        return image

    def _release(self, app, image: str, command: str, on_log=None) -> None:
        """Like migrate: once, with the same env and disks, before any traffic reaches the new release."""
        from engine.volumes import ensure_volume

        if on_log:
            on_log(f"Release command: {command}")
        env_args, env_file = self._env_args(app)
        volumes = []
        for disk in app.disks.all():
            volumes += _bind(ensure_volume(f"dockra-disk-{app.slug}-{disk.name}", disk.size_gb), disk.mount_to)
        name = f"dockra-release-{app.slug}-{secrets.token_hex(3)}"
        try:
            _stream(
                ["docker", "run", "--rm", "--name", name, "--network", self._ensure_app_network(app),
                 "--security-opt", "no-new-privileges",
                 *container_flags(app.cpu, app.memory_mb), *env_args, *volumes,
                 "--entrypoint", "sh", image, "-c", command],
                on_log,
                timeout=settings.DOCKRA_RELEASE_TIMEOUT,
            )
        except RuntimeError as exc:
            _run(["docker", "rm", "-f", name], check=False)
            raise RuntimeError(tr("The release command failed; the current release was not touched\n{exc}", exc=exc)) from exc
        finally:
            if env_file:
                env_file.unlink(missing_ok=True)

    def _launch(self, app, image: str, on_log=None) -> None:
        """The new release starts next to the current one, and the old one is removed only after the new one is healthy."""
        from engine.volumes import ensure_volume

        release = secrets.token_hex(4)
        count = max(app.instances, 1)
        network = self._ensure_app_network(app)
        env_args, env_file = self._env_args(app)
        started: list[str] = []
        try:
            volumes = []
            for disk in app.disks.all():
                host_path = ensure_volume(f"dockra-disk-{app.slug}-{disk.name}", disk.size_gb)
                volumes += _bind(host_path, disk.mount_to)
            for index in range(1, count + 1):
                name = f"dockra-app-{app.slug}-{release}-{index}"
                cmd = [
                    "docker", "run", "-d", "--name", name, "--network", network,
                    "--restart", "unless-stopped", *container_flags(app.cpu, app.memory_mb),
                    "--log-opt", "max-size=20m", "--log-opt", "max-file=3",
                    # App processes cannot gain privileges through setuid binaries or sudo.
                    "--security-opt", "no-new-privileges",
                ]
                for label in self._labels(app, release):
                    cmd.extend(["--label", label])
                cmd += [*env_args, *volumes, image]
                _run(cmd)
                started.append(name)
            for name in started:
                self._wait_ready(name, app, on_log)
        except Exception:
            if started:
                _run(["docker", "rm", "-f", *started], check=False)
            raise
        finally:
            if env_file:
                env_file.unlink(missing_ok=True)
        self._remove_existing(app, keep_release=release)
        if on_log:
            on_log(f"Release {release} is healthy; previous containers removed")

    def _probe(self, ip: str, app) -> str:
        """ok, refused (network reachable but not listening yet) or unreachable."""
        port = int(app.port)
        try:
            if app.health_path:
                host = next(iter(app.domains.values_list("hostname", flat=True)), ip)
                conn = http.client.HTTPConnection(ip, port, timeout=3)
                conn.request("GET", app.health_path, headers={"Host": host, "User-Agent": "dockra-health"})
                status = conn.getresponse().status
                conn.close()
                return "ok" if status < 500 else "refused"
            socket.create_connection((ip, port), timeout=2).close()
            return "ok"
        except ConnectionRefusedError:
            return "refused"
        except (http.client.HTTPException, ConnectionResetError):
            return "refused"
        except OSError:
            return "unreachable"

    def _wait_ready(self, name: str, app, on_log=None) -> None:
        timeout = settings.DOCKRA_HEALTH_TIMEOUT
        deadline = time.monotonic() + timeout
        reachable = False
        fmt = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
        status = ""
        while time.monotonic() < deadline:
            proc = _run(["docker", "inspect", "-f", fmt, name], check=False)
            status, health, ips = (proc.stdout.decode().strip().split("|") + ["", "", ""])[:3]
            if status in {"exited", "dead"} or health == "unhealthy":
                logs = _run(["docker", "logs", "--tail", "80", name], check=False)
                body = ((logs.stdout or b"") + (logs.stderr or b"")).decode("utf-8", "replace").strip()
                raise RuntimeError(body or f"Container {name} did not start")
            if status == "running":
                if health == "healthy":
                    return
                ip = ips.split()[0] if ips.split() else ""
                if ip:
                    result = self._probe(ip, app)
                    if result == "ok":
                        return
                    reachable = reachable or result == "refused"
            time.sleep(1)
        if status == "running" and not reachable:
            # The worker cannot reach the container network; running is good enough.
            if on_log:
                on_log(f"{name}: health probe unreachable from worker, accepting running container")
            return
        logs = _run(["docker", "logs", "--tail", "60", name], check=False)
        body = ((logs.stdout or b"") + (logs.stderr or b"")).decode("utf-8", "replace").strip()
        target = app.health_path or f"port {app.port}"
        raise RuntimeError(f"Container {name} did not answer on {target} within {timeout} seconds\n{body}".strip())

    def restart(self, app) -> None:
        if app.current_image:
            self._relaunch(app)
            return
        ids = self._containers(app)
        if not ids:
            raise RuntimeError(tr("There is no container to restart"))
        _run(["docker", "restart", *ids])

    def stop(self, app) -> None:
        ids = self._containers(app)
        if ids:
            _run(["docker", "stop", *ids])

    def _relaunch(self, app) -> None:
        if not app.current_image:
            raise RuntimeError(tr("There is no image to run; deploy once first"))
        self._ensure_network()
        from engine.locks import app_lock

        with app_lock(app.id):
            self._launch(app, app.current_image)
        from engine.tls import sync_certificates

        sync_certificates(list(app.domains.all()), force=True)

    def start(self, app) -> None:
        ids = self._containers(app)
        desired = max(app.instances, 1)
        if not ids or len(ids) != desired:
            if not app.current_image and not ids:
                raise RuntimeError(tr("There is no container to start; deploy once first"))
            if app.current_image:
                self._relaunch(app)
                return
        _run(["docker", "start", *ids])
        for cid in self._containers(app):
            _run(["docker", "update", *container_flags(app.cpu, app.memory_mb), cid])

    def apply_resources(self, app) -> None:
        ids = self._containers(app)
        desired = max(app.instances, 1)
        if not ids:
            return
        if len(ids) != desired:
            self._relaunch(app)
            return
        for cid in ids:
            _run(["docker", "update", *container_flags(app.cpu, app.memory_mb), cid])

    def remove_app(self, app) -> None:
        from engine.volumes import release_volume

        ids = self._containers(app)
        if ids:
            _run(["docker", "rm", "-f", *ids])
        self._remove_app_network(app)
        from engine.logs import remove_app

        remove_app(app.slug)
        for disk in list(app.disks.all()):
            release_volume(f"dockra-disk-{app.slug}-{disk.name}")

    def sync(self, app) -> None:
        if app.status != "running" or not app.current_image:
            return
        self._relaunch(app)

    def restart_counts(self) -> dict:
        """Sum of RestartCount over each app's containers, to detect crash loops."""
        proc = _run(["docker", "ps", "-aq", "--filter", "label=dockra.role=app"], check=False)
        ids = proc.stdout.decode().split()
        if not ids:
            return {}
        info = _run(["docker", "inspect", "-f", '{{index .Config.Labels "dockra.app"}} {{.RestartCount}}', *ids], check=False)
        totals: dict[str, int] = {}
        for line in info.stdout.decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                totals[parts[0]] = totals.get(parts[0], 0) + int(parts[1])
        return totals

    def usage_all(self) -> dict:
        """Stats for every app and database from a single docker stats call; each call takes about two seconds.
        App keys are slugs and database keys are db:<container name>."""
        owner = {}
        for args, prefix in (
            (["--filter", "label=dockra.role=app", "--format", '{{.ID}}|{{.Label "dockra.app"}}'], ""),
            (["--filter", "name=^dockra-db-", "--format", "{{.ID}}|{{.Names}}"], "db:"),
        ):
            proc = _run(["docker", "ps", "--no-trunc", *args], check=False)
            for line in proc.stdout.decode().splitlines():
                if "|" in line:
                    cid, key = line.split("|", 1)
                    owner[cid.strip()[:12]] = prefix + key.strip()
        if not owner:
            return {}
        for gone in set(_CPU_SEEN) - set(owner):
            _CPU_SEEN.pop(gone, None)
        stats = _run(
            ["docker", "stats", "--no-stream", "--format", "{{.ID}}|{{.CPUPerc}}|{{.MemPerc}}|{{.NetIO}}", *owner],
            check=False,
        )
        rows: dict[str, dict] = {}
        for line in stats.stdout.decode().splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            slug = owner.get(parts[0].strip()[:12])
            if not slug:
                continue
            row = rows.setdefault(slug, {"cpus": [], "rams": [], "sent": 0.0})
            row["cpus"].append(_cpu_average(parts[0].strip()[:12], _percent(parts[1])))
            row["rams"].append(_percent(parts[2]))
            row["sent"] += _net_sent(parts[3])
        return rows

    def usage(self, app, rows: dict | None = None):
        rows = self.usage_all() if rows is None else rows
        return _usage_row(rows.get(app.slug), app.cpu, app.slug)

    def database_usage(self, database, rows: dict | None = None):
        rows = self.usage_all() if rows is None else rows
        key = f"db:{database.container_name or f'dockra-db-{database.name}'}"
        return _usage_row(rows.get(key), database.cpu or 0.5, key)

    def prune_images(self, app, keep: int = 3) -> None:
        proc = _run(["docker", "images", f"dockra/{app.slug}", "--format", "{{.Repository}}:{{.Tag}}"], check=False)
        tags = [line.strip() for line in proc.stdout.decode().splitlines() if ":v" in line]

        def number(tag: str) -> int:
            tail = tag.rsplit(":v", 1)[-1]
            return int(tail) if tail.isdigit() else 0

        tags.sort(key=number, reverse=True)
        stale = [tag for tag in tags[keep:] if tag != app.current_image]
        if stale:
            _run(["docker", "rmi", *stale], check=False)
        _run(["docker", "image", "prune", "-f"], check=False)
        # The BuildKit cache (including pip and npm caches) is kept; on isolated networks it is the package source.
        _run(["docker", "builder", "prune", "-f", "--filter", f"until={settings.DOCKRA_BUILD_CACHE_HOURS}h"], check=False)

    def python_version(self, app) -> str:
        ids = self._containers(app, running_only=True)
        if not ids:
            return ""
        for cmd in (["python", "--version"], ["node", "--version"]):
            proc = _run(["docker", "exec", ids[0], *cmd], check=False)
            text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
            parts = text.split()
            if len(parts) >= 2 and parts[0].lower() == "python":
                return parts[1]
            if proc.returncode == 0 and text.startswith("v"):
                return text[1:]
        return ""

    def stop_database(self, database) -> None:
        _run(["docker", "stop", database.container_name or f"dockra-db-{database.name}"], check=False)

    def start_database(self, database) -> None:
        """Start the existing container; if missing (e.g. a new host), recreate it with the same password and data."""
        name = database.container_name or f"dockra-db-{database.name}"
        if _run(["docker", "inspect", name], check=False).returncode == 0:
            _run(["docker", "start", name])
            self._wait_db(database.engine, name, database.username, database.password, database.db_name)
            self.connect_database(database)
            return
        self.provision_database(database, password=database.password)

    def _sees_volume(self, container: str, target: str) -> bool | None:
        """Does the container see the loop disk at its target? None means not checked (e.g. the container is stopped)."""
        proc = _run(["docker", "exec", container, "cat", "/proc/self/mountinfo"], check=False)
        if proc.returncode != 0:
            return None
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            fields = line.split()
            if len(fields) > 4 and fields[4] == target:
                # Volumes are the data folder on the disk filesystem; if the container started before the mount,
                # the root is the full path of that folder on the host disk.
                return fields[3] == "/data"
        return None

    def heal_volumes(self, log=None) -> None:
        """A container that started before its disk was mounted (e.g. after a host or Docker restart) sees empty data;
        now that remount_all has mounted the disk, a restart brings it back on the real data."""
        from apps_core.models import Disk
        from databases.models import Database

        checks = [
            (database.container_name, db_data_path(database), f"database {database.name}")
            for database in Database.objects.filter(status=Database.Status.RUNNING).exclude(container_name="")
        ]
        for disk in Disk.objects.select_related("app"):
            for cid in self._containers(disk.app, running_only=True):
                checks.append((cid, disk.mount_to, f"disk {disk.name} of app {disk.app.slug}"))
        restarted = set()
        for container, target, label in checks:
            if container in restarted or self._sees_volume(container, target) is not False:
                continue
            if log:
                log(f"{label} was not on the real disk; restarting container {container}")
            _run(["docker", "restart", container], check=False)
            restarted.add(container)

    def sync_resources(self, log=None) -> None:
        """Containers created before shared CPU get the new weight and burst limit without a restart,
        and older databases get a lower OOM priority so that if host memory runs out an app is killed before a database."""
        from apps_core.models import App
        from databases.models import Database

        for app in App.objects.filter(status=App.Status.RUNNING):
            for cid in self._containers(app, running_only=True):
                _run(["docker", "update", *cpu_flags(app.cpu), cid], check=False)
        pids = []
        for database in Database.objects.filter(status=Database.Status.RUNNING).exclude(container_name=""):
            name = database.container_name
            _run(["docker", "update", *cpu_flags(database.cpu or 0.5), name], check=False)
            info = _run(["docker", "inspect", "-f", "{{.State.Pid}} {{.HostConfig.OomScoreAdj}}", name], check=False)
            parts = info.stdout.decode().split()
            if info.returncode == 0 and len(parts) == 2 and parts[0] != "0" and parts[1] != str(DB_OOM_SCORE_ADJ):
                pids.append(parts[0])
        if not pids:
            return
        from engine.volumes import _host_script

        # Every process in the container's cgroup; processes forked later inherit the value from their parent.
        try:
            _host_script(
                "for pid in " + " ".join(pids) + "; do\n"
                '  cg=$(cut -d: -f3 /proc/$pid/cgroup | head -1)\n'
                '  for p in $(cat /sys/fs/cgroup$cg/cgroup.procs 2>/dev/null); do\n'
                f'    echo {DB_OOM_SCORE_ADJ} > /proc/$p/oom_score_adj 2>/dev/null || true\n'
                "  done\n"
                "done\n"
            )
        except RuntimeError as exc:
            if log:
                log(f"database oom_score_adj: {exc}")

    def remove_disk(self, app, disk) -> None:
        from engine.volumes import release_volume

        release_volume(f"dockra-disk-{app.slug}-{disk.name}")

    def remove_database(self, database) -> None:
        from engine.volumes import release_volume

        name = database.container_name or f"dockra-db-{database.name}"
        _run(["docker", "rm", "-f", name], check=False)
        release_volume(f"dockra-dbvol-{database.name}")

    def database_logs(self, database, tail: int = 400) -> str:
        name = database.container_name or f"dockra-db-{database.name}"
        proc = _run(["docker", "logs", "--timestamps", "--tail", str(tail), name], check=False)
        text = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
        # Databases write to both stdout and stderr; sort by timestamp to keep the real order.
        return "\n".join(sorted(line for line in text.splitlines() if line))

    def container_logs(self, app, tail: int = 400) -> str:
        ids = self._containers(app)
        chunks = []
        for cid in ids:
            proc = _run(["docker", "logs", "--timestamps", "--tail", str(tail), cid], check=False)
            chunks.append((proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace"))
        if len(chunks) <= 1:
            return chunks[0] if chunks else ""
        # Lines start with a timestamp; sorting interleaves the logs of all instances.
        return "\n".join(sorted(line for chunk in chunks for line in chunk.splitlines() if line))

    def stream_logs(self, app, tail: int = 200, max_seconds: int = 3600):
        """Live logs of all instances in one stream; processes are killed when the connection closes."""
        ids = self._containers(app, running_only=True)
        if not ids:
            yield "No running container\n"
            return
        lines: queue.Queue = queue.Queue(maxsize=5000)
        procs = []
        prefix = len(ids) > 1

        def pump(proc, label):
            for raw in proc.stdout:
                text = raw.decode("utf-8", "replace")
                lines.put(f"[{label}] {text}" if prefix else text)
            lines.put(None)

        for index, cid in enumerate(ids, 1):
            proc = subprocess.Popen(
                ["docker", "logs", "-f", "--timestamps", "--tail", str(tail), cid],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            procs.append(proc)
            threading.Thread(target=pump, args=(proc, index), daemon=True).start()
        done = 0
        deadline = time.monotonic() + max_seconds
        try:
            while done < len(procs) and time.monotonic() < deadline:
                try:
                    item = lines.get(timeout=15)
                except queue.Empty:
                    # An empty line keeps the connection alive and reveals when the user has disconnected.
                    yield "\n"
                    continue
                if item is None:
                    done += 1
                    continue
                yield item
        finally:
            for proc in procs:
                proc.kill()

    def exec(self, app, command: str, timeout: int | None = None) -> tuple[str, int]:
        ids = self._containers(app, running_only=True)
        if not ids:
            raise RuntimeError(tr("The app container is not running"))
        done = _run(
            ["docker", "exec", ids[0], "sh", "-c", command],
            check=False,
            timeout=timeout or settings.DOCKRA_EXEC_TIMEOUT,
        )
        out = ((done.stdout or b"") + (done.stderr or b"")).decode("utf-8", "replace")
        return out, done.returncode

    def provision_database(self, database, password: str | None = None) -> dict:
        """Create (or, with an existing password, recreate) the database container. Data stays on the disk."""
        self._ensure_network()
        if database.engine not in DB_SPECS:
            raise RuntimeError(tr("Unsupported database engine"))
        spec = DB_SPECS[database.engine]
        name = f"dockra-db-{database.name}"
        password = password or secrets.token_urlsafe(18)
        user = db_user(database)
        db_name = user
        from engine.volumes import ensure_volume

        _run(["docker", "rm", "-f", name], check=False)
        host_path = ensure_volume(f"dockra-dbvol-{database.name}", database.disk_gb)
        mem = int(database.memory_mb)
        base = [
            "docker", "run", "-d", "--name", name, "--network", self.network,
            "--restart", "unless-stopped", *container_flags(database.cpu or 0.5, mem),
            "--oom-score-adj", str(DB_OOM_SCORE_ADJ),
            "--log-opt", "max-size=20m", "--log-opt", "max-file=3",
            *_bind(host_path, db_data_path(database)),
        ]
        image = f"{spec['image']}:{database.version}"
        ensure_image(image)
        engine = database.engine
        if engine == "postgres":
            shared = max(32, int(mem * 0.25))
            connections = min(100, max(20, mem // 16))
            cmd = [
                *base, "--shm-size", f"{mem}m",
                "-e", f"POSTGRES_USER={user}", "-e", f"POSTGRES_PASSWORD={password}", "-e", f"POSTGRES_DB={db_name}",
                image, "-c", f"shared_buffers={shared}MB", "-c", f"max_connections={connections}",
            ]
        elif engine in ("mysql", "mariadb"):
            pool = max(64, int(mem * 0.5))
            connections = min(200, max(30, mem // 8))
            cmd = [
                *base,
                "-e", f"MYSQL_ROOT_PASSWORD={password}", "-e", f"MYSQL_DATABASE={db_name}",
                "-e", f"MYSQL_USER={user}", "-e", f"MYSQL_PASSWORD={password}",
                image, f"--innodb-buffer-pool-size={pool}M", f"--max-connections={connections}",
                "--performance-schema=OFF", "--character-set-server=utf8mb4",
            ]
        elif engine == "mongodb":
            cache = max(0.25, round(mem * 0.4 / 1024, 2))
            cmd = [
                *base,
                "-e", f"MONGO_INITDB_ROOT_USERNAME={user}", "-e", f"MONGO_INITDB_ROOT_PASSWORD={password}",
                "-e", f"MONGO_INITDB_DATABASE={db_name}",
                image, "--wiredTigerCacheSizeGB", str(cache),
            ]
        else:
            user = "default"
            db_name = "0"
            maxmemory = max(16, int(mem * 0.8))
            cmd = [
                *base, image,
                "redis-server", "--requirepass", password,
                "--maxmemory", f"{maxmemory}mb", "--maxmemory-policy", "allkeys-lru",
            ]
        _run(cmd)
        self._wait_db(engine, name, user, password, db_name)
        database.container_name = name
        self.connect_database(database)
        return {
            "host": name,
            "port": spec["port"],
            "username": user,
            "password": password,
            "db_name": db_name,
            "container_name": name,
        }

    def _db_ping(self, engine: str, name: str, user: str, password: str, db_name: str) -> bool:
        if engine == "postgres":
            proc = _run(["docker", "exec", name, "pg_isready", "-U", user, "-h", "127.0.0.1"], check=False)
            return proc.returncode == 0
        if engine in ("mysql", "mariadb"):
            # During init a temporary server runs without networking; a TCP connection means the real server is ready.
            client = "mysql" if engine == "mysql" else "mariadb"
            proc = _run(
                ["docker", "exec", "-e", f"MYSQL_PWD={password}", name, client, "-h127.0.0.1", f"-u{user}", "-e", "SELECT 1", db_name],
                check=False,
            )
            return proc.returncode == 0
        if engine == "mongodb":
            script = (
                # mongo 4.4 and 5 only ship the legacy mongo shell.
                f'"$(command -v mongosh || echo mongo)" --quiet --host "$(hostname -i | cut -d" " -f1)" -u "{user}" -p "$P" '
                "--authenticationDatabase admin --eval 'db.adminCommand(\"ping\").ok'"
            )
            proc = _run(["docker", "exec", "-e", f"P={password}", name, "sh", "-c", script], check=False)
            return proc.returncode == 0 and b"1" in (proc.stdout or b"")
        proc = _run(["docker", "exec", "-e", f"REDISCLI_AUTH={password}", name, "redis-cli", "ping"], check=False)
        return b"PONG" in (proc.stdout or b"")

    def _wait_db(self, engine, name, user, password, db_name) -> None:
        for _ in range(120):
            if self._db_ping(engine, name, user, password, db_name):
                return
            time.sleep(1)
        raise RuntimeError(tr("The database did not start in time"))

    def _pipe_out(self, cmd: list[str], dest: Path) -> None:
        with dest.open("wb") as handle:
            proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE, timeout=3600)
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "Backup failed")

    def _pipe_in(self, cmd: list[str], src: Path, tolerate: str = "") -> str:
        with _opener(src)(src, "rb") as handle:
            proc = subprocess.run(cmd, stdin=handle, capture_output=True, timeout=7200)
        out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
        if proc.returncode != 0 and not (tolerate and tolerate in out):
            raise RuntimeError(out[-3000:] or "Restore failed")
        return out[-3000:]

    def _pipe_filtered(self, cmd: list[str], src: Path, drop) -> str:
        """Feed SQL to the client line by line, skipping drop statements (up to their closing ;)."""
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT)
            skipping = False
            try:
                with _opener(src)(src, "rb") as handle:
                    for line in handle:
                        if skipping or drop.match(line):
                            stripped = line.rstrip()
                            skipping = not (stripped.endswith(b";") or stripped.startswith(b"\\"))
                            continue
                        proc.stdin.write(line)
                proc.stdin.close()
            except BrokenPipeError:
                pass
            except BaseException:
                proc.kill()
                raise
            try:
                code = proc.wait(timeout=7200)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                raise RuntimeError(tr("The restore took longer than two hours and was stopped")) from exc
            out.seek(0, 2)
            out.seek(max(0, out.tell() - 200_000))
            text = out.read().decode("utf-8", "replace").strip()
        if code != 0:
            raise RuntimeError(text[-3000:] or "Restore failed")
        return text

    def _pg_restore_parallel(self, database, src: Path) -> str:
        """Parallel pg_restore (-j) only works from a file, not stdin; a temporary file is copied into the container."""
        name = database.container_name
        plain = _plain_copy(src)
        inside = f"/tmp/dockra-restore-{secrets.token_hex(6)}.dump"
        workers = max(1, min(8, int(float(database.cpu or 0.5) * 2 + 0.5)))
        try:
            _run(["docker", "cp", str(plain), f"{name}:{inside}"], timeout=3600)
            proc = _run(
                ["docker", "exec", name, "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                 "-j", str(workers), "-U", database.username, "-d", database.db_name, inside],
                check=False,
                timeout=7200,
            )
        finally:
            _run(["docker", "exec", "-u", "0", name, "rm", "-f", inside], check=False)
            if plain != src:
                plain.unlink(missing_ok=True)
        out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
        if proc.returncode != 0 and "errors ignored on restore" not in out:
            raise RuntimeError(out[-3000:] or "Restore failed")
        return out[-200_000:]

    def db_summary(self, database) -> dict:
        """Approximate table and row counts after an import, to compare with the source."""
        name, engine = database.container_name, database.engine
        if engine == "postgres":
            sql = (
                "ANALYZE; SELECT count(*), coalesce(sum(greatest(c.reltuples, 0)), 0)::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'"
            )
            cmd = ["docker", "exec", name, "psql", "-At", "-U", database.username, "-d", database.db_name, "-c", sql]
            unit = "tables"
        elif engine in ("mysql", "mariadb"):
            client = "mysql" if engine == "mysql" else "mariadb"
            sql = (
                "SELECT count(*), coalesce(sum(table_rows), 0) FROM information_schema.tables "
                f"WHERE table_schema = '{database.db_name}' AND table_type = 'BASE TABLE'"
            )
            cmd = ["docker", "exec", "-e", f"MYSQL_PWD={database.password}", name, client, "-uroot", "-N", "-B", "-e", sql]
            unit = "tables"
        elif engine == "mongodb":
            script = (
                f'const d = db.getSiblingDB("{database.db_name}"); let n = 0; const cs = d.getCollectionNames(); '
                'cs.forEach(c => { n += d.getCollection(c).estimatedDocumentCount(); }); print(cs.length + " " + n)'
            )
            cmd = [
                "docker", "exec", "-e", f"P={database.password}", name, "sh", "-c",
                f'"$(command -v mongosh || echo mongo)" --quiet -u "{database.username}" -p "$P" '
                f"--authenticationDatabase admin --eval '{script}'",
            ]
            unit = "collection"
        else:
            cmd = ["docker", "exec", "-e", f"REDISCLI_AUTH={database.password}", name, "redis-cli", "DBSIZE"]
            unit = "keys"
        proc = _run(cmd, check=False, timeout=900)
        lines = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        numbers = re.findall(r"\d+", lines[-1]) if lines else []
        if proc.returncode != 0 or not numbers:
            return {}
        if engine == "redis":
            return {"tables": 0, "rows": int(numbers[0]), "unit": unit}
        return {"tables": int(numbers[0]), "rows": int(numbers[1]) if len(numbers) > 1 else 0, "unit": unit}

    def backup_database(self, database, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        name = database.container_name
        engine = database.engine
        password = database.password
        if engine == "postgres":
            self._pipe_out(["docker", "exec", name, "pg_dump", "-Fc", "-U", database.username, database.db_name], dest)
        elif engine in ("mysql", "mariadb"):
            dump = "mysqldump" if engine == "mysql" else "mariadb-dump"
            self._pipe_out(
                ["docker", "exec", "-e", f"MYSQL_PWD={password}", name, dump, "-uroot",
                 "--single-transaction", "--routines", "--triggers", database.db_name],
                dest,
            )
        elif engine == "mongodb":
            self._pipe_out(
                ["docker", "exec", name, "mongodump", "--archive", "--gzip", "-u", database.username, "-p", password,
                 "--authenticationDatabase", "admin", "--db", database.db_name],
                dest,
            )
        elif engine == "redis":
            _run(["docker", "exec", "-e", f"REDISCLI_AUTH={password}", name, "redis-cli", "SAVE"])
            _run(["docker", "cp", f"{name}:/data/dump.rdb", str(dest)])
        else:
            raise RuntimeError(tr("Unsupported database engine"))
        return dest.stat().st_size

    def restore_database(self, database, src: Path) -> str:
        """Restore a backup from this panel or an export from another provider; current data is replaced."""
        name = database.container_name
        engine = database.engine
        password = database.password
        if engine == "postgres":
            with _opener(src)(src, "rb") as handle:
                custom = handle.read(5) == b"PGDMP"
            if custom:
                try:
                    return self._pg_restore_parallel(database, src)
                except RuntimeError as exc:
                    # An older pg_restore cannot read a newer pg_dump file; reject before touching any data.
                    if "unsupported version" in str(exc):
                        raise RuntimeError(
                            tr("This backup was taken with a PostgreSQL newer than {version}; create a database with the same or a higher version than the source and restore there", version=database.version)
                        ) from exc
                    raise
            _run(["docker", "exec", name, "psql", "-U", database.username, "-d", database.db_name, "-c",
                  "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"])
            return self._pipe_filtered(
                ["docker", "exec", "-i", name, "psql", "-q", "-U", database.username, "-d", database.db_name], src, PG_SQL_DROP
            )
        if engine in ("mysql", "mariadb"):
            client = "mysql" if engine == "mysql" else "mariadb"
            # --force: one incompatible statement does not leave the import half-done; errors are counted and reported.
            return self._pipe_filtered(
                ["docker", "exec", "-i", "-e", f"MYSQL_PWD={password}", name, client, "-uroot", "--force",
                 "--max-allowed-packet=1G", database.db_name],
                src,
                MYSQL_SQL_DROP,
            )
        if engine == "mongodb":
            with src.open("rb") as handle:
                zipped = handle.read(2) == b"\x1f\x8b"
            cmd = ["docker", "exec", "-i", name, "mongorestore", "--archive", "--drop", "-u", database.username,
                   "-p", password, "--authenticationDatabase", "admin"]
            if zipped:
                cmd.append("--gzip")
            from engine.dumps import head, mongo_databases

            sources = mongo_databases(head(src))
            if len(sources) == 1 and sources[0] != database.db_name:
                # Data is stored under the source database name; move it into this service's database.
                cmd += [f"--nsFrom={sources[0]}.*", f"--nsTo={database.db_name}.*"]
            with src.open("rb") as handle:
                proc = subprocess.run(cmd, stdin=handle, capture_output=True, timeout=7200)
            out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
            if proc.returncode != 0:
                raise RuntimeError(out[-3000:] or "Restore failed")
            return out[-3000:]
        if engine == "redis":
            rdb, limit = _rdb_version(src), _rdb_supported(database.version)
            if rdb and limit and rdb > limit:
                need = RDB_MIN_REDIS.get(rdb, "a newer release")
                raise RuntimeError(
                    tr("This RDB file (version {rdb}) needs Redis {need} or later and Redis {version} cannot load it; create a Redis database with version {need} or later", rdb=rdb, need=need, version=database.version)
                )
            plain = _plain_copy(src)
            try:
                _run(["docker", "stop", name])
                _run(["docker", "cp", str(plain), f"{name}:/data/dump.rdb"])
                _run(["docker", "start", name])
            finally:
                if plain != src:
                    plain.unlink(missing_ok=True)
            self._wait_db(engine, name, "default", password, "0")
            return "dump.rdb restored"
        raise RuntimeError(tr("Unsupported database engine"))

    def exec_cron(self, app, command: str) -> str:
        out, code = self.exec(app, command, timeout=settings.DOCKRA_CRON_TIMEOUT)
        if code != 0:
            raise RuntimeError(out.strip() or "cron job failed")
        return out.strip()


class _DockerSocket(http.client.HTTPConnection):
    def __init__(self, path: str = "/var/run/docker.sock", timeout: float = 5):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def _usage_row(row, cpu, key: str) -> dict:
    if not row or not row["cpus"]:
        return {"cpu": 0, "ram": 0, "network_mbps": 0.0}
    # Relative to the burst limit: 100% means it actually hit its ceiling, not that it used more than its guaranteed share.
    limit = max(cpu_burst(cpu) * 100, 1)
    used = min(100, round((sum(row["cpus"]) / len(row["cpus"])) / limit * 100))
    ram = min(100, round(sum(row["rams"]) / len(row["rams"])))
    return {"cpu": used, "ram": ram, "network_mbps": _net_mbps(key, row["sent"])}


def _cpu_total_ns(cid: str) -> int | None:
    """Cumulative container CPU counter (nanoseconds) from the Docker API."""
    conn = _DockerSocket()
    try:
        conn.request("GET", f"/containers/{cid}/stats?stream=false&one-shot=true")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        return int(json.loads(resp.read())["cpu_stats"]["cpu_usage"]["total_usage"])
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return None
    finally:
        conn.close()


# Last counters in the worker process; docker stats is a ~2 second snapshot and reported a per-minute cron spike as 100%.
# Alerts and stats call this back to back; the baseline only moves after CPU_WINDOW, so close calls get the same average.
_CPU_SEEN: dict[str, tuple[int, float, float | None]] = {}
CPU_WINDOW = 30


def _cpu_average(cid: str, instant: float) -> float:
    """Average CPU percent (like Docker's CPUPerc: 100 = one core) since the previous sample."""
    now = time.monotonic()
    previous = _CPU_SEEN.get(cid)
    if previous is not None and now - previous[1] < CPU_WINDOW:
        return instant if previous[2] is None else previous[2]
    total = _cpu_total_ns(cid)
    if total is None:
        return instant
    average = None
    if previous is not None and total >= previous[0]:
        average = (total - previous[0]) / ((now - previous[1]) * 1e9) * 100
    _CPU_SEEN[cid] = (total, now, average)
    return instant if average is None else average


def _percent(text: str) -> float:
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return 0.0


_BYTES = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
}
_net_samples: dict[str, tuple[float, float]] = {}


def _size_bytes(text: str) -> float:
    raw = text.strip().lower().replace(" ", "")
    number = ""
    unit = ""
    for char in raw:
        if char.isdigit() or char == ".":
            number += char
        else:
            unit += char
    try:
        value = float(number or 0)
    except ValueError:
        return 0.0
    return value * _BYTES.get(unit, 1)


def _net_sent(net_io: str) -> float:
    parts = [part.strip() for part in net_io.split("/")]
    if len(parts) < 2:
        return 0.0
    return _size_bytes(parts[1])


def _net_mbps(slug: str, sent_bytes: float) -> float:
    now = time.monotonic()
    previous = _net_samples.get(slug)
    _net_samples[slug] = (now, sent_bytes)
    if previous is None:
        return 0.0
    elapsed = now - previous[0]
    if elapsed <= 0:
        return 0.0
    delta = sent_bytes - previous[1]
    if delta < 0:
        return 0.0
    return round(delta * 8 / elapsed / 1_000_000, 2)


def get_driver():
    if settings.DOCKRA_DRIVER == "docker":
        return DockerDriver()
    return FakeDriver()
