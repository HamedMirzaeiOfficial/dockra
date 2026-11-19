"""Backup and restore of app disk files (tar.gz of the volume data folder).

The worker mounts /var/lib/dockra with rslave, so it reads and writes mounted volumes directly.
"""

import os
import shutil
import tarfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps_core.models import App, Disk, DiskBackup


def backup_folder() -> Path:
    return Path(settings.MEDIA_ROOT) / "backups" / "disks"


def disk_data(disk: Disk) -> Path:
    from engine.volumes import volume_path

    return volume_path(f"dockra-disk-{disk.app.slug}-{disk.name}", disk.size_gb)


def _used_bytes(root: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                continue
    return total


def _pack(src: Path, dest: Path) -> None:
    """Files deleted during the backup (the app keeps running) are skipped instead of failing the backup."""
    with tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        for folder, dirs, files in os.walk(src):
            dirs.sort()
            for name in dirs + sorted(files):
                full = os.path.join(folder, name)
                try:
                    tar.add(full, arcname=os.path.relpath(full, src), recursive=False)
                except FileNotFoundError:
                    continue


def _keep_mode(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
    # tar_filter rejects paths outside the disk and absolute links but strips group/other permissions;
    # restore the original mode (without setuid/setgid) so the app can still write after a restore.
    safe = tarfile.tar_filter(member, path)
    if member.mode is None:
        return safe
    return safe.replace(mode=member.mode & 0o1777, deep=False)


def _unpack(src: Path, dest: Path) -> None:
    with tarfile.open(src, "r:gz") as tar:
        tar.extractall(dest, filter=_keep_mode, numeric_owner=True)


def make_backup(backup: DiskBackup) -> None:
    """Create the backup and mark the row READY; raises on failure."""
    disk = backup.disk
    if disk is None:
        raise RuntimeError("The disk of this backup has been deleted")
    src = disk_data(disk)
    folder = backup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    # A backup must not fill the host disk and take apps and databases down with it.
    reserve = int(settings.DOCKRA_RESERVED_DISK_GB) * (1024 ** 3)
    need = _used_bytes(src)
    free = shutil.disk_usage(folder).free
    if need > free - reserve:
        raise RuntimeError(f"Not enough free space on the server to back up disk {disk.name}")
    dest = folder / f"{disk.app.slug}-{disk.name}-{backup.id}.tar.gz"
    try:
        _pack(src, dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    backup.status = DiskBackup.Status.READY
    backup.size_bytes = dest.stat().st_size
    backup.path = str(dest)
    backup.finished_at = timezone.now()
    backup.save(update_fields=["status", "size_bytes", "path", "finished_at"])


def delete_backup(backup: DiskBackup) -> None:
    if backup.path:
        Path(backup.path).unlink(missing_ok=True)
    backup.delete()


def backup_hours(disk: Disk) -> int:
    return settings.DOCKRA_DISK_BACKUP_HOURS if disk.backup_hours is None else disk.backup_hours


def backup_keep(disk: Disk | None) -> int:
    if disk is None or disk.backup_keep is None:
        return max(1, settings.DOCKRA_DISK_BACKUP_KEEP)
    return max(1, disk.backup_keep)


def prune(app: App, disk_name: str) -> None:
    """After a successful backup, keep the newest ready backups (automatic and manual together, "before restore" separately)
    and delete older failed ones so the host disk does not fill up."""
    disk = Disk.objects.filter(app=app, name=disk_name).first()
    keep = backup_keep(disk)
    rows = DiskBackup.objects.filter(app=app, disk_name=disk_name)
    ready = rows.filter(status=DiskBackup.Status.READY)
    for kinds in ((DiskBackup.Kind.AUTO, DiskBackup.Kind.MANUAL), (DiskBackup.Kind.SAFETY,)):
        for old in ready.filter(kind__in=kinds).order_by("-created_at")[keep:]:
            delete_backup(old)
    newest = ready.order_by("-created_at").first()
    if newest is not None:
        for failed in rows.filter(status=DiskBackup.Status.FAILED, created_at__lt=newest.created_at):
            delete_backup(failed)


def restore(backup: DiskBackup, disk: Disk) -> DiskBackup:
    """Replace the disk contents with a backup. A "before restore" backup of the current state is taken first
    and the current files are not removed until the restore has completed."""
    src = Path(backup.path)
    if backup.status != DiskBackup.Status.READY or not src.is_file():
        raise RuntimeError("The file of this backup is not on the server")
    safety = DiskBackup.objects.create(app=disk.app, disk=disk, disk_name=disk.name, kind=DiskBackup.Kind.SAFETY)
    try:
        make_backup(safety)
    except Exception as exc:
        safety.status = DiskBackup.Status.FAILED
        safety.error = str(exc)[-2000:]
        safety.finished_at = timezone.now()
        safety.save(update_fields=["status", "error", "finished_at"])
        raise RuntimeError(f"Could not take the pre-restore backup; the disk was not touched\n{exc}") from exc
    root = disk_data(disk)
    incoming = root / f".dockra-restore-{backup.id}"
    old = root / f".dockra-old-{backup.id}"
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir()
    try:
        _unpack(src, incoming)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    # The move happens on the same filesystem; if it fails midway the previous files are in .dockra-old and the safety backup.
    old.mkdir(exist_ok=True)
    for item in root.iterdir():
        if item not in (incoming, old):
            item.rename(old / item.name)
    for item in incoming.iterdir():
        item.rename(root / item.name)
    incoming.rmdir()
    shutil.rmtree(old, ignore_errors=True)
    prune(disk.app, disk.name)
    return safety


def schedule_auto() -> int:
    from engine.actions import backup_disk

    now = timezone.now()
    retry = now - timedelta(minutes=settings.DOCKRA_BACKUP_RETRY_MINUTES)
    count = 0
    for disk in Disk.objects.select_related("app"):
        hours = backup_hours(disk)
        if hours <= 0:
            continue
        rows = DiskBackup.objects.filter(app=disk.app, disk_name=disk.name)
        if rows.filter(status=DiskBackup.Status.RUNNING, created_at__gte=now - timedelta(hours=6)).exists():
            continue
        # One successful backup (automatic or manual) per cycle; a failed one is retried after a short delay.
        fresh = rows.filter(status=DiskBackup.Status.READY, kind__in=[DiskBackup.Kind.AUTO, DiskBackup.Kind.MANUAL])
        if fresh.filter(created_at__gte=now - timedelta(hours=hours)).exists():
            continue
        if rows.filter(kind=DiskBackup.Kind.AUTO, status=DiskBackup.Status.FAILED, created_at__gte=retry).exists():
            continue
        backup = DiskBackup.objects.create(app=disk.app, disk=disk, disk_name=disk.name, kind=DiskBackup.Kind.AUTO)
        backup_disk(backup, None)
        count += 1
    return count


def cleanup_orphans() -> None:
    cutoff = timezone.now() - timedelta(days=settings.DOCKRA_ORPHAN_BACKUP_DAYS)
    for backup in DiskBackup.objects.filter(disk__isnull=True, created_at__lt=cutoff):
        delete_backup(backup)
