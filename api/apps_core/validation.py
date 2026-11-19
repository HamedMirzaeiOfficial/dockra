"""Shared disk checks for the API and the dockra.json / liara.json manifests.

On the host a volume is named `dockra-disk-<slug>-<name>`; since both slug and disk name may contain dashes, two
different apps could collide on the same name (shop + media-x and shop-media + x) and see each other's files. This prevents it.
"""

import posixpath
import re

from apps_core.models import App, Disk

DISK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,61}$")
# Mounting a disk on these paths hides the container's OS and the app cannot start.
SYSTEM_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/root",
    "/run", "/sbin", "/sys", "/usr", "/usr/bin", "/usr/lib", "/usr/local", "/var", "/var/lib",
}


class DiskError(ValueError):
    pass


def volume_name(slug: str, name: str) -> str:
    return f"dockra-disk-{slug}-{name}"


def clean_mount(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw.startswith("/") or "\x00" in raw or "\\" in raw:
        raise DiskError("The mount path must be an absolute path inside the container, e.g. /app/media")
    if ".." in raw.split("/"):
        raise DiskError("The mount path must not contain ..")
    mount = posixpath.normpath(raw)
    if mount in SYSTEM_PATHS:
        raise DiskError(f"{mount} is a system folder of the container; use a subfolder such as /app/media")
    if len(mount) > 200:
        raise DiskError("The mount path is too long")
    return mount


def check_disk(app: App, name: str, mount_to: str, disk: Disk | None = None) -> str:
    """Validate the name and path and return the cleaned path. `disk` is the disk being edited, if any."""
    if not DISK_NAME_RE.match(name or ""):
        raise DiskError("Disk name: letters, digits, dash, underscore or dot; at most 40 characters")
    mount = clean_mount(mount_to)
    others = app.disks.all() if disk is None else app.disks.exclude(pk=disk.pk)
    if disk is None and app.disks.filter(name=name).exists():
        raise DiskError(f"This app already has a disk named {name}; use resize to make it larger")
    owner = others.filter(mount_to=mount).first()
    if owner is not None:
        raise DiskError(f"{mount} is already used by disk {owner.name}")
    nested = next((item for item in others if mount.startswith(item.mount_to.rstrip("/") + "/") or item.mount_to.startswith(mount + "/")), None)
    if nested is not None:
        raise DiskError(f"{mount} is inside or above the path of disk {nested.name} ({nested.mount_to})")
    volume = volume_name(app.slug, name)
    if not VOLUME_RE.match(volume):
        raise DiskError("The app and disk names are too long together; choose a shorter disk name")
    if disk is None:
        for item in Disk.objects.exclude(app=app).select_related("app"):
            if volume_name(item.app.slug, item.name) == volume:
                raise DiskError("This name collides with a disk of another app on the server; choose a different disk name")
    return mount


# URL-safe characters only; backticks, $ and spaces have meaning in Traefik rules and replacements.
REDIRECT_PATH_RE = re.compile(r"^/[A-Za-z0-9._~%@:+,;=!*'()/-]{0,199}$")
REDIRECT_TARGET_RE = re.compile(
    r"^(?:https?://(?P<host>[a-z0-9.-]+)(?::\d{1,5})?)?(?P<path>/[A-Za-z0-9._~%@:+,;=!*'()/?&#\[\]-]*)?$"
)


class RedirectError(ValueError):
    pass


def check_redirect(app: App, data, redirect=None) -> dict:
    """Return the cleaned redirect fields or raise RedirectError."""
    from apps_core.models import Redirect

    hosts = set(app.domains.values_list("hostname", flat=True))
    source_host = str(data.get("source_host") or "").strip().lower().rstrip(".")
    if source_host and source_host not in hosts:
        raise RedirectError("The source domain must be one of this app's domains")
    match = str(data.get("match") or Redirect.Match.PREFIX)
    if match not in Redirect.Match.values:
        raise RedirectError("Match type must be prefix or exact")
    source_path = str(data.get("source_path") or "/").strip()
    if not REDIRECT_PATH_RE.match(source_path):
        raise RedirectError("The source path must start with / and contain only valid URL characters (no ? or #)")
    if match == Redirect.Match.PREFIX and source_path != "/":
        source_path = source_path.rstrip("/")
    target = str(data.get("target") or "").strip()
    found = REDIRECT_TARGET_RE.match(target) if len(target) <= 500 else None
    if not target or not found or not (found.group("host") or found.group("path")):
        raise RedirectError("The target must be a full URL (https://example.com/...) or a path on the same domain (/new)")

    sources = {source_host} if source_host else hosts
    target_host = found.group("host")
    if not target_host or target_host in sources:
        target_path = (found.group("path") or "/").split("?", 1)[0].split("#", 1)[0]
        if match == Redirect.Match.EXACT:
            loop = target_path == source_path
        else:
            loop = source_path == "/" or target_path == source_path or target_path.startswith(source_path + "/")
        if loop:
            raise RedirectError("This redirect points to itself and would loop")

    same = Redirect.objects.filter(app=app, source_host=source_host, source_path=source_path, match=match)
    if redirect is not None:
        same = same.exclude(pk=redirect.pk)
    if same.exists():
        raise RedirectError("A redirect already exists for this domain and path")
    return {
        "source_host": source_host,
        "source_path": source_path,
        "match": match,
        "target": target,
        "keep_path": bool(data.get("keep_path", True)),
        "permanent": bool(data.get("permanent", True)),
    }
