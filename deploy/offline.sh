#!/usr/bin/env bash
# Preparation for isolated networks: pull and keep every required image while the internet is reachable.
#
#   ./offline.sh pull                 pull every image (through daemon mirrors if configured)
#   ./offline.sh save dockra-images.tar.gz   save them into one file (for another host or a rainy day)
#   ./offline.sh load dockra-images.tar.gz   load them on a host without internet
#   ./offline.sh check                list images missing on this host
#   sudo ./offline.sh mirrors https://mirror.example.com   write a Docker Hub mirror into the daemon config
#
# Extend the list with DOCKRA_EXTRA_IMAGES (space separated), e.g. another python or postgres version.
set -euo pipefail

IMAGES=(
  # Platform infrastructure
  traefik:v3.6.1
  postgres:16
  python:3.12-slim
  docker:27-cli
  node:22-alpine
  # App build bases (default and common pythonVersion and nodeVersion)
  python:3.8-slim-bookworm
  python:3.9-slim-bookworm
  python:3.10-slim-bookworm
  python:3.11-slim-bookworm
  python:3.12-slim-bookworm
  python:3.13-slim-bookworm
  node:18-alpine
  node:20-alpine
  nginx:alpine
  # Databases at the default version, plus common versions so backups can be restored on the same version
  postgres:14
  postgres:15
  postgres:17
  mysql:8.0
  mariadb:11.4
  mongo:7
  redis:6.2
  redis:7.2
  redis:7.4
  # Database admin UIs (pgAdmin, phpMyAdmin, Mongo Express, Redis Commander)
  dpage/pgadmin4:9
  phpmyadmin:5
  mongo-express:1
  rediscommander/redis-commander:latest
)
# shellcheck disable=SC2206
IMAGES+=(${DOCKRA_EXTRA_IMAGES:-})

cmd=${1:-}
case "$cmd" in
  list)
    printf '%s\n' "${IMAGES[@]}"
    ;;
  pull)
    failed=()
    for image in "${IMAGES[@]}"; do
      echo "== $image"
      docker pull -q "$image" || failed+=("$image")
    done
    if [ ${#failed[@]} -gt 0 ]; then
      echo "not pulled: ${failed[*]}" >&2
      exit 1
    fi
    ;;
  save)
    out=${2:-dockra-images.tar.gz}
    docker save "${IMAGES[@]}" | gzip -1 > "$out"
    echo "saved $(du -h "$out" | cut -f1): $out"
    ;;
  load)
    in=${2:-dockra-images.tar.gz}
    gunzip -c "$in" | docker load
    ;;
  check)
    missing=0
    for image in "${IMAGES[@]}"; do
      if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "missing: $image"
        missing=1
      fi
    done
    [ "$missing" = 0 ] && echo "all images are present"
    exit "$missing"
    ;;
  mirrors)
    shift
    [ $# -gt 0 ] || { echo "usage: $0 mirrors <url> [url...]" >&2; exit 1; }
    # install.sh calls this on every update; restarting Docker interrupts every app and database,
    # so Docker is restarted only when the mirrors actually changed.
    changed=0
    python3 - "$@" <<'PY' || changed=$?
import json
import sys
from pathlib import Path

path = Path("/etc/docker/daemon.json")
config = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
print("registry-mirrors:", ", ".join(sys.argv[1:]))
if config.get("registry-mirrors") == sys.argv[1:]:
    sys.exit(10)
config["registry-mirrors"] = sys.argv[1:]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(config, indent=2) + "\n")
PY
    case "$changed" in
      0) systemctl restart docker ;;
      10) echo "Docker mirrors unchanged; not restarting Docker" ;;
      *) exit "$changed" ;;
    esac
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac
