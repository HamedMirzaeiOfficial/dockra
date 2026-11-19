import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured

DEV_SECRET_KEY = "dev-only-dockra-change-me-32byte-key"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
if not DEBUG and SECRET_KEY in (DEV_SECRET_KEY, "change-me-secret", ""):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY on the server (deploy/install.sh generates one)")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
# The database admin forwardAuth request from Traefik arrives with the internal host api:8000.
if "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("api")
# Traefik terminates TLS and sends X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "accounts",
    "apps_core",
    "databases",
    "audit",
    "engine",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

if os.environ.get("DATABASE_URL"):
    from urllib.parse import urlparse

    _u = urlparse(os.environ["DATABASE_URL"])
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _u.path.lstrip("/"),
            "USER": _u.username,
            "PASSWORD": _u.password or "",
            "HOST": _u.hostname,
            "PORT": str(_u.port or 5432),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH") or BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DOCKRA_TIME_ZONE", "UTC")  # also the timezone of cron schedules
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
DOCKRA_DATA_ROOT = os.environ.get("DOCKRA_DATA_ROOT", "")
if DOCKRA_DATA_ROOT:
    MEDIA_ROOT = Path(DOCKRA_DATA_ROOT) / "media"
    WORK_ROOT = Path(DOCKRA_DATA_ROOT) / "work"
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
else:
    MEDIA_ROOT = BASE_DIR / "media"
    WORK_ROOT = BASE_DIR / "work"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("accounts.authentication.DockraAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("accounts.permissions.IsMember",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

CORS_EXPOSE_HEADERS = ["Content-Disposition"]
CORS_ALLOW_HEADERS = (*default_headers, "x-organization")
_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "")
if _origins:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]
else:
    CORS_ALLOW_ALL_ORIGINS = True

DOCKRA_DRIVER = os.environ.get("DOCKRA_DRIVER", "fake")
DOCKRA_EAGER = os.environ.get("DOCKRA_EAGER", "1" if DOCKRA_DRIVER == "fake" else "0") == "1"
PUBLIC_IP = os.environ.get("PUBLIC_IP", "127.0.0.1")
# Default app domain, e.g. apps.example.com with a wildcard record in your DNS. Empty = <IP>.sslip.io
# (sslip.io is a public DNS service and does not resolve on isolated networks).
DOCKRA_APPS_DOMAIN = os.environ.get("DOCKRA_APPS_DOMAIN", "").strip().strip(".").lower()
# Resolvers that the domain "check record" queries directly when the container resolver fails (comma separated).
DOCKRA_DNS_SERVERS = [
    item.strip()
    for item in (os.environ.get("DOCKRA_DNS_SERVERS") or "1.1.1.1,8.8.8.8").split(",")
    if item.strip()
]
DOCKRA_NETWORK = os.environ.get("DOCKRA_NETWORK", "dockra")
DOCKRA_APT_MIRROR = os.environ.get("DOCKRA_APT_MIRROR", "").rstrip("/")
DOCKRA_PIP_INDEX_URL = os.environ.get("DOCKRA_PIP_INDEX_URL", "")
DOCKRA_PIP_EXTRA_INDEX_URL = os.environ.get("DOCKRA_PIP_EXTRA_INDEX_URL", "")
DOCKRA_PIP_FALLBACK_MIRROR = os.environ.get("DOCKRA_PIP_FALLBACK_MIRROR", "")
# Docker Hub mirrors (space separated); install.sh also writes them to the daemon and builds try them first.
DOCKRA_DOCKER_MIRRORS = os.environ.get("DOCKRA_DOCKER_MIRRORS", "")
DOCKRA_HOST_CPU = float(os.environ.get("DOCKRA_HOST_CPU") or 0)
DOCKRA_HOST_MEMORY_MB = int(os.environ.get("DOCKRA_HOST_MEMORY_MB") or 0)
DOCKRA_HOST_DISK_GB = int(os.environ.get("DOCKRA_HOST_DISK_GB") or 0)
DOCKRA_RESERVED_CPU = float(os.environ.get("DOCKRA_RESERVED_CPU") or 1)
DOCKRA_RESERVED_MEMORY_MB = int(os.environ.get("DOCKRA_RESERVED_MEMORY_MB") or 2048)
DOCKRA_RESERVED_DISK_GB = int(os.environ.get("DOCKRA_RESERVED_DISK_GB") or 20)
# Shared CPU: each app's cores are a guaranteed share (cgroup weight) and may burst to this factor of idle cores; 1 = fixed cap.
DOCKRA_CPU_BURST = float(os.environ.get("DOCKRA_CPU_BURST") or 3)
# Reservations are accepted up to this factor of allocatable capacity. Short on CPU means slower; short on RAM means OOM.
DOCKRA_CPU_OVERCOMMIT = float(os.environ.get("DOCKRA_CPU_OVERCOMMIT") or 2)
DOCKRA_MEMORY_OVERCOMMIT = float(os.environ.get("DOCKRA_MEMORY_OVERCOMMIT") or 1.2)
# With overcommit, real host memory is checked too: above this percentage, new or larger apps are rejected.
DOCKRA_MEMORY_REAL_PERCENT = int(os.environ.get("DOCKRA_MEMORY_REAL_PERCENT") or 85)
# Alert when processes waited for CPU this percentage of the last minute (PSI), i.e. burst capacity is exhausted.
DOCKRA_CPU_PRESSURE_PERCENT = int(os.environ.get("DOCKRA_CPU_PRESSURE_PERCENT") or 25)

DOCKRA_ENCRYPTION_KEY = os.environ.get("DOCKRA_ENCRYPTION_KEY", "")
# The CLI is served by the server itself; compose mounts the repository cli folder read-only.
DOCKRA_CLI_DIR = os.environ.get("DOCKRA_CLI_DIR") or str(BASE_DIR.parent / "cli")
DOCKRA_CLI_BIN_DIR = os.environ.get("DOCKRA_CLI_BIN_DIR", "")
# Proxies whose X-Forwarded-For is trusted (names or IPs, comma separated).
DOCKRA_TRUSTED_PROXIES = [
    item.strip()
    for item in os.environ.get("DOCKRA_TRUSTED_PROXIES", "traefik" if DOCKRA_DRIVER == "docker" else "").split(",")
    if item.strip()
]
DOCKRA_LOGIN_MAX_FAILS = int(os.environ.get("DOCKRA_LOGIN_MAX_FAILS") or 8)
DOCKRA_LOGIN_WINDOW_SECONDS = int(os.environ.get("DOCKRA_LOGIN_WINDOW_SECONDS") or 900)
DOCKRA_CLI_TOKEN_DAYS = int(os.environ.get("DOCKRA_CLI_TOKEN_DAYS") or 365)
DOCKRA_NPM_REGISTRY = os.environ.get("DOCKRA_NPM_REGISTRY", "")
DOCKRA_BUILD_TIMEOUT = int(os.environ.get("DOCKRA_BUILD_TIMEOUT") or 1800)
DOCKRA_RELEASE_TIMEOUT = int(os.environ.get("DOCKRA_RELEASE_TIMEOUT") or 900)
DOCKRA_HEALTH_TIMEOUT = int(os.environ.get("DOCKRA_HEALTH_TIMEOUT") or 90)
DOCKRA_CRON_TIMEOUT = int(os.environ.get("DOCKRA_CRON_TIMEOUT") or 600)
DOCKRA_EXEC_TIMEOUT = int(os.environ.get("DOCKRA_EXEC_TIMEOUT") or 120)
DOCKRA_JOB_STALE_SECONDS = int(os.environ.get("DOCKRA_JOB_STALE_SECONDS") or 120)
DOCKRA_LOG_DAYS = int(os.environ.get("DOCKRA_LOG_DAYS") or 14)
DOCKRA_LOG_KEEP = int(os.environ.get("DOCKRA_LOG_KEEP") or 50)
DOCKRA_TERMINAL_PORT = int(os.environ.get("DOCKRA_TERMINAL_PORT") or 8001)
# Terminal service name on the internal network, so the API can check it is up before issuing a ticket.
DOCKRA_TERMINAL_HOST = os.environ.get("DOCKRA_TERMINAL_HOST", "")
DOCKRA_TERMINAL_PUBLIC_URL = os.environ.get("DOCKRA_TERMINAL_PUBLIC_URL", "")
DOCKRA_ALERT_PERCENT = int(os.environ.get("DOCKRA_ALERT_PERCENT") or 90)
DOCKRA_ALERT_MINUTES = int(os.environ.get("DOCKRA_ALERT_MINUTES") or 5)
# Uptime probes go through Traefik on the dockra network; empty = disabled.
DOCKRA_PROBE_URL = os.environ.get("DOCKRA_PROBE_URL", "http://traefik" if DOCKRA_DRIVER == "docker" else "")
# Traefik's own Prometheus metrics, internal network only; empty = no request statistics.
DOCKRA_TRAEFIK_METRICS_URL = os.environ.get(
    "DOCKRA_TRAEFIK_METRICS_URL", "http://traefik:8082/metrics" if DOCKRA_DRIVER == "docker" else ""
)
DOCKRA_ERROR_RATE_PERCENT = int(os.environ.get("DOCKRA_ERROR_RATE_PERCENT") or 5)
DOCKRA_METRICS_DAYS = int(os.environ.get("DOCKRA_METRICS_DAYS") or 7)
DOCKRA_BACKUP_KEEP = int(os.environ.get("DOCKRA_BACKUP_KEEP") or 3)
DOCKRA_AUTO_BACKUP_HOURS = int(os.environ.get("DOCKRA_AUTO_BACKUP_HOURS") or 24)
# A failed automatic backup is retried after this many minutes (not a full cycle later).
DOCKRA_BACKUP_RETRY_MINUTES = int(os.environ.get("DOCKRA_BACKUP_RETRY_MINUTES") or 60)
# Disks are usually larger, so fewer copies are kept. 0 disables automatic disk backups.
DOCKRA_DISK_BACKUP_HOURS = int(os.environ.get("DOCKRA_DISK_BACKUP_HOURS") or 24)
DOCKRA_DISK_BACKUP_KEEP = int(os.environ.get("DOCKRA_DISK_BACKUP_KEEP") or 3)
# Backups of a deleted disk are kept for this many days.
DOCKRA_ORPHAN_BACKUP_DAYS = int(os.environ.get("DOCKRA_ORPHAN_BACKUP_DAYS") or 30)
DOCKRA_S3_ENDPOINT = os.environ.get("DOCKRA_S3_ENDPOINT", "").rstrip("/")
DOCKRA_S3_BUCKET = os.environ.get("DOCKRA_S3_BUCKET", "")
DOCKRA_S3_ACCESS_KEY = os.environ.get("DOCKRA_S3_ACCESS_KEY", "")
DOCKRA_S3_SECRET_KEY = os.environ.get("DOCKRA_S3_SECRET_KEY", "")
DOCKRA_S3_REGION = os.environ.get("DOCKRA_S3_REGION", "us-east-1")
DOCKRA_S3_PREFIX = os.environ.get("DOCKRA_S3_PREFIX", "dockra")
# Image used to create disks (via nsenter on the host); compose names the API image dockra-api.
# Let's Encrypt needs public internet; on isolated networks leave it empty and put certificates in /var/lib/dockra/certs.
DOCKRA_TLS_RESOLVER = os.environ.get("DOCKRA_TLS_RESOLVER", "le")
DOCKRA_BUILD_CACHE_HOURS = int(os.environ.get("DOCKRA_BUILD_CACHE_HOURS") or 720)
DOCKRA_TOOLS_IMAGE = os.environ.get("DOCKRA_TOOLS_IMAGE", "dockra-api")
DOCKRA_PANEL_PG_IMAGE = os.environ.get("DOCKRA_PANEL_PG_IMAGE", "postgres:16")
# Database admin UI (pgAdmin, ...) under /dbadmin on the API domain; closed after this idle time.
API_DOMAIN = os.environ.get("API_DOMAIN", "").strip().lower()
DOCKRA_DBADMIN_URL = os.environ.get("DOCKRA_DBADMIN_URL", "").strip().rstrip("/")
DOCKRA_DBADMIN_AUTH_URL = os.environ.get("DOCKRA_DBADMIN_AUTH_URL", "http://api:8000").rstrip("/")
DOCKRA_DBADMIN_IDLE_MINUTES = int(os.environ.get("DOCKRA_DBADMIN_IDLE_MINUTES") or 60)
DOCKRA_PGADMIN_IMAGE = os.environ.get("DOCKRA_PGADMIN_IMAGE", "dpage/pgadmin4:9")
DOCKRA_PHPMYADMIN_IMAGE = os.environ.get("DOCKRA_PHPMYADMIN_IMAGE", "phpmyadmin:5")
DOCKRA_MONGO_EXPRESS_IMAGE = os.environ.get("DOCKRA_MONGO_EXPRESS_IMAGE", "mongo-express:1")
DOCKRA_REDIS_COMMANDER_IMAGE = os.environ.get("DOCKRA_REDIS_COMMANDER_IMAGE", "rediscommander/redis-commander:latest")
DOCKRA_BLOB_KEEP_DEPLOYS = int(os.environ.get("DOCKRA_BLOB_KEEP_DEPLOYS") or 5)
DOCKRA_TELEGRAM_BOT_TOKEN = os.environ.get("DOCKRA_TELEGRAM_BOT_TOKEN", "")
DOCKRA_TELEGRAM_CHAT_ID = os.environ.get("DOCKRA_TELEGRAM_CHAT_ID", "")
DOCKRA_TELEGRAM_API = os.environ.get("DOCKRA_TELEGRAM_API", "https://api.telegram.org").rstrip("/")
DOCKRA_NOTIFY_WEBHOOK = os.environ.get("DOCKRA_NOTIFY_WEBHOOK", "")

DATA_UPLOAD_MAX_MEMORY_SIZE = 500 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
# Uploads are written next to their final destination so moving them is a single rename.
FILE_UPLOAD_TEMP_DIR = str(MEDIA_ROOT / "tmp") if DOCKRA_DATA_ROOT else None
if FILE_UPLOAD_TEMP_DIR:
    Path(FILE_UPLOAD_TEMP_DIR).mkdir(parents=True, exist_ok=True)
