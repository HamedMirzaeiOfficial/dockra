"""Detect the format of a backup file (a provider export or any pg_dump/mysqldump/mongodump/RDB) and download it safely from a URL.

The engine and the minimum version needed to restore are guessed from the file header (after gunzip) so
`dockra db import` can create a matching database without asking.
"""

import gzip
import ipaddress
import re
import shutil
import socket
import struct
import tarfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HEAD_BYTES = 1 << 20
MONGO_MAGIC = b"\x6d\xe2\x99\x81"
DEFAULTS = {"postgres": "16", "mysql": "8.0", "mariadb": "11.4", "mongodb": "7", "redis": "7.4"}
RDB_MIN_REDIS = {10: "7.0", 11: "7.2", 12: "7.4", 13: "8.0"}


class DumpError(RuntimeError):
    pass


def is_gzip(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"\x1f\x8b"


def _raw_head(path: Path, size: int) -> bytes:
    if not is_gzip(path):
        with path.open("rb") as handle:
            return handle.read(size)
    out = b""
    try:
        with gzip.open(path, "rb") as handle:
            while len(out) < size:
                chunk = handle.read(size - len(out))
                if not chunk:
                    break
                out += chunk
    except (OSError, EOFError):
        if not out:
            raise DumpError("The gzip file is corrupt")
    return out


def _is_tar(data: bytes) -> bool:
    return data[257:262] == b"ustar"


def head(path: Path, size: int = HEAD_BYTES) -> bytes:
    """Beginning of the real file content; gzip and tar are unwrapped. A truncated gzip is read as far as possible."""
    out = _raw_head(path, size)
    if not _is_tar(out):
        return out
    try:
        with tarfile.open(path, "r|*") as tar:
            for member in tar:
                if member.isfile():
                    return tar.extractfile(member).read(size)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise DumpError(f"The tar archive is corrupt: {exc}") from exc
    raise DumpError("The tar archive is empty")


def unwrap(path: Path) -> None:
    """Some providers ship a tar.gz with a single dump inside (e.g. postgres.dump); the archive is replaced by that file."""
    if not _is_tar(_raw_head(path, 512)):
        return
    inner = path.with_name(path.name + ".inner")
    try:
        with tarfile.open(path, "r:*") as tar:
            files = [m for m in tar if m.isfile()]
            if len(files) != 1:
                raise DumpError("The tar archive must contain exactly one backup file")
            with tar.extractfile(files[0]) as reader, inner.open("wb") as writer:
                shutil.copyfileobj(reader, writer, 1 << 20)
    except (tarfile.TarError, OSError, EOFError) as exc:
        inner.unlink(missing_ok=True)
        raise DumpError(f"The tar archive is corrupt: {exc}") from exc
    except BaseException:
        inner.unlink(missing_ok=True)
        raise
    inner.replace(path)


def _postgres_major(data: bytes) -> int | None:
    found = [int(m) for m in re.findall(rb"(?:database|pg_dump|pg_dumpall) version (\d{1,2})", data[:65536])]
    if not found and data[:5] == b"PGDMP":
        # Custom-format header: database name, server version and pg_dump version as strings (e.g. "16.4 (Debian ...)").
        found = [int(m) for m in re.findall(rb"(?<![\d.])(\d{1,2})\.\d{1,2}(?=[ .)\x00-\x1f]|$)", data[:4096])]
    found = [major for major in found if 9 <= major <= 40]
    return max(found) if found else None


def _pg_version(major: int | None) -> str:
    if major is None or major < 12:
        return DEFAULTS["postgres"]
    return str(major)


def _mysql_version(data: bytes) -> tuple[str, str]:
    text = data[:65536].decode("utf-8", "replace")
    server = re.search(r"Server version\s+(\d+)\.(\d+)[^\n]*", text)
    maria = "MariaDB" in text[:4000]
    if maria:
        if server and (int(server.group(1)), int(server.group(2))) >= (10, 5):
            return "mariadb", f"{server.group(1)}.{server.group(2)}"
        return "mariadb", DEFAULTS["mariadb"]
    if server and int(server.group(1)) >= 8:
        return "mysql", f"{server.group(1)}.{server.group(2)}"
    return "mysql", DEFAULTS["mysql"]


def mongo_databases(data: bytes) -> list[str]:
    """Database names inside a mongodump archive (from BSON headers with a db field)."""
    names = []
    for match in re.finditer(rb"\x02db\x00", data):
        start = match.end()
        if start + 4 > len(data):
            break
        (length,) = struct.unpack("<i", data[start:start + 4])
        if not 1 < length < 200:
            continue
        name = data[start + 4:start + 3 + length].decode("utf-8", "replace")
        if name and name not in ("admin", "config", "local") and name not in names:
            names.append(name)
    return names


def detect(path: Path) -> dict:
    """{"engine", "version", "format"} or DumpError if the format is not recognized."""
    data = head(path)
    if data[:5] == b"PGDMP":
        return {"engine": "postgres", "version": _pg_version(_postgres_major(data)), "format": "pg_dump custom"}
    if data[:4] == MONGO_MAGIC:
        return {"engine": "mongodb", "version": DEFAULTS["mongodb"], "format": "mongodump archive", "databases": mongo_databases(data)}
    if data[:5] == b"REDIS" and data[5:9].isdigit():
        rdb = int(data[5:9])
        version = RDB_MIN_REDIS.get(rdb) or (DEFAULTS["redis"] if rdb < 10 else max(RDB_MIN_REDIS.values()))
        if tuple(map(int, version.split("."))) < tuple(map(int, DEFAULTS["redis"].split("."))):
            version = DEFAULTS["redis"]
        return {"engine": "redis", "version": version, "format": f"RDB {rdb}"}
    text = data[:65536]
    if b"-- MySQL dump" in text or b"-- MariaDB dump" in text or b"mysqldump" in text[:2000] or b"mariadb-dump" in text[:2000]:
        engine, version = _mysql_version(data)
        return {"engine": engine, "version": version, "format": "SQL (mysqldump)"}
    if b"PostgreSQL database" in text or b"pg_dump" in text or b"SET client_encoding" in text or b"pg_catalog." in text:
        return {"engine": "postgres", "version": _pg_version(_postgres_major(data)), "format": "SQL (pg_dump)"}
    raise DumpError("Unrecognized file format; specify the engine with --engine")


def needs_newer(engine: str, wanted: str, detected: str) -> bool:
    """The requested version is older than the source and the dump cannot be restored into it."""
    if engine not in ("postgres", "redis"):
        return False

    def parts(value):
        nums = re.findall(r"\d+", value)
        return tuple(int(n) for n in nums[:2]) or (0,)

    return parts(wanted) < parts(detected)


# ---- Download from a URL (e.g. a provider's backup download link) ----


def ensure_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise DumpError(f"Could not resolve {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            # The worker sits on the panel's internal network; never follow internal addresses (SSRF).
            raise DumpError("Internal or private addresses are not allowed")


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DumpError("The URL must be http or https")
    ensure_public(parts.hostname)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, dest: Path, on_progress=None) -> int:
    check_url(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "dockra"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with opener.open(req, timeout=60) as resp, dest.open("wb") as handle:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                total += len(chunk)
                if on_progress:
                    on_progress(total)
    except DumpError:
        dest.unlink(missing_ok=True)
        raise
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise DumpError(f"Download failed: {exc}") from exc
    if total == 0:
        dest.unlink(missing_ok=True)
        raise DumpError("The downloaded file is empty")
    return total
