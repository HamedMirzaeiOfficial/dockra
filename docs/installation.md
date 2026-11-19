# Installation and operations

**English** · [فارسی](fa/installation.md)

## Requirements

| Item | Minimum |
| --- | --- |
| OS | Linux with systemd (Ubuntu 22.04+, Debian 12) |
| Docker | Docker Engine 24+ with Compose 2.24+ |
| Hardware | 2 cores, 4 GB RAM, 40 GB disk. The platform reserves 1 core, 2 GB and 20 GB for itself; the rest is for apps and databases |
| Access | root or sudo; loop device support (disks are ext4 images on loop devices) |
| Ports | 80 and 443 (Traefik); 8000 (API), 8001 (terminal) and 3000 (panel) until you put them behind HTTPS |

## Install

```bash
git clone <repository> dockra
cd dockra/deploy
cp .env.example .env
```

Set at least ([all settings](configuration.md)):

```bash
PUBLIC_IP=203.0.113.10                     # the server's public IP
ACME_EMAIL=ops@example.com                 # Let's Encrypt account
API_PUBLIC_URL=http://203.0.113.10:8000    # where the panel and CLI reach the API
PANEL_ORIGIN=http://203.0.113.10:3000      # panel origin (CORS)
DOCKRA_APPS_DOMAIN=apps.example.com        # optional; otherwise <app>.<ip>.sslip.io
```

Then run `./install.sh`. It:

1. generates `DJANGO_SECRET_KEY`, the platform database password, `DOCKRA_ENCRYPTION_KEY` and the admin password (only where the placeholder is still set);
2. detects host CPU, memory and disk and writes them to `.env`;
3. fills `API_DOMAIN` and `PANEL_DOMAIN` with `api.` and `panel.` on the apps domain (or sslip.io) if empty;
4. writes Docker Hub mirrors from `DOCKRA_DOCKER_MIRRORS` to `/etc/docker/daemon.json` (restarting Docker only if they changed);
5. creates `/var/lib/dockra`, builds the images, runs migrations once, and starts every service.

The admin password is printed and stored in `deploy/.admin-password`.

## First sign-in

1. Open `http://<ip>:3000`, sign in as `admin`.
2. Delete `deploy/.admin-password`.
3. Enable two-factor authentication in Settings.
4. Create one account per person with the right role ([security](security.md)).
5. Share the CLI install command shown on the panel's Docs page.

## Domains and HTTPS for the panel and API

1. Point A records for `API_DOMAIN` and `PANEL_DOMAIN` (or a wildcard for the apps domain) at the server.
2. Wait for the certificates, or provide your own (below).
3. In `.env`:
   ```bash
   API_PUBLIC_URL=https://api.apps.example.com
   PANEL_ORIGIN=https://panel.apps.example.com
   API_BIND=127.0.0.1
   PANEL_BIND=127.0.0.1
   ```
4. Re-run `./install.sh` (the panel is rebuilt because `API_PUBLIC_URL` is compiled into it).
5. Developers run `dockra login --api https://api.apps.example.com` once.

If another service owns ports 80/443, add `-f docker-compose.small.yml`: Traefik moves to 8080/8443 with a smaller resource share. HTTP-01 challenges then need a front proxy or your own certificates.

## Updating

```bash
cd dockra && git pull && cd deploy && ./install.sh
```

`install.sh` is idempotent and keeps `.env`. Images are rebuilt and migrations applied once before services restart; app and database containers are not touched, so apps keep serving. Without repository access on the server, build a tarball with `git archive` and run `deploy/update.sh <bundle>`, or use `deploy/push.ps1 -Server <host>` from Windows. Take a manual backup before large upgrades.

## Background tasks

| Every | Task |
| --- | --- |
| 15 s | service heartbeats and mutual checks |
| 30 s | fail jobs whose worker is gone; reattach Traefik to app networks |
| 1 min | usage samples, alert evaluation, idle database admin UIs closed |
| 10 min | due automatic database and disk backups |
| 1 h | cleanup (unused blobs, old logs, expired login tickets, jobs older than 30 days); daily platform database backup |

## Data locations

| Path | Contents |
| --- | --- |
| `/var/lib/dockra/images/*.img` | app and database disks (ext4 images) |
| `/var/lib/dockra/mnt/<name>/data` | their mount points (also in `/etc/fstab`) |
| `/var/lib/dockra/media/backups/` | database backups; `panel/` holds the platform's own daily backups |
| `/var/lib/dockra/blobs/` | uploaded deployment files (content-addressed) |
| `/var/lib/dockra/work/mirror/` | latest file tree of each app |
| `/var/lib/dockra/logs/<app>/` | archived logs of previous releases |
| `/var/lib/dockra/certs/` | your own certificates for Traefik |
| `/var/lib/dockra/cli/` | prebuilt CLI binaries |
| volume `pgdata` | the platform database |
| volume `traefik-certs` | Let's Encrypt certificates |

## Backups of the platform itself

The worker dumps the platform database daily into `media/backups/panel/` (and to S3 when configured). To recover a server: install Dockra on the new host with the **same** `.env` (the encryption key must match), restore the latest panel dump into the `db` service, copy `/var/lib/dockra` if you have it, then start apps and databases from the panel — containers are recreated with their stored settings and passwords.

## Restricted or air-gapped networks

Dockra works without public internet:

- **Images:** while online, run `deploy/offline.sh pull` and `offline.sh save dockra-images.tar.gz`; on the target run `offline.sh load dockra-images.tar.gz`. `offline.sh check` lists missing images. Extend the list with `DOCKRA_EXTRA_IMAGES`.
- **Registries and packages:** set `DOCKRA_DOCKER_MIRRORS`, `DOCKRA_PIP_INDEX_URL` (plus `DOCKRA_PIP_FALLBACK_MIRROR`), `DOCKRA_NPM_REGISTRY` and `DOCKRA_APT_MIRROR` to your internal mirrors. The BuildKit cache (`DOCKRA_BUILD_CACHE_HOURS`) also serves previously downloaded packages.
- **DNS:** set `DOCKRA_APPS_DOMAIN` to an internal wildcard domain (sslip.io is a public resolver) and `DOCKRA_DNS_SERVERS` to your resolvers.
- **TLS:** set `DOCKRA_TLS_RESOLVER=` (empty) and put certificates (`*.pem`) and a Traefik dynamic config (`*.yml`) in `/var/lib/dockra/certs/`.
- **Dockerfiles:** remove `# syntax=` lines, which make BuildKit fetch a frontend image.

`deploy/e2e.sh` includes a simulated isolated network to keep this path working.

## Uninstall

**This deletes every app, database and backup on the server.**

```bash
cd dockra/deploy
docker compose down -v --remove-orphans
docker ps -aq --filter label=dockra.role=app | xargs -r docker rm -f
docker ps -aq --filter name=dockra-db- | xargs -r docker rm -f
docker network rm dockra
for m in /var/lib/dockra/mnt/*; do sudo umount "$m" 2>/dev/null; done
sudo sed -i '\#/var/lib/dockra/images/#d' /etc/fstab
sudo rm -rf /var/lib/dockra
```
