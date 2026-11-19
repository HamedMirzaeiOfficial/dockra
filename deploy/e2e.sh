#!/usr/bin/env bash
# End-to-end test on real Docker: install, deploy, zero-downtime redeploy, rollback, database and backup.
# Run on a machine with Docker and sudo (e.g. GitHub Actions). Everything is removed at the end.
set -euo pipefail
cd "$(dirname "$0")"
root=$(cd .. && pwd)
project=dockra-e2e
work=$(mktemp -d)
export HOME="$work/home"
mkdir -p "$HOME"
dockra() { python3 "$root/cli/dockra.py" "$@"; }
compose() { docker compose -p "$project" --env-file "$work/.env" -f docker-compose.yml "$@"; }
fail() { echo "E2E FAIL: $*" >&2; compose logs --tail 80 api worker builder >&2 || true; exit 1; }

nin_on() {
  # Simulated isolated network: the daemon cannot reach external registries or mirrors and containers have no internet.
  sudo cp /etc/hosts "$work/hosts.bak"
  for h in registry-1.docker.io auth.docker.io index.docker.io production.cloudflare.docker.com \
           pypi.org files.pythonhosted.org deb.debian.org registry.npmjs.org fonts.googleapis.com \
           fonts.gstatic.com acme-v02.api.letsencrypt.org dl-cdn.alpinelinux.org sslip.io; do
    echo "0.0.0.0 $h" | sudo tee -a /etc/hosts >/dev/null
  done
  sudo iptables -I DOCKER-USER -d 10.0.0.0/8 -j RETURN
  sudo iptables -I DOCKER-USER -d 172.16.0.0/12 -j RETURN
  sudo iptables -I DOCKER-USER -d 192.168.0.0/16 -j RETURN
  sudo iptables -A DOCKER-USER -j REJECT
  nin=1
}
nin_off() {
  [ "${nin:-0}" = 1 ] || return 0
  sudo cp "$work/hosts.bak" /etc/hosts
  sudo iptables -D DOCKER-USER -j REJECT || true
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do sudo iptables -D DOCKER-USER -d "$net" -j RETURN || true; done
  nin=0
}

cleanup() {
  nin_off
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker ps -aq --filter label=dockra.role=app | xargs -r docker rm -f >/dev/null 2>&1 || true
  docker ps -aq --filter name=dockra-db- | xargs -r docker rm -f >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

cat > "$work/.env" <<EOF
DJANGO_SECRET_KEY=e2e-$(openssl rand -hex 24)
POSTGRES_PASSWORD=e2e-$(openssl rand -hex 12)
DOCKRA_ENCRYPTION_KEY=e2e-$(openssl rand -hex 16)
PUBLIC_IP=127.0.0.1
ACME_EMAIL=e2e@example.com
API_DOMAIN=api.127.0.0.1.sslip.io
PANEL_DOMAIN=panel.127.0.0.1.sslip.io
API_PUBLIC_URL=http://127.0.0.1:8000
PANEL_ORIGIN=http://127.0.0.1:3000
DOCKRA_ADMIN_PASSWORD=e2e-admin-pass-42
DOCKRA_HOST_CPU=4
DOCKRA_HOST_MEMORY_MB=8192
DOCKRA_HOST_DISK_GB=60
DOCKRA_RESERVED_DISK_GB=5
DOCKRA_RESERVED_MEMORY_MB=1024
DOCKRA_AUTO_BACKUP_HOURS=0
EOF
sudo mkdir -p /var/lib/dockra

echo "== stack"
compose up -d --build traefik db api worker builder terminal
for _ in $(seq 60); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/api/health | grep -q '"driver":"docker"' || fail "API not healthy"

dockra login --api http://127.0.0.1:8000 --username admin --password e2e-admin-pass-42

echo "== sample app"
app="$work/app"
mkdir -p "$app/proj"
cat > "$app/requirements.txt" <<'EOF'
Django>=5.0,<5.3
EOF
cat > "$app/proj/__init__.py" <<'EOF'
EOF
cat > "$app/proj/settings.py" <<'EOF'
SECRET_KEY = "e2e"
DEBUG = False
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "proj.urls"
INSTALLED_APPS = []
DATABASES = {}
EOF
write_urls() {
  cat > "$app/proj/urls.py" <<EOF
from django.http import HttpResponse
from django.urls import path

urlpatterns = [path("", lambda request: HttpResponse("$1"))]
EOF
}
write_urls v1
cat > "$app/proj/wsgi.py" <<'EOF'
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")
application = get_wsgi_application()
EOF
cat > "$app/dockra.json" <<'EOF'
{"app": "e2e", "platform": "django", "port": 8000, "release": "echo release-ran", "healthCheck": {"path": "/"}}
EOF

host="e2e.127.0.0.1.sslip.io"
hit() { curl -s -m 3 -H "Host: $host" http://127.0.0.1/ || true; }

cd "$app"
dockra deploy --message first | tee "$work/d1.log"
grep -q "release-ran" "$work/d1.log" || fail "release command did not run"
[ "$(hit)" = "v1" ] || fail "v1 not served: $(hit)"

echo "== incremental + zero downtime"
write_urls v2
( misses=0; for _ in $(seq 400); do r=$(hit); [ "$r" = v1 ] || [ "$r" = v2 ] || misses=$((misses+1)); sleep 0.1; done; echo "$misses" > "$work/misses" ) &
watcher=$!
dockra deploy --message second | tee "$work/d2.log"
grep -q "changed: 1 files" "$work/d2.log" || fail "delta upload sent more than the changed file"
wait "$watcher" || true
[ "$(hit)" = "v2" ] || fail "v2 not served"
misses=$(cat "$work/misses")
echo "failed requests during deploy: $misses"
[ "$misses" -le 2 ] || fail "downtime during deploy ($misses failed requests)"

echo "== logs survive redeploy"
dockra exec -- echo hello-from-container | grep -q hello-from-container || fail "exec"
docker run --rm -v /var/lib/dockra:/var/lib/dockra alpine sh -c 'ls /var/lib/dockra/logs/e2e/*.log.gz' >/dev/null || fail "old release logs were not archived"

echo "== rollback"
dockra rollback v1
[ "$(hit)" = "v1" ] || fail "rollback did not serve v1"

echo "== broken release keeps old version"
write_urls v3
sed -i 's/"release": "echo release-ran"/"release": "exit 7"/' dockra.json
if dockra deploy --message broken; then fail "broken release should fail"; fi
[ "$(hit)" = "v1" ] || fail "broken release took the app down"
sed -i 's/"release": "exit 7"/"release": "echo release-ran"/' dockra.json

echo "== national internet simulation"
nin_on
write_urls v4
dockra deploy --message offline | tee "$work/d4.log" || fail "deploy without international internet"
[ "$(hit)" = "v4" ] || fail "offline deploy not served"
token=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.dockra.json')))['token'])")
curl -fsS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
  -d '{"name":"media","mount_to":"/data","size_gb":1}' http://127.0.0.1:8000/api/apps/e2e/disks >/dev/null \
  || fail "disk creation needs the internet"
dockra db create e2e-offline --engine postgres || fail "database creation needs the internet"
nin_off

echo "== database"
dockra db create e2e-db --engine postgres
dockra db link e2e-db

echo "== network isolation"
probe() { dockra exec -- python -c "import socket; socket.create_connection(('$1', $2), 3)" >/dev/null 2>&1; }
# link recreates the app in the background; wait until it is connected.
reached=0
for _ in $(seq 30); do probe dockra-db-e2e-db 5432 && { reached=1; break; }; sleep 2; done
[ "$reached" = 1 ] || fail "app cannot reach its linked database"
if probe db 5432; then fail "app can reach the panel database"; fi
if probe api 8000; then fail "app can reach the panel API service"; fi
if probe dockra-db-e2e-offline 5432; then fail "app can reach a database that is not linked to it"; fi
dockra db backup e2e-db
dockra db backups e2e-db | grep -q "Ready" || fail "backup not ready"
id=$(dockra db backups e2e-db | awk 'NR==1{print $1}')
dockra db restore e2e-db --backup "$id" -y

echo "E2E OK"
