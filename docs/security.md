# Security

**English** · [فارسی](fa/security.md)

Dockra is designed for a team running **its own** applications on **its own** server. This page describes what the platform protects, how, and — just as important — what it does not.

## Threat model

| In scope | Out of scope |
| --- | --- |
| An outsider on the internet trying to reach the API, dashboard, databases or other apps | Hostile tenants running deliberately malicious code (see [limitations](#known-limitations)) |
| A team member acting beyond their role (viewer deploying, developer deleting) | A compromised host OS or Docker daemon |
| A member of one organization reaching another organization's resources | Physical access to the server or its disks |
| A bug in one app exposing another app or the platform's own database | Supply-chain attacks on base images and packages your apps install |
| Stolen sessions, phishing of the CLI login, password guessing | Denial of service at the network level |

## Roles

| Action | Viewer | Developer | Admin | Owner |
| --- | :-: | :-: | :-: | :-: |
| See apps, databases, logs, charts, alerts | ✓ | ✓ | ✓ | ✓ |
| See secret env values and database URLs | | ✓ | ✓ | ✓ |
| Deploy, rollback, env, domains, disks, cron, webhooks | | ✓ | ✓ | ✓ |
| Console, terminal, database tunnel, download disk files and backups | | ✓ | ✓ | ✓ |
| Create databases and backups | | ✓ | ✓ | ✓ |
| Restore databases, delete apps and databases, manage members | | | ✓ | ✓ |

Enforcement is centralized in `accounts.permissions.IsMember`: reads need membership, writes need the view's `write_role` (developer by default). Every object lookup is scoped to `request.membership.organization`, so resources of other organizations return 404, not 403. Tests: `engine/test_platform.py`, `engine/tests.py`, `engine/test_hardening.py`.

Everyone (viewers included) manages their own password, 2FA and API tokens; a token carries the role of its owner. The owner role belongs to the initial `admin` and cannot be changed through the API; only the owner manages admins; nobody changes their own role.

## Authentication

- **Dashboard:** JWT (12 hours) carrying a session version (`sv`). Logout, password change, admin password reset and deactivation bump the version and invalidate every outstanding token immediately.
- **CLI browser login:** a one-time code valid for 10 minutes and approvable once. The approval page shows the last six characters of the code that `dockra login` printed and warns that approving someone else's link grants full access (phishing). Forms carry CSRF tokens and pages are served with a strict CSP (`default-src 'none'`). Approval issues an API token valid for `DOCKRA_CLI_TOKEN_DAYS` (365 by default).
- **API tokens:** prefixed `dockra_`, shown once, stored as sha256 only, optional expiry, revocable.
- **Passwords:** Django validators (minimum length, common passwords, numeric-only).
- **Throttling:** 8 failed attempts in 15 minutes per IP *or* per username temporarily blocks sign-in (dashboard and CLI). `X-Forwarded-For` is honoured only when the request comes from a trusted proxy (`DOCKRA_TRUSTED_PROXIES`, Traefik by default), which overwrites any client-supplied value.
- **TOTP 2FA:** any authenticator app; 8 one-time recovery codes stored as hashes; each 6-digit code is accepted once (replay protection); ±30 s clock skew.

## Secrets

Encrypted at rest with Fernet using a key derived from `DOCKRA_ENCRYPTION_KEY` (falls back to `DJANGO_SECRET_KEY`):

- all app environment variable values,
- database passwords,
- webhook secrets,
- users' TOTP keys.

Handling rules:

- Env vars reach containers through `--env-file`, never as command-line arguments visible in the host process list.
- Values marked secret are masked (`••••••`) for viewers; the API never overwrites a stored secret with the mask if a client echoes it back.
- FTP passwords used for disk imports are not persisted in the jobs table.
- The CLI asks for FTP passwords interactively so they do not land in shell history.

**Not encrypted by Dockra:** app and database data on disks, backup files on the server, and off-site S3 copies. Keep the bucket private and restrict access to `/var/lib/dockra`.

## Isolation

- **Docker socket.** The `api` service — the only one that parses user requests directly — has no access to `docker.sock`. Worker, builder and terminal do, which is root-equivalent on the host; they only act on queued jobs created by the API after authorization.
- **Containers.** Every app runs with CPU (`--cpus`, `--cpu-shares`), memory (no swap) and PID limits and `--security-opt no-new-privileges`. Built-in templates run as a non-root user (uid 1000). The OOM priority makes the kernel kill apps before databases before platform services.
- **Networks.** Each app has its own network. Only its instances, Traefik, the worker/builder that health-checks it, and databases explicitly linked to it are attached. Apps cannot reach other apps, unlinked databases, the platform database or platform services. Database admin UIs get their own network with just Traefik and one database.
- **Databases** publish no ports on the host; external access is only through the authenticated, ticketed tunnel (`dockra db connect`).
- **Terminal and tunnels** use signed one-time tickets valid for 60 seconds; role and organization are re-checked on connect; every session is audited.

## Input handling

| Risk | Mitigation | Where |
| --- | --- | --- |
| Path traversal in uploads and archives | `tarfile` `data` filter, explicit checks for absolute paths, `..`, links leaving the target; disk file API resolves paths and rejects anything outside the disk root | `engine/source.py`, `engine/disk_backup.py`, `engine/disk_import.py`, `apps_core/extra.py::_inside` |
| Disk name collisions between apps | volume names are validated so `shop`+`media-x` and `shop-media`+`x` cannot map to the same volume | `apps_core/validation.py` |
| SSRF on server-side downloads (backup URLs, archive URLs) | only http/https, the hostname is resolved and private, loopback, link-local and reserved addresses are rejected | `engine/dumps.py` |
| SSRF through the domain check | the CDN probe only contacts globally routable IPs | `apps_core/views.py::edge_server` |
| Command injection | Docker is invoked with argument lists, never a shell string; the console runs the user's command inside *their* container by design, with the working directory quoted with `shlex.quote` | `engine/drivers.py`, `apps_core/extra.py` |
| Traefik rule injection via redirects | redirect paths and targets are restricted to URL-safe characters; loops and duplicates are rejected | `apps_core/validation.py` |
| Webhook forgery | HMAC-SHA256 (`X-Hub-Signature-256`, `X-Gitea-Signature`) or constant-time token comparison (`X-Gitlab-Token`, `?token=`); apps without a secret reject all hooks | `apps_core/extra.py` |
| XSS in the dashboard | React escaping, strict CSP (scripts and connections only to the panel and API), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` | `web/next.config.ts` |

## Audit log

Visible in Settings and at `GET /api/audit`: deployments (manual and automatic), rollbacks, database lifecycle, backups and restores, console commands (with the command text), terminal sessions, database tunnels, webhook changes, disk file uploads and deletions, app lifecycle, and every membership change.

## Known limitations

- **Docker is not a sandbox for hostile code.** Containers share the host kernel. A kernel or runtime vulnerability can lead to host compromise. Do not use Dockra to run code from untrusted users. Stronger isolation (gVisor, Kata Containers, separate VMs per tenant) is on the roadmap.
- **Builds run on the host's Docker daemon.** A `Dockerfile` can run arbitrary commands during the build and use network and CPU until `DOCKRA_BUILD_TIMEOUT`. Only give deploy rights to people you trust with the server.
- **Disk creation uses a privileged helper** (`nsenter` into the host's namespaces) to create loop-mounted filesystems.
- **Apps have outbound internet access.** There is no egress filtering.
- **Rate limiting covers sign-in only.** Other endpoints require authentication but are not throttled; put the API behind Traefik and, if needed, add a rate-limit middleware.
- **GitLab-style `?token=` webhooks** put the secret in the URL, which may appear in proxy logs. Prefer signed (HMAC) webhooks.
- **The dashboard token lives in `localStorage`.** The CSP limits the damage of an XSS bug, but an injected script running on the panel origin could read it.
- **No encryption of data at rest** for disks and backups (see above).

## Hardening checklist

- [ ] Delete `deploy/.admin-password` and enable 2FA for every admin and owner
- [ ] One account per person; no shared logins
- [ ] Panel and API behind HTTPS, `API_BIND` and `PANEL_BIND` set to `127.0.0.1`
- [ ] Once HTTPS is stable, set `DOCKRA_HSTS_SECONDS=31536000`
- [ ] Disable or remove members who left
- [ ] `deploy/.env` is mode 600 and backed up separately
- [ ] Off-site backups to a private bucket
- [ ] Host clock synchronized (NTP) — TOTP depends on it
- [ ] Non-expiring tokens only for CI, with descriptive names

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
