#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required"
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  fi
# Insecure default values are replaced with random ones.
python3 - <<'PY'
from pathlib import Path
import secrets
path = Path(".env")
text = path.read_text()
text = text.replace("change-me-secret", secrets.token_urlsafe(48), 1)
text = text.replace("change-me-db", secrets.token_urlsafe(24), 1)
text = text.replace("change-me-encryption", secrets.token_urlsafe(32), 1)
if "DOCKRA_ADMIN_PASSWORD=change-me-admin" in text:
    # Fresh installs only; seed never changes the password of an existing admin user.
    password = secrets.token_urlsafe(12)
    text = text.replace("DOCKRA_ADMIN_PASSWORD=change-me-admin", f"DOCKRA_ADMIN_PASSWORD={password}", 1)
    Path(".admin-password").write_text(password + "\n")
path.write_text(text)
PY
if [ "$(id -u)" -eq 0 ]; then
  mkdir -p /var/lib/dockra
else
  sudo mkdir -p /var/lib/dockra
fi
cpu=$(nproc)
mem=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
disk=$(df -BG /var/lib/dockra | awk 'NR==2 {gsub(/G/,"",$2); print $2}')
python3 - "$cpu" "$mem" "$disk" <<'PY'
import sys
from pathlib import Path
cpu, mem, disk = sys.argv[1:4]
path = Path(".env")
text = path.read_text()
for key, value in (("DOCKRA_HOST_CPU", cpu), ("DOCKRA_HOST_MEMORY_MB", mem), ("DOCKRA_HOST_DISK_GB", disk)):
    needle = f"{key}=0"
    if needle in text:
        text = text.replace(needle, f"{key}={value}", 1)
    elif f"{key}=" not in text:
        text += f"\n{key}={value}\n"
path.write_text(text)
PY
ip=$(awk -F= '/^PUBLIC_IP=/{print $2}' .env)
apps_domain=$(awk -F= '/^DOCKRA_APPS_DOMAIN=/{print $2}' .env)
for key in API_DOMAIN PANEL_DOMAIN; do
  if ! grep -q "^$key=." .env; then
    name=$( [ "$key" = API_DOMAIN ] && echo api || echo panel )
    sed -i "/^$key=/d" .env
    if [ -n "$apps_domain" ]; then
      echo "$key=$name.$apps_domain" >> .env
    else
      echo "$key=$name.$ip.sslip.io" >> .env
    fi
  fi
done
# Isolated networks: Docker Hub mirrors from .env (DOCKRA_DOCKER_MIRRORS, space separated) are written to the daemon.
mirrors=$(awk -F= '/^DOCKRA_DOCKER_MIRRORS=/{print $2}' .env)
if [ -n "$mirrors" ]; then
  if [ "$(id -u)" -eq 0 ]; then ./offline.sh mirrors $mirrors; else sudo ./offline.sh mirrors $mirrors; fi
fi
if [ "$(id -u)" -eq 0 ]; then mkdir -p /var/lib/dockra/certs; else sudo mkdir -p /var/lib/dockra/certs; fi
# api, worker, builder and terminal share one image; building them in parallel on one tag would clash.
docker compose build api
docker compose build web
# Migrate once before services start; otherwise api, worker and builder would migrate concurrently.
docker compose up -d --wait db
docker compose run --rm --no-deps -T api python manage.py migrate --noinput < /dev/null
docker compose up -d --remove-orphans
echo "Panel: http://$ip:3000  API: http://$ip:8000  user: admin"
if [ -f .admin-password ]; then
  chmod 600 .admin-password
  echo "admin password: $(cat .admin-password)  (also stored in deploy/.admin-password; delete it after signing in)"
fi
