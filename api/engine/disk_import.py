"""Import files into an app disk (e.g. media from another host): from a tar/zip archive, a download link, or directly over FTP.

FTP runs on the server itself, in parallel (no detour through a laptop), and is incremental: a file that already exists
with the same size is skipped, so a second run during the final cut-over only fetches new files.
"""

import ftplib
import os
import re
import shutil
import stat
import tarfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone as dt_timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from engine.dumps import DumpError, ensure_public

PROGRESS_SECONDS = 5
LIARA_WRAPPER = re.compile(r"[\w.-]+-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(-\d+)?")


class DiskImportError(RuntimeError):
    pass


def _inside(root: Path, rel: str) -> Path:
    parts = [part for part in PurePosixPath(rel.replace("\\", "/")).parts if part not in ("", ".", "/")]
    if not parts or ".." in parts:
        raise DiskImportError(f"Invalid path in archive: {rel}")
    target = root.joinpath(*parts)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise DiskImportError(f"Path outside the disk: {rel}")
    return target


def _keep_mode(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo:
    from engine.disk_backup import _keep_mode as keep

    return keep(member, path)


def _parts(name: str) -> list[str]:
    return [part for part in PurePosixPath(name.replace("\\", "/")).parts if part not in ("", ".", "/")]


def _wrapper(names: list[tuple[str, bool]], own: tuple[str, ...] = ()) -> str:
    """Provider disk backups often put everything in one folder (e.g. media-2026-10-14T16-08-14-…) and manual archives
    usually in a folder named after the disk (e.g. media/); return that folder name or empty."""
    tops = set()
    for name, is_dir in names:
        parts = _parts(name)
        if not parts:
            continue
        if len(parts) == 1 and not is_dir:
            return ""
        tops.add(parts[0])
    top = tops.pop() if len(tops) == 1 else ""
    return top if LIARA_WRAPPER.fullmatch(top) or (top and top in own) else ""


def _strip(name: str, wrapper: str) -> str:
    parts = _parts(name)
    return "/".join(parts[1:]) if wrapper and parts and parts[0] == wrapper else name


def import_archive(src: Path, root: Path, step, own: tuple[str, ...] = ()) -> dict:
    """Extract a tar (any compression) or zip into the disk root; files with the same name are replaced."""
    root = root.resolve()
    with src.open("rb") as handle:
        magic = handle.read(4)
    files = total = 0
    if magic == b"PK\x03\x04":
        with zipfile.ZipFile(src) as archive:
            infos = archive.infolist()
            need = sum(info.file_size for info in infos)
            _check_space(root, need)
            wrapper = _wrapper([(info.filename, info.is_dir()) for info in infos], own)
            if wrapper:
                step(f"Skipping top-level folder {wrapper}; its contents are extracted into the disk root")
            for info in infos:
                rel = _strip(info.filename, wrapper)
                if not _parts(rel):
                    continue
                target = _inside(root, rel)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer, 1 << 20)
                files += 1
                total += info.file_size
    else:
        try:
            with tarfile.open(src, "r:*") as archive:
                members = archive.getmembers()
                wrapper = _wrapper([(member.name, member.isdir()) for member in members], own)
                if wrapper:
                    step(f"Skipping top-level folder {wrapper}; its contents are extracted into the disk root")
                for member in members:
                    if wrapper:
                        rel = _strip(member.name, wrapper)
                        if not _parts(rel):
                            continue
                        link = _strip(member.linkname, wrapper) if member.islnk() else member.linkname
                        member = member.replace(name=rel, linkname=link, deep=False)
                    archive.extract(member, root, filter=_keep_mode, numeric_owner=True)
                    if member.isfile():
                        files += 1
                        total += member.size
        except tarfile.TarError as exc:
            raise DiskImportError(f"Invalid archive (tar, tar.gz or zip): {exc}") from exc
    step(f"Extracted {files} files ({total / 1_048_576:.1f} MB)")
    return {"files": files, "bytes": total, "skipped": 0, "failed": 0}


def _check_space(root: Path, need: int) -> None:
    free = shutil.disk_usage(root).free
    if need > free:
        raise DiskImportError(
            f"Not enough disk space: {need / 1e9:.2f} GB needed and {free / 1e9:.2f} GB free; grow the disk first"
        )


# ---- FTP ----


def parse_ftp(url: str) -> dict:
    parts = urlsplit(url)
    if parts.scheme not in ("ftp", "ftps") or not parts.hostname:
        raise DiskImportError("The FTP URL must look like ftp://user:pass@host/path or ftps://...")
    try:
        ensure_public(parts.hostname)
    except DumpError as exc:
        raise DiskImportError(str(exc)) from exc
    return {
        "tls": parts.scheme == "ftps",
        "host": parts.hostname,
        "port": parts.port or 21,
        "user": unquote(parts.username or "anonymous"),
        "password": unquote(parts.password or ""),
        "path": unquote(parts.path or "/") or "/",
    }


def _connect(cfg: dict) -> ftplib.FTP:
    if cfg["tls"]:
        ftp = ftplib.FTP_TLS(timeout=60)
        ftp.connect(cfg["host"], cfg["port"])
        try:
            ftp.auth()
        except ftplib.error_perm:
            # Some providers' "FTPS access" is plain FTP in practice and rejects AUTH (500); other connections fall back to plain too.
            ftp.close()
            cfg["tls"] = False
            cfg["plain_fallback"] = True
            return _connect(cfg)
        ftp.login(cfg["user"], cfg["password"])
        ftp.prot_p()
    else:
        ftp = ftplib.FTP(timeout=60)
        ftp.connect(cfg["host"], cfg["port"])
        ftp.login(cfg["user"], cfg["password"])
    ftp.set_pasv(True)
    return ftp


def _check_path(ftp: ftplib.FTP, path: str) -> None:
    """Report a missing path together with the root listing; on many hosts the FTP root is the disk itself, not the container mount path."""
    if path in ("", "/"):
        return
    try:
        ftp.cwd(path)
        ftp.cwd("/")
        return
    except ftplib.error_perm:
        pass
    try:
        names = [name for name, _facts in _entries(ftp, "/") if name not in (".", "..")]
    except (OSError, ftplib.Error):
        names = []
    shown = ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
    hint = f"; at the FTP root: {shown}" if names else ""
    raise DiskImportError(
        f"{path} does not exist on the FTP server. The FTP root (/) is usually the disk itself, not the path inside the container{hint}"
    )


def _parse_list_line(line: str):
    """Unix-style LIST, for servers without MLSD."""
    fields = line.split(None, 8)
    if len(fields) < 9 or fields[0][0] not in "-dl":
        return None
    name = fields[8]
    if fields[0][0] == "l":
        return None
    kind = "dir" if fields[0][0] == "d" else "file"
    try:
        size = int(fields[4])
    except ValueError:
        size = -1
    return name, {"type": kind, "size": str(size)}


def _entries(ftp: ftplib.FTP, path: str):
    try:
        return [(name, facts) for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"])]
    except ftplib.error_perm:
        lines = []
        ftp.retrlines(f"LIST {path}", lines.append)
        return [item for item in (_parse_list_line(line) for line in lines) if item]


def _walk(ftp: ftplib.FTP, base: str) -> list[tuple[str, int, float | None]]:
    """Every file as (relative path, size, mtime)."""
    out = []
    pending = [""]
    while pending:
        rel = pending.pop()
        remote = str(PurePosixPath(base) / rel) if rel else base
        for name, facts in _entries(ftp, remote):
            if name in (".", "..") or facts.get("type") in ("cdir", "pdir"):
                continue
            child = f"{rel}/{name}" if rel else name
            if facts.get("type") == "dir":
                pending.append(child)
            elif facts.get("type", "file") == "file":
                modify = facts.get("modify")
                mtime = None
                if modify:
                    try:
                        mtime = datetime.strptime(modify[:14], "%Y%m%d%H%M%S").replace(tzinfo=dt_timezone.utc).timestamp()
                    except ValueError:
                        mtime = None
                out.append((child, int(facts.get("size") or -1), mtime))
    return out


def probe_ftp(url: str) -> None:
    """Check the connection, login and path before emptying the disk (replace)."""
    cfg = parse_ftp(url)
    try:
        ftp = _connect(cfg)
    except (OSError, ftplib.Error) as exc:
        raise DiskImportError(f"FTP connection failed: {exc}") from exc
    try:
        _check_path(ftp, cfg["path"])
    finally:
        try:
            ftp.quit()
        except (OSError, ftplib.Error):
            pass


def import_ftp(url: str, root: Path, step, workers: int = 8) -> dict:
    cfg = parse_ftp(url)
    root = root.resolve()
    step(f"Connecting to {cfg['host']}…")
    try:
        lister = _connect(cfg)
    except (OSError, ftplib.Error) as exc:
        raise DiskImportError(f"FTP connection failed: {exc}") from exc
    if cfg.get("plain_fallback"):
        step("The FTP server does not support AUTH TLS; continuing without TLS")
    try:
        _check_path(lister, cfg["path"])
        files = _walk(lister, cfg["path"])
    finally:
        try:
            lister.quit()
        except (OSError, ftplib.Error):
            pass

    todo = []
    skipped = 0
    for rel, size, mtime in files:
        local = _inside(root, rel)
        try:
            info = local.stat()
            if stat.S_ISREG(info.st_mode) and size >= 0 and info.st_size == size and (mtime is None or info.st_mtime >= mtime - 2):
                skipped += 1
                continue
        except FileNotFoundError:
            pass
        todo.append((rel, size, mtime, local))
    need = sum(max(size, 0) for _rel, size, _m, _l in todo)
    step(f"Found {len(files)} files; {len(todo)} files ({need / 1_048_576:.1f} MB) to transfer, {skipped} already present")
    if not todo:
        return {"files": 0, "bytes": 0, "skipped": skipped, "failed": 0}
    _check_space(root, need)

    local_state = threading.local()
    lock = threading.Lock()
    done = {"files": 0, "bytes": 0, "last": time.monotonic()}

    def connection():
        if getattr(local_state, "ftp", None) is None:
            local_state.ftp = _connect(cfg)
        return local_state.ftp

    def fetch(item):
        rel, size, mtime, local = item
        remote = str(PurePosixPath(cfg["path"]) / rel)
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(f".{local.name}.dockra-part")
        last_error = None
        for _attempt in range(3):
            try:
                with part.open("wb") as handle:
                    connection().retrbinary(f"RETR {remote}", handle.write, blocksize=1 << 16)
                os.replace(part, local)
                if mtime:
                    os.utime(local, (mtime, mtime))
                with lock:
                    done["files"] += 1
                    done["bytes"] += max(size, 0)
                    now = time.monotonic()
                    if now - done["last"] >= PROGRESS_SECONDS:
                        done["last"] = now
                        step(f"  {done['files']}/{len(todo)} files, {done['bytes'] / 1_048_576:.1f}/{need / 1_048_576:.1f} MB")
                return None
            except (OSError, EOFError, ftplib.Error) as exc:
                last_error = exc
                ftp = getattr(local_state, "ftp", None)
                local_state.ftp = None
                if ftp is not None:
                    try:
                        ftp.close()
                    except OSError:
                        pass
        part.unlink(missing_ok=True)
        return f"{rel}: {last_error}"

    failed = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as pool:
        for future in as_completed([pool.submit(fetch, item) for item in todo]):
            error = future.result()
            if error:
                failed.append(error)
    seconds = max(time.monotonic() - started, 0.1)
    step(f"Transferred {done['files']} files ({done['bytes'] / 1_048_576:.1f} MB) in {seconds:.0f} s ({done['bytes'] / 1_048_576 / seconds:.1f} MB/s)")
    for line in failed[:5]:
        step(f"warning: {line[:300]}")
    if failed and not done["files"]:
        raise DiskImportError(f"No files were transferred; {len(failed)} errors")
    if len(failed) > 5:
        step(f"… and {len(failed) - 5} more files failed; run again to fetch only those")
    return {"files": done["files"], "bytes": done["bytes"], "skipped": skipped, "failed": len(failed)}


def clear(root: Path) -> None:
    for item in root.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
