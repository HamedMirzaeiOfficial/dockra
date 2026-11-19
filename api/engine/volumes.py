import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

from engine.resources import QuotaExceeded

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")


def data_root() -> Path:
    raw = getattr(settings, "DOCKRA_DATA_ROOT", "") or ""
    path = Path(raw) if raw else Path(settings.BASE_DIR) / "var"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check_name(name: str) -> str:
    if not _SAFE.match(name or ""):
        raise RuntimeError("Invalid volume name")
    return name


def _docker(args: list[str], check: bool = True, script: str = "") -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(["docker", *args], input=script.encode() if script else None, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found in PATH") from exc
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(err or "Creating the volume failed")
    return proc


def _host_script(script: str) -> None:
    """The script runs on the host via nsenter and uses the host's own fallocate and mkfs.ext4.

    The helper container is the API image already present on the host; nothing is downloaded from the internet.
    """
    # -i is required: sh -s reads the script from stdin and docker does not forward it without -i.
    _docker(
        ["run", "--rm", "-i", "--privileged", "--pid=host", settings.DOCKRA_TOOLS_IMAGE,
         "nsenter", "-t", "1", "-a", "--", "sh", "-s"],
        script=script,
    )


def ensure_volume(name: str, size_gb: int) -> str:
    name = _check_name(name)
    size_gb = max(int(size_gb), 1)
    root = data_root()
    image = root / "images" / f"{name}.img"
    mount = root / "mnt" / name
    data = mount / "data"
    if settings.DOCKRA_DRIVER != "docker":
        data.mkdir(parents=True, exist_ok=True)
        return str(data)
    image.parent.mkdir(parents=True, exist_ok=True)
    mount.mkdir(parents=True, exist_ok=True)
    have = image.stat().st_size if image.exists() else 0
    need = size_gb * (1024 ** 3)
    if need > have:
        extra = need - have
        free = shutil.disk_usage(root).free
        reserve = int(settings.DOCKRA_RESERVED_DISK_GB) * (1024 ** 3)
        if extra > max(free - reserve, 0):
            raise QuotaExceeded("Not enough space left on the host disk for this size")
    root_s = str(root)
    _host_script(
        f"""set -eu
img="{root_s}/images/{name}.img"
mnt="{root_s}/mnt/{name}"
mkdir -p "$(dirname "$img")" "$mnt"
if [ ! -f "$img" ]; then
  fallocate -l {size_gb}G "$img"
  mkfs.ext4 -F -q "$img"
fi
grep -q " $mnt " /proc/mounts || mount -o loop "$img" "$mnt"
# Grow in place (shrinking is not supported): enlarge the file, refresh the loop device and grow ext4 online.
if [ "$(stat -c %s "$img")" -lt {size_gb * 1024 ** 3} ]; then
  fallocate -l {size_gb}G "$img"
  dev=$(losetup -j "$img" | head -1 | cut -d: -f1)
  losetup -c "$dev"
  resize2fs "$dev" >/dev/null
fi
# First time only; never change permissions on a running database's data folder (e.g. PGDATA with 0700).
if [ ! -d "$mnt/data" ]; then
  mkdir "$mnt/data"
  chmod 1777 "$mnt/data"
fi
grep -q "$img" /etc/fstab || echo "$img $mnt ext4 loop,defaults 0 0" >> /etc/fstab
"""
    )
    return str(data)


def release_volume(name: str) -> None:
    name = _check_name(name)
    if settings.DOCKRA_DRIVER != "docker":
        return
    root = str(data_root())
    _host_script(
        f"""set -eu
img="{root}/images/{name}.img"
mnt="{root}/mnt/{name}"
if grep -q " $mnt " /proc/mounts; then umount "$mnt"; fi
rm -f "$img"
grep -vF "$img " /etc/fstab > /etc/fstab.dockra.tmp && cat /etc/fstab.dockra.tmp > /etc/fstab && rm -f /etc/fstab.dockra.tmp
rm -rf "$mnt"
"""
    )


def remount_all() -> None:
    if settings.DOCKRA_DRIVER != "docker":
        return
    from apps_core.models import Disk
    from databases.models import Database

    for disk in Disk.objects.select_related("app"):
        ensure_volume(f"dockra-disk-{disk.app.slug}-{disk.name}", disk.size_gb)
    for database in Database.objects.all():
        ensure_volume(f"dockra-dbvol-{database.name}", database.disk_gb)


def volume_path(name: str, size_gb: int) -> Path:
    """Host path of the volume data; created and mounted if not mounted yet."""
    name = _check_name(name)
    data = data_root() / "mnt" / name / "data"
    if not data.is_dir():
        return Path(ensure_volume(name, size_gb))
    return data
