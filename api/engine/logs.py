"""Logs of containers removed by the next deployment are kept on disk for a while."""

import gzip
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.log\.gz$")


def app_dir(slug: str) -> Path:
    from engine.volumes import data_root

    return data_root() / "logs" / slug


def archive_container(slug: str, cid: str, release: str) -> Path | None:
    """Store the full container log compressed before removal. Failure never breaks the deployment."""
    folder = app_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_release = re.sub(r"[^A-Za-z0-9_-]", "", release or "old")[:20] or "old"
    dest = folder / f"{stamp}-{safe_release}-{cid[:12]}.log.gz"
    proc = None
    try:
        # Output goes through Python to be compressed; GzipFile as stdout would write to the raw fd and corrupt the file.
        proc = subprocess.Popen(["docker", "logs", "--timestamps", cid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        timer = threading.Timer(120, proc.kill)
        timer.start()
        try:
            with gzip.open(dest, "wb", compresslevel=6) as handle:
                shutil.copyfileobj(proc.stdout, handle, 1 << 16)
            code = proc.wait()
        finally:
            timer.cancel()
        if code != 0 or dest.stat().st_size <= 20:
            dest.unlink(missing_ok=True)
            return None
        return dest
    except (OSError, subprocess.SubprocessError):
        if proc is not None:
            proc.kill()
        dest.unlink(missing_ok=True)
        return None


def list_archive(slug: str) -> list[dict]:
    folder = app_dir(slug)
    if not folder.is_dir():
        return []
    rows = []
    for item in sorted(folder.glob("*.log.gz"), reverse=True):
        parts = item.name[: -len(".log.gz")].split("-")
        info = item.stat()
        rows.append(
            {
                "name": item.name,
                "release": parts[1] if len(parts) > 2 else "",
                "ended_at": datetime.fromtimestamp(info.st_mtime, tz=dt_timezone.utc).isoformat(),
                "size": info.st_size,
            }
        )
    return rows


def archive_path(slug: str, name: str) -> Path | None:
    if not NAME_RE.match(name or ""):
        return None
    path = app_dir(slug) / name
    return path if path.is_file() else None


def _legacy_text(path: Path) -> str | None:
    """Legacy archives: raw text followed by an empty gzip member (old archive_container). Returns the text."""
    with path.open("rb") as handle:
        if handle.read(2) == b"\x1f\x8b":
            return None
    data = path.read_bytes()
    cut = data.rfind(b"\x1f\x8b\x08")
    return data[:cut if cut >= 0 else len(data)].decode("utf-8", "replace")


def iter_lines(path: Path):
    text = _legacy_text(path)
    if text is not None:
        for line in text.splitlines():
            yield line
        return
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line.rstrip("\n")
    except (OSError, EOFError, gzip.BadGzipFile):
        # Truncated file (e.g. the worker died while writing): what was read is enough; the response must not break midway.
        yield "[dockra] the rest of this log file is corrupt or truncated"


def search(slug: str, query: str, current: str, limit: int = 500) -> dict:
    """Simple case-insensitive search, first in the current log and then in older archives."""
    needle = query.lower()
    matches = []
    for line in current.splitlines():
        if needle in line.lower():
            matches.append({"source": "current", "line": line})
            if len(matches) >= limit:
                return {"matches": matches, "truncated": True}
    for row in list_archive(slug):
        path = archive_path(slug, row["name"])
        if path is None:
            continue
        try:
            for line in iter_lines(path):
                if needle in line.lower():
                    matches.append({"source": row["name"], "line": line})
                    if len(matches) >= limit:
                        return {"matches": matches, "truncated": True}
        except OSError:
            # The file was pruned during the search or is unreadable; keep searching the other archives.
            continue
    return {"matches": matches, "truncated": False}


def prune() -> int:
    """Delete logs older than DOCKRA_LOG_DAYS and beyond DOCKRA_LOG_KEEP files per app."""
    from engine.volumes import data_root

    root = data_root() / "logs"
    if not root.is_dir():
        return 0
    cutoff = time.time() - settings.DOCKRA_LOG_DAYS * 86_400
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        files = sorted(folder.glob("*.log.gz"), reverse=True)
        for index, item in enumerate(files):
            if index >= settings.DOCKRA_LOG_KEEP or item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
                removed += 1
    return removed


def remove_app(slug: str) -> None:
    shutil.rmtree(app_dir(slug), ignore_errors=True)
