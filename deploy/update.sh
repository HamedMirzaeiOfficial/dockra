#!/usr/bin/env bash
# Install a new code version from a tar.gz bundle (git archive output) onto the install folder and run install.sh.
# The folder is updated in place (not swapped) so bind mounts of running containers stay valid.
# Server-local files (.env, overrides, admin password) are never touched.
set -euo pipefail
archive=${1:?usage: update.sh <release.tar.gz> [commit]}
commit=${2:-}
root=${DOCKRA_ROOT:-/opt/dockra}
keep='^(\.release|deploy/\.env|deploy/\.admin-password|deploy/docker-compose\.override\.yml)$'

mkdir -p "$root"
new=$(mktemp)
cur=$(mktemp)
trap 'rm -f "$new" "$cur"' EXIT
tar -tzf "$archive" | grep -v '/$' | sed 's#^\./##' | sort -u > "$new"
(cd "$root" && find . -type f | sed 's#^\./##' | grep -Ev "$keep" | sort -u) > "$cur"
comm -23 "$cur" "$new" | (cd "$root" && xargs -r -d '\n' rm -f --)
tar -xzf "$archive" -C "$root"
find "$root" -mindepth 1 -type d -empty -delete
rm -f "$archive"
if [ -n "$commit" ]; then
  echo "$commit" > "$root/.release"
fi

cd "$root/deploy"
chmod +x ./*.sh
./install.sh
echo "Version on the server: ${commit:-unknown}"
