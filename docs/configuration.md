# Configuration

**English** · [فارسی](fa/configuration.md)

All settings are environment variables in `deploy/.env` (template: [`deploy/.env.example`](../deploy/.env.example)). Every value reaches the API, worker, builder and terminal containers. Re-run `deploy/install.sh` after changes.

## Addresses

| Variable | Default | Description |
| --- | --- | --- |
| `PUBLIC_IP` | `127.0.0.1` | server IP; used for sslip.io domains and DNS instructions |
| `API_PUBLIC_URL` | `http://127.0.0.1:8000` | API URL used by the browser; compiled into the panel build |
| `PANEL_ORIGIN` | `http://127.0.0.1:3000` | panel origin; the only CORS origin allowed by the API |
| `API_DOMAIN`, `PANEL_DOMAIN` | derived | domains behind Traefik; `/ws` on the API domain goes to the terminal service |
| `API_BIND`, `PANEL_BIND` | `0.0.0.0` | interfaces for the raw ports 8000/8001 and 3000; set `127.0.0.1` once HTTPS works |
| `DOCKRA_APPS_DOMAIN` | empty | default app domain `<slug>.<domain>` with a wildcard DNS record; empty = `<slug>.<ip>.sslip.io` |
| `DJANGO_ALLOWED_HOSTS` | `*` | allowed API hosts (comma separated) |
| `DOCKRA_ORG_NAME` | `Default organization` | organization name on first install |
| `DOCKRA_TIME_ZONE` | `UTC` | platform time zone, also used for cron schedules |

## Secrets

| Variable | Default | Description |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | generated | signs sessions and tickets; the API refuses to start with the placeholder in production |
| `DOCKRA_ENCRYPTION_KEY` | generated | encrypts env values, database passwords, webhook secrets, TOTP keys. **Never change after install**; falls back to `DJANGO_SECRET_KEY` |
| `POSTGRES_PASSWORD` | generated | platform database password |
| `DOCKRA_ADMIN_PASSWORD` | generated | `admin` password on first run only |

## Capacity

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_HOST_CPU`, `DOCKRA_HOST_MEMORY_MB`, `DOCKRA_HOST_DISK_GB` | `0` (detect) | host capacity |
| `DOCKRA_RESERVED_CPU`, `_MEMORY_MB`, `_DISK_GB` | `1`, `2048`, `20` | kept for the platform and OS |
| `DOCKRA_CPU_BURST` | `3` | an app may use up to this multiple of its cores when the host is idle (1 = hard cap) |
| `DOCKRA_CPU_OVERCOMMIT`, `DOCKRA_MEMORY_OVERCOMMIT` | `2`, `1.2` | reservations accepted up to this factor of capacity |
| `DOCKRA_MEMORY_REAL_PERCENT` | `85` | above this real host memory usage, new or larger apps are rejected |
| `DOCKRA_CPU_PRESSURE_PERCENT` | `25` | alert when processes waited for CPU this share of the last minute (PSI) |
| `DOCKRA_BUILDERS` | `1` | concurrent builds (about half a core each) |

## Builds

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_PIP_INDEX_URL` | empty | PyPI mirror for app builds and the platform image |
| `DOCKRA_PIP_EXTRA_INDEX_URL` | empty | secondary index tried first when the mirror fails |
| `DOCKRA_PIP_FALLBACK_MIRROR` | empty | next fallback; the chain always ends with `https://pypi.org/simple` |
| `DOCKRA_NPM_REGISTRY` | empty | npm mirror |
| `DOCKRA_APT_MIRROR` | empty | Debian mirror replacing `http://deb.debian.org` |
| `DOCKRA_DOCKER_MIRRORS` | empty | Docker Hub mirrors (space separated); written to the daemon and tried for base images |
| `DOCKRA_BUILD_TIMEOUT` | `1800` | seconds per image build |
| `DOCKRA_RELEASE_TIMEOUT` | `900` | seconds for the release command |
| `DOCKRA_HEALTH_TIMEOUT` | `90` | seconds for a new release to become healthy |
| `DOCKRA_BUILD_CACHE_HOURS` | `720` | BuildKit cache (including pip and npm) retention |
| `DOCKRA_BLOB_KEEP_DEPLOYS` | `5` | uploaded files of this many recent deployments per app are kept |

## Operations

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_CRON_TIMEOUT` | `600` | seconds per cron run |
| `DOCKRA_EXEC_TIMEOUT` | `120` | seconds for console commands and `dockra exec` |
| `DOCKRA_JOB_STALE_SECONDS` | `120` | a running job silent this long is marked failed |
| `DOCKRA_LOG_DAYS`, `DOCKRA_LOG_KEEP` | `14`, `50` | archived log retention per app |
| `DOCKRA_METRICS_DAYS` | `7` | usage history retention |
| `DOCKRA_TERMINAL_PUBLIC_URL` | empty | public WebSocket URL of the terminal if it differs (e.g. `wss://api.example.com/ws`) |
| `DOCKRA_TERMINAL_PORT` | `8001` | terminal service port |
| `DOCKRA_PANEL_CONNECT_SRC` | empty | extra origins allowed by the panel CSP (space separated) |
| `DOCKRA_DBADMIN_IDLE_MINUTES` | `60` | idle time before a database admin UI is closed |
| `DOCKRA_PGADMIN_IMAGE`, `DOCKRA_PHPMYADMIN_IMAGE`, `DOCKRA_MONGO_EXPRESS_IMAGE`, `DOCKRA_REDIS_COMMANDER_IMAGE` | official images | override for private registries |

## Backups

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_AUTO_BACKUP_HOURS` | `24` | default database backup interval (0 = off); overridable per database |
| `DOCKRA_BACKUP_KEEP` | `3` | ready backups kept per database (and for the platform's own daily backup) |
| `DOCKRA_BACKUP_RETRY_MINUTES` | `60` | retry delay for a failed automatic backup |
| `DOCKRA_DISK_BACKUP_HOURS`, `DOCKRA_DISK_BACKUP_KEEP` | `24`, `3` | the same for app disks |
| `DOCKRA_ORPHAN_BACKUP_DAYS` | `30` | how long backups of a deleted disk are kept |
| `DOCKRA_S3_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY`, `_SECRET_KEY` | empty | off-site copies to any S3-compatible storage |
| `DOCKRA_S3_REGION` | `us-east-1` | SigV4 region |
| `DOCKRA_S3_PREFIX` | `dockra` | key prefix: `<prefix>/databases/<name>/…`, `<prefix>/panel/…` |
| `DOCKRA_PANEL_PG_IMAGE` | `postgres:16` | `pg_dump` image for the platform backup; must match the `db` service version |

## TLS

| Variable | Default | Description |
| --- | --- | --- |
| `ACME_EMAIL` | | Let's Encrypt account email |
| `DOCKRA_TLS_RESOLVER` | `le` | Traefik certificate resolver; empty = only certificates from `/var/lib/dockra/certs` |
| `DOCKRA_HSTS_SECONDS` | `0` | HSTS on panel and API HTTPS; enable only once HTTPS is stable |

## Monitoring and notifications

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_ALERT_PERCENT` | `90` | CPU, memory and disk threshold |
| `DOCKRA_ALERT_MINUTES` | `5` | consecutive minutes above the threshold |
| `DOCKRA_ERROR_RATE_PERCENT` | `5` | 5xx rate threshold (with at least 20 requests in the window) |
| `DOCKRA_TRAEFIK_METRICS_URL` | `http://traefik:8082/metrics` | Traefik Prometheus endpoint (internal only); empty = no request stats |
| `DOCKRA_PROBE_URL` | `http://traefik` | base for "unreachable" probes; empty = off |
| `DOCKRA_TELEGRAM_BOT_TOKEN`, `DOCKRA_TELEGRAM_CHAT_ID` | empty | Telegram notifications |
| `DOCKRA_TELEGRAM_API` | `https://api.telegram.org` | any Telegram-compatible API or proxy |
| `DOCKRA_NOTIFY_WEBHOOK` | empty | receives `POST {event, text, …}` JSON |

## Security

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_TRUSTED_PROXIES` | `traefik` | proxies whose `X-Forwarded-For` is honoured (names or IPs) |
| `DOCKRA_LOGIN_MAX_FAILS`, `DOCKRA_LOGIN_WINDOW_SECONDS` | `8`, `900` | sign-in throttling |
| `DOCKRA_CLI_TOKEN_DAYS` | `365` | lifetime of tokens issued by `dockra login` |
| `DOCKRA_DNS_SERVERS` | `1.1.1.1,8.8.8.8` | resolvers for the domain record check when the container resolver fails |

## Development only

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKRA_DRIVER` | `fake` | `docker` on servers (set by compose) |
| `DOCKRA_EAGER` | `1` with fake | run jobs inline in the request |
| `SQLITE_PATH` | `api/db.sqlite3` | SQLite file when `DATABASE_URL`/Postgres settings are absent |
| `DOCKRA_DATA_ROOT` | `api/var` | data folder for local runs |
