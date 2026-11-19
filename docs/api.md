# REST API reference

Base URL: `API_PUBLIC_URL` (e.g. `https://api.example.com`). Paths work with and without a trailing slash.

## Conventions

- **Authentication:** `Authorization: Bearer <token>`, either a sign-in JWT (`access`, 12 hours) or an API token (`dockra_…`). JWTs are revoked immediately on logout, password change, admin reset or deactivation; API tokens on deactivation or admin reset.
- **Organization:** users with several memberships may send `X-Organization: <slug>`; otherwise the first membership is used.
- **Roles:** reads (GET) for every member; writes need developer unless marked *admin* ([roles](security.md#roles)).
- **Errors:** `{"detail": "…"}` with 400, 401, 403, 404, 409, 413, 429 or 502.
- **Queued work** (deploy, start/stop, backups, …): `202` means queued, `200` means done inline (local fake driver). Follow progress on the resource (e.g. `deployments`). An immediate failure returns `400` with `detail`.
- **Times:** ISO 8601.

## Auth and account

| Method and path | Description |
| --- | --- |
| `POST /api/auth/login` | `{username, password, otp?}` → `{access, refresh, user}`. With 2FA and no code: `400 {otp_required: true}`. Throttled: `429 {retry_after}`. Unauthenticated |
| `GET /api/auth/me` | `id, username, name, initials, role, role_label, organization{name, slug}` |
| `POST /api/auth/logout` | revoke every panel session of this user (204); API tokens stay |
| `POST /api/auth/password` | `{old_password, new_password}` → `{access}` (fresh JWT; other sessions revoked) |
| `GET /api/auth/2fa` | `{enabled}` |
| `POST /api/auth/2fa/setup` | `{secret, otpauth_url}` (not active yet) |
| `POST /api/auth/2fa/confirm` | `{code}` → `{enabled: true, recovery_codes: [8]}` |
| `POST /api/auth/2fa/disable` | `{password, code}` → `{enabled: false}` |
| `GET /api/auth/tokens` | own tokens (any role): `id, name, prefix, created_at, last_used_at, expires_at` |
| `POST /api/auth/tokens` | `{name, expires_days?}` (default 90; `null`/`0` = never) → `{id, name, prefix, token, expires_at}`; `token` is shown once |
| `DELETE /api/auth/tokens/<id>` | revoke |
| `GET /api/members` | `id, username, name, role, role_label, is_active, is_self` |
| `POST /api/members` | *admin*. `{username, password, role: admin\|developer\|viewer, first_name?, last_name?}`; only the owner grants `admin` |
| `PATCH /api/members/<id>` | *admin*. `{role?, is_active?}`; not for yourself or the owner; only the owner changes admins |
| `DELETE /api/members/<id>` | *admin*. Same rules |
| `POST /api/members/<id>/password` | *admin*. `{password}`; revokes that user's sessions and tokens (204) |
| `GET /api/audit` | `id, action, target, detail, actor, created_at` |

## Overview

| Method and path | Description |
| --- | --- |
| `GET /api/health` | `{ok, driver, services: [{name, kind, queues, last_seen, ok}]}`; unauthenticated; **503** when worker, builder or terminal is silent for 90 s |
| `GET /api/dashboard` | totals, capacity (`cpu`, `memory_mb`, `disk_gb` × total, reserved, allocatable, used, free), services, recent deployments, apps |
| `GET /api/monitoring` | `{apps (with metrics), databases}` |
| `GET /api/alerts?status=firing\|resolved\|all` | `id, app, app_name, kind, kind_label, message, status, started_at, resolved_at` |
| `GET /api/domains` | every domain of the organization |

## Apps

| Method and path | Description |
| --- | --- |
| `GET /api/apps` | list |
| `POST /api/apps` | `{name, slug, platform?, port?, instances?, cpu?, memory_mb?, git_url?, git_branch?}` → app (201) |
| `GET /api/apps/<slug>` | app plus `env, domains, redirects, disks, cron, metrics, pipeline` |
| `PATCH /api/apps/<slug>` | any of `name, instances, cpu, memory_mb, port, platform, git_url, git_branch, health_path` |
| `DELETE /api/apps/<slug>` | *admin*. Removes containers, disks and logs; 204 or `202 {job}` |
| `POST /api/apps/<slug>/restart` \| `stop` \| `start` | → `{app, job}` |
| `POST /api/apps/<slug>/rollback` | `{deployment_id}` → `{deployment, job, app}` |
| `GET /api/apps/<slug>/deployments` | `id, number, version, status, status_label, message, image, actor, log, is_current, created_at, finished_at` |
| `POST /api/apps/<slug>/deployments/<id>/cancel` | `202 {state: canceled\|canceling}`; 400 if not queued or building |
| `GET /api/apps/<slug>/metrics?range=1h\|6h\|24h\|7d` | latest minute (`cpu, ram, network_mbps, requests_per_min, errors_5xx_per_min, error_rate, avg_ms, p95_ms`, …) and `series[…]` |

App fields: `id, name, slug, platform, platform_label, status (running|deploying|stopped|failed), status_label, instances, desired_instances, port, cpu, memory_mb, git_url, git_branch, health_path, webhook_enabled, runtime, current_image, region, region_label, created_at`.

### Deploying

| Method and path | Description |
| --- | --- |
| `POST /api/apps/<slug>/sync` | `{files: [{path, hash (sha256), size, mode}]}` → `{missing: [hash], bytes, files}`. Relative paths without `..`; at most 50,000 files |
| `POST /api/apps/<slug>/blobs` | raw tar/tar.gz whose member names are content hashes; at most 2 GB → `{stored}`; hash mismatch 400 |
| `POST /api/apps/<slug>/deploy` | JSON `{message, files}` after sync/blobs (`409 {missing}` if a blob is absent); multipart `{message, file: tar.gz}`; or JSON `{message, git_url?}` → `{deployment, job}` |

### Logs and execution

| Method and path | Description |
| --- | --- |
| `GET /api/apps/<slug>/logs?deployment=<id>` | `{deployment, text, updated_at}` — build log plus the last 400 container lines |
| `POST /api/apps/<slug>/logs/ticket?tail=200` | `{url, ticket}` for live logs over WebSocket (binary frames of raw log bytes; `{"type":"exit"}` / `{"type":"error","detail"}` text frames); at most one hour |
| `GET /api/apps/<slug>/logs/archive` | `[{name, release, ended_at, size}]` |
| `GET /api/apps/<slug>/logs/archive/<name>` | `text/plain` |
| `GET /api/apps/<slug>/logs/search?q=&limit=500` | `{matches: [{source, line}], truncated}`; `q` needs two characters |
| `POST /api/apps/<slug>/exec` | `{command, cwd?}` → `{output, exit_code, cwd}` |
| `POST /api/apps/<slug>/terminal` | `{url, ticket}`; 503 if the terminal service is down |

**Terminal WebSocket protocol.** Client → server: binary frames (input bytes) or JSON `{"type":"input","data":"…"}` and `{"type":"resize","cols":N,"rows":N}`. Server → client: binary frames (terminal output), then `{"type":"exit","code":N}` or `{"type":"error","detail":"…"}`.

### Env, domains, redirects, disks, cron

| Method and path | Description |
| --- | --- |
| `GET /api/apps/<slug>/env` | `[{id, key, value, is_secret}]`; secret values are `••••••` for viewers |
| `PUT /api/apps/<slug>/env` | `{items: [{key, value, is_secret}]}` — **replaces the whole list**; echoing the mask keeps the stored secret |
| `GET /api/apps/<slug>/domains` | `id, hostname, kind (sslip\|custom), ssl_status (pending\|active\|error), is_primary, redirect_https, created_at` |
| `POST /api/apps/<slug>/domains` | `{hostname}` |
| `GET /api/apps/<slug>/domains/<id>/check` | `{state: ok\|missing\|mismatch\|proxied, message, found, expected}` |
| `PATCH /api/apps/<slug>/domains/<id>` | `{redirect_https}` |
| `DELETE /api/apps/<slug>/domains/<id>` | primary domain: 400 |
| `GET\|POST /api/apps/<slug>/redirects` | `source_host (empty = all), source_path, match (prefix\|exact), target, keep_path, permanent`; loops and duplicates 400 |
| `PATCH\|DELETE /api/apps/<slug>/redirects/<id>` | |
| `GET\|POST /api/apps/<slug>/disks` | `{name, mount_to, size_gb}` |
| `PATCH /api/apps/<slug>/disks/<name>` | `{size_gb? (grow only), mount_to?, backup_hours?, backup_keep?}` |
| `DELETE /api/apps/<slug>/disks/<name>` | `{job}`; containers restart without the disk, then data is deleted |
| `GET /api/apps/<slug>/disks/<name>/files?path=/` | `{path, entries: [{name, type, size, modified}]}` |
| `DELETE /api/apps/<slug>/disks/<name>/files?path=` | delete a file or folder |
| `GET /api/apps/<slug>/disks/<name>/download?path=` | file, or folder as tar.gz; viewers 403 |
| `POST /api/apps/<slug>/disks/<name>/upload?path=` | multipart `file`, or raw tar/tar.gz extracted at the path |
| `POST /api/apps/<slug>/disks/<name>/import` | raw tar/zip (`?replace=1`) or JSON `{url}` / `{ftp: {…}}` → `{job}` |
| `GET\|POST /api/apps/<slug>/disks/<name>/backups` | list / create disk backups |
| `POST /api/apps/<slug>/disks/<name>/backups/<id>/restore` | restore (a safety backup is taken first) |
| `GET\|POST /api/apps/<slug>/cron` | `{schedule, command}` → `id, schedule, command, enabled, last_run_at, last_status, last_output` |
| `PATCH\|DELETE /api/apps/<slug>/cron/<id>` | |

### Webhooks

| Method and path | Description |
| --- | --- |
| `GET /api/apps/<slug>/webhook` | `{enabled, url}` |
| `POST /api/apps/<slug>/webhook` | create or rotate → `{enabled, url, secret}`; needs `git_url` |
| `DELETE /api/apps/<slug>/webhook` | disable |
| `POST /api/hooks/<slug>` | receives pushes; unauthenticated, verified with `X-Hub-Signature-256`, `X-Gitea-Signature`, `X-Gitlab-Token` or `?token=`. `ping` → `{pong}`; other branch → `{skipped}`; success `202 {deployment, version}` |

## Databases

| Method and path | Description |
| --- | --- |
| `GET /api/databases` | list |
| `POST /api/databases` | `{name, engine, version?, cpu?, memory_mb?, disk_gb?}` |
| `POST /api/databases/import` | raw dump body with options in the query string, or `?upload=<id>` / `?url=` → `{job, database}` |
| `GET /api/databases/<id>` | details; `password` and `url` for non-viewers |
| `PATCH /api/databases/<id>` | `{cpu?, memory_mb?, disk_gb? (grow only), backup_hours?, backup_keep?}` |
| `POST /api/databases/<id>/stop` \| `start` | → `{database, job}` |
| `DELETE /api/databases/<id>` | *admin* |
| `POST /api/databases/<id>/link` | `{app, env_key?}` |
| `POST /api/databases/<id>/backup` | → `{backup, job}` |
| `DELETE /api/databases/<id>/backups/<bid>` | |
| `GET /api/databases/<id>/backups/<bid>/download` | file; viewers 403 |
| `POST /api/databases/<id>/restore` | *admin*. JSON `{backup_id}` or a file → `{job}` |
| `POST /api/databases/<id>/tunnel` | `{url, ticket}`; WebSocket relaying raw TCP |
| `GET\|POST\|DELETE /api/databases/<id>/admin` | web admin UI status / open / close |

## Uploads and jobs

| Method and path | Description |
| --- | --- |
| `POST /api/uploads` | start a resumable upload → `{upload}` |
| `GET /api/uploads/<id>` | `{size}` received so far |
| `PUT /api/uploads/<id>?offset=N` | append a chunk; wrong offset → `409 {size}` |
| `GET /api/jobs/<id>` | `{status, log, result}` of long-running jobs (imports, restores) |

## CLI distribution and browser login

| Path | Description |
| --- | --- |
| `POST /api/auth/cli/start` | → `{code}` |
| `GET /cli?code=` | browser sign-in and approval page |
| `GET /api/auth/cli/poll?code=` | `{status: pending}` or once `{status: ready, token, username}`; expired 410 |
| `POST /api/auth/cli/watch` | `{app, after}` → `{code}` for the browser deploy tracker `/cli/deploy?ticket=` |
| `GET /cli/install.sh` \| `/cli/install.ps1` | install scripts pointing at this server |
| `GET /cli/dockra.py` | the CLI, with `X-Dockra-Version` and `X-Dockra-Sha256` |
| `GET /cli/version` | `{version, binaries}` |
| `GET /cli/bin/<name>` | `dockra-linux`, `dockra-macos`, `dockra-windows.exe` |
