"""Web admin UIs for databases (pgAdmin, phpMyAdmin, Mongo Express, Redis Commander).

On request, each database gets its own container on a dedicated network `dockra-dbadmin-<name>`
that is attached only to that database and Traefik. Traefik serves it under `/dbadmin/<name>/` on the API domain
and checks every request with the API through forwardAuth (a signed cookie minted from a short-lived panel ticket).
The tool signs in automatically with the database's own credentials. It is closed after DOCKRA_DBADMIN_IDLE_MINUTES of inactivity.
"""

import json
import secrets
import time
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from config.i18n import tr

TICKET_SALT = "dockra-dbadmin-ticket"
SESSION_SALT = "dockra-dbadmin-session"
TICKET_SECONDS = 60
SESSION_SECONDS = 12 * 3600

TOOLS = {
    "postgres": "pgAdmin",
    "mysql": "phpMyAdmin",
    "mariadb": "phpMyAdmin",
    "mongodb": "Mongo Express",
    "redis": "Redis Commander",
}


def tool_name(database) -> str:
    return TOOLS.get(database.engine, "")


def container_name(database) -> str:
    return f"dockra-dbadmin-{database.name}"


def prefix(database) -> str:
    return f"/dbadmin/{database.name}"


def cookie_name(database_id: int) -> str:
    return f"dockra_dbadmin_{database_id}"


def public_base(request) -> str:
    """Base URL on which Traefik serves /dbadmin (the API host)."""
    if settings.DOCKRA_DBADMIN_URL:
        return settings.DOCKRA_DBADMIN_URL
    scheme = "https" if request.is_secure() else "http"
    host = request.get_host()
    _, _, port = host.partition(":")
    # The raw API port (8000) does not go through Traefik; use the API domain behind Traefik.
    if port == "8000" and settings.API_DOMAIN:
        host = settings.API_DOMAIN
    return f"{scheme}://{host}"


def make_ticket(user, database, version: int) -> str:
    return signing.dumps({"u": user.id, "d": database.id, "sv": version, "n": secrets.token_hex(6)}, salt=TICKET_SALT)


def read_ticket(raw: str) -> dict | None:
    try:
        return signing.loads(raw or "", salt=TICKET_SALT, max_age=TICKET_SECONDS)
    except signing.BadSignature:
        return None


def make_session(data: dict) -> str:
    return signing.dumps({"u": data["u"], "d": data["d"], "sv": data["sv"]}, salt=SESSION_SALT)


def read_session(raw: str) -> dict | None:
    try:
        return signing.loads(raw or "", salt=SESSION_SALT, max_age=SESSION_SECONDS)
    except signing.BadSignature:
        return None


def open_url(base: str, ticket: str) -> str:
    return f"{base}/api/dbadmin/open?t={quote(ticket)}"


def _pgpass_field(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(":", "\\:")


def spec(database, base: str) -> dict:
    """Image, port, env and resources of the tool container. strip means Traefik removes the path prefix."""
    path = prefix(database)
    host, port = database.host or database.container_name, database.port
    user, password = database.username, database.password or ""
    if database.engine == "postgres":
        servers = {
            "Servers": {
                "1": {
                    "Name": database.name,
                    "Group": "Dockra",
                    "Host": host,
                    "Port": port,
                    "MaintenanceDB": database.db_name or "postgres",
                    "Username": user,
                    "SSLMode": "prefer",
                    "PassFile": "/var/lib/pgadmin/pgpass",
                }
            }
        }
        script = (
            'printf "%s" "$DOCKRA_SERVERS" > /var/lib/pgadmin/servers.json'
            ' && printf "%s\\n" "$DOCKRA_PGPASS" > /var/lib/pgadmin/pgpass'
            " && chmod 600 /var/lib/pgadmin/pgpass && exec /entrypoint.sh"
        )
        return {
            "image": settings.DOCKRA_PGADMIN_IMAGE,
            "port": 80,
            "strip": False,
            "cpu": 0.5,
            "memory_mb": 512,
            "entrypoint": ["sh"],
            "args": ["-c", script],
            "env": {
                "DOCKRA_SERVERS": json.dumps(servers),
                "DOCKRA_PGPASS": f"{_pgpass_field(host)}:{port}:*:{_pgpass_field(user)}:{_pgpass_field(password)}",
                "PGPASSFILE": "/var/lib/pgadmin/pgpass",
                "PGADMIN_SERVER_JSON_FILE": "/var/lib/pgadmin/servers.json",
                # Desktop mode: no pgAdmin login screen; access is controlled by the panel's forwardAuth.
                "PGADMIN_CONFIG_SERVER_MODE": "False",
                "PGADMIN_CONFIG_MASTER_PASSWORD_REQUIRED": "False",
                "PGADMIN_CONFIG_UPGRADE_CHECK_ENABLED": "False",
                "PGADMIN_DEFAULT_EMAIL": "admin@example.com",
                "PGADMIN_DEFAULT_PASSWORD": secrets.token_urlsafe(18),
                "PGADMIN_DISABLE_POSTFIX": "true",
                "SCRIPT_NAME": path,
            },
        }
    if database.engine in ("mysql", "mariadb"):
        return {
            "image": settings.DOCKRA_PHPMYADMIN_IMAGE,
            "port": 80,
            "strip": True,
            "cpu": 0.5,
            "memory_mb": 256,
            "env": {
                "PMA_HOST": host,
                "PMA_PORT": str(port),
                "PMA_USER": user,
                "PMA_PASSWORD": password,
                "PMA_ABSOLUTE_URI": f"{base}{path}/",
                "UPLOAD_LIMIT": "512M",
                "HIDE_PHP_VERSION": "true",
            },
        }
    if database.engine == "mongodb":
        url = f"mongodb://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/?authSource=admin"
        return {
            "image": settings.DOCKRA_MONGO_EXPRESS_IMAGE,
            "port": 8081,
            "strip": False,
            "cpu": 0.3,
            "memory_mb": 256,
            "env": {
                "ME_CONFIG_MONGODB_URL": url,
                "ME_CONFIG_MONGODB_ENABLE_ADMIN": "true",
                "ME_CONFIG_BASICAUTH": "false",
                "ME_CONFIG_BASICAUTH_ENABLED": "false",
                "ME_CONFIG_SITE_BASEURL": f"{path}/",
                "ME_CONFIG_SITE_COOKIESECRET": secrets.token_hex(16),
                "ME_CONFIG_SITE_SESSIONSECRET": secrets.token_hex(16),
                "VCAP_APP_HOST": "0.0.0.0",
            },
        }
    if database.engine == "redis":
        return {
            "image": settings.DOCKRA_REDIS_COMMANDER_IMAGE,
            "port": 8081,
            "strip": False,
            "cpu": 0.2,
            "memory_mb": 192,
            "env": {
                "REDIS_HOST": host,
                "REDIS_PORT": str(port),
                "REDIS_PASSWORD": password,
                "REDIS_LABEL": database.name,
                "URL_PREFIX": path,
            },
        }
    raise RuntimeError(tr("No admin tool is defined for this engine"))


def labels(database, base: str, tool: dict) -> list[str]:
    router = container_name(database)
    path = prefix(database)
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    secure = base.startswith("https://")
    middlewares = [f"{router}-auth"]
    items = [
        "traefik.enable=true",
        f"traefik.docker.network={container_name(database)}",
        f"traefik.http.routers.{router}.rule=Host(`{host}`) && PathPrefix(`{path}/`)",
        f"traefik.http.routers.{router}.entrypoints={'websecure' if secure else 'web'}",
        f"traefik.http.routers.{router}.priority=200",
        f"traefik.http.routers.{router}.service={router}",
        f"traefik.http.middlewares.{router}-auth.forwardauth.address="
        f"{settings.DOCKRA_DBADMIN_AUTH_URL}/api/dbadmin/auth/{database.id}",
        f"traefik.http.services.{router}.loadbalancer.server.port={tool['port']}",
    ]
    if tool["strip"]:
        items.append(f"traefik.http.middlewares.{router}-strip.stripprefix.prefixes={path}")
        middlewares.append(f"{router}-strip")
    items.append(f"traefik.http.routers.{router}.middlewares={','.join(middlewares)}")
    if secure:
        items.append(f"traefik.http.routers.{router}.tls=true")
        if settings.DOCKRA_TLS_RESOLVER:
            items.append(f"traefik.http.routers.{router}.tls.certresolver={settings.DOCKRA_TLS_RESOLVER}")
    return items


def listening(proc_net: str, port: int) -> bool:
    """Parse /proc/net/tcp and tcp6: is the port in LISTEN state (0A)?"""
    for line in proc_net.splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[3] == "0A" and ":" in fields[1]:
            try:
                if int(fields[1].rsplit(":", 1)[1], 16) == port:
                    return True
            except ValueError:
                continue
    return False


# forwardAuth is called for every static file too; keep the check cheap.
_auth_cache: dict[str, float] = {}
AUTH_CACHE_SECONDS = 30


def cached_ok(key: str) -> bool:
    return _auth_cache.get(key, 0) > time.monotonic()


def remember_ok(key: str) -> None:
    now = time.monotonic()
    if len(_auth_cache) > 2000:
        for key, until in list(_auth_cache.items()):
            if until <= now:
                _auth_cache.pop(key, None)
    _auth_cache[key] = now + AUTH_CACHE_SECONDS
