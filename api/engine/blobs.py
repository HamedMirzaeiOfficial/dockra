"""Content-addressed store of project files, so each deployment only uploads changed files.

Each file is stored once under the sha256 of its content. The file list of a deployment (the tree) is itself
a blob. For every app the server keeps a mirror of the latest tree so the next build
only writes the files that changed.
"""

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
from pathlib import Path, PurePosixPath

from django.conf import settings
from config.i18n import tr

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILES = 50_000


class TreeError(ValueError):
    pass


def root() -> Path:
    from engine.volumes import data_root

    path = data_root() / "blobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def blob_path(digest: str) -> Path:
    return root() / digest[:2] / digest


def has(digest: str) -> bool:
    return blob_path(digest).is_file()


def _clean_path(raw) -> str:
    text = str(raw or "").replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in ("", ".", "..") for part in text.split("/")):
        raise TreeError(tr("Invalid path: {path}", path=text))
    return path.as_posix()


def normalize_tree(files) -> list[dict]:
    if not isinstance(files, list) or not files:
        raise TreeError(tr("The file list is empty"))
    if len(files) > MAX_FILES:
        raise TreeError(tr("More than {max_files} files", max_files=MAX_FILES))
    seen = set()
    out = []
    for item in files:
        if not isinstance(item, dict):
            raise TreeError(tr("Invalid file list"))
        path = _clean_path(item.get("path"))
        digest = str(item.get("hash") or "").lower()
        if not HASH_RE.match(digest):
            raise TreeError(tr("Invalid hash for {path}", path=path))
        if path in seen:
            raise TreeError(tr("Duplicate path: {path}", path=path))
        seen.add(path)
        mode = 0o755 if int(item.get("mode") or 0) & 0o111 else 0o644
        out.append({"path": path, "hash": digest, "size": int(item.get("size") or 0), "mode": mode})
    out.sort(key=lambda row: row["path"])
    return out


def missing(files: list[dict]) -> list[str]:
    absent = set()
    for digest in {row["hash"] for row in files}:
        path = blob_path(digest)
        try:
            # Refresh the mtime so gc does not remove it before the deploy request arrives.
            os.utime(path)
        except FileNotFoundError:
            absent.add(digest)
    return sorted(absent)


def _write_blob(digest: str, data_iter) -> None:
    dest = blob_path(digest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{digest}.{os.getpid()}.tmp")
    sha = hashlib.sha256()
    with tmp.open("wb") as handle:
        for chunk in data_iter:
            sha.update(chunk)
            handle.write(chunk)
    if sha.hexdigest() != digest:
        tmp.unlink(missing_ok=True)
        raise TreeError(tr("File content does not match hash {value}", value=digest[:12]))
    os.replace(tmp, dest)


def store_tar(stream) -> int:
    """Read a tar (compressed or not) from a stream whose member names are the hash of their content."""
    stored = 0
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            if not member.isfile():
                continue
            digest = member.name.rsplit("/", 1)[-1].lower()
            if not HASH_RE.match(digest):
                raise TreeError(tr("Invalid member name: {name}", name=member.name))
            handle = archive.extractfile(member)
            if has(digest):
                continue
            _write_blob(digest, iter(lambda h=handle: h.read(1024 * 1024), b""))
            stored += 1
    return stored


def save_tree(files: list[dict]) -> str:
    body = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(body).hexdigest()
    if not has(digest):
        _write_blob(digest, [body])
    return digest


def load_tree(digest: str) -> list[dict]:
    return json.loads(blob_path(digest).read_bytes())


def _mirror_dir(slug: str) -> Path:
    return Path(settings.WORK_ROOT) / "mirror" / slug


def _state_path(slug: str) -> Path:
    return Path(settings.WORK_ROOT) / "mirror" / f"{slug}.state.json"


def _clear(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def sync_mirror(slug: str, files: list[dict]) -> tuple[Path, int]:
    """Bring the app mirror in line with the tree and return the number of files written."""
    mirror = _mirror_dir(slug)
    state_file = _state_path(slug)
    state: dict = {}
    if state_file.exists() and mirror.is_dir():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            state = {}
    if not state:
        shutil.rmtree(mirror, ignore_errors=True)
    # If interrupted midway, the mirror is rebuilt from scratch next time.
    state_file.unlink(missing_ok=True)
    mirror.mkdir(parents=True, exist_ok=True)
    wanted = {row["path"]: row for row in files}
    for path in sorted(set(state) - set(wanted), reverse=True):
        target = mirror / path
        if target.is_file() or target.is_symlink():
            target.unlink()
        parent = target.parent
        while parent != mirror and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    written = 0
    for path, row in wanted.items():
        if state.get(path) == [row["hash"], row["mode"]]:
            continue
        target = mirror / path
        for parent in reversed(target.relative_to(mirror).parents[:-1]):
            node = mirror / parent
            if node.exists() and not node.is_dir():
                node.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            _clear(target)
        shutil.copyfile(blob_path(row["hash"]), target)
        os.chmod(target, row["mode"])
        written += 1
    state = {path: [row["hash"], row["mode"]] for path, row in wanted.items()}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return mirror, written


def link_tree(src: Path, dest: Path) -> None:
    """Cheap folder copy with hardlinks; falls back to a regular copy."""
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        for name in dirnames:
            (dest / rel / name).mkdir(exist_ok=True)
        for name in filenames:
            source = Path(dirpath) / name
            target = dest / rel / name
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)


def gc(keep_deploys: int | None = None, min_age_seconds: int = 86_400) -> int:
    """Remove blobs that none of the recent deployments of any app still need."""
    from apps_core.models import App, Deployment

    keep = keep_deploys or settings.DOCKRA_BLOB_KEEP_DEPLOYS
    referenced: set[str] = set()
    for app in App.objects.all():
        trees = app.deployments.exclude(tree="").order_by("-number").values_list("tree", flat=True)[:keep]
        for digest in trees:
            referenced.add(digest)
            if has(digest):
                referenced.update(row["hash"] for row in load_tree(digest))
    pending = Deployment.objects.filter(status__in=["queued", "building"]).exclude(tree="")
    for digest in pending.values_list("tree", flat=True):
        referenced.add(digest)
        if has(digest):
            referenced.update(row["hash"] for row in load_tree(digest))
    cutoff = time.time() - min_age_seconds
    removed = 0
    for bucket in root().iterdir():
        if not bucket.is_dir():
            continue
        for blob in bucket.iterdir():
            if blob.name in referenced:
                continue
            try:
                if blob.stat().st_mtime < cutoff:
                    blob.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
