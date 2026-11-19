#!/usr/bin/env python3
"""Dockra CLI. Without arguments it prints a command cheat sheet."""

__version__ = "0.8.0"

import argparse
import base64
import gzip
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import struct
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CONFIG = Path.home() / ".dockra.json"
MANIFEST = "dockra.json"
LIARA_MANIFEST = "liara.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dockra"
BATCH_BYTES = 32 * 1024 * 1024
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".next",
    "media", "staticfiles", "static_root", ".dockra", ".claude", ".pytest_cache",
}
SKIP_FILES = {".env", "db.sqlite3", "db.sqlite3-journal"}

CHEATSHEET = """
Dockra — quick reference

  dockra login
  dockra login --api http://127.0.0.1:8000
  dockra init myapp
  dockra deploy                 (only changed files are uploaded)
  dockra deploy --full          (the whole folder)
  dockra domain add app.example.com
  dockra domain check app.example.com
  dockra domain https app.example.com on
  dockra redirect add old.example.com https://example.com   (whole domain, path preserved)
  dockra redirect add /blog /news                       (path and below; --exact for that page only)
  dockra env set DEBUG=0
  dockra logs -f
  dockra exec -- python manage.py migrate
  dockra shell                  (real terminal; --line for line mode)
  dockra env set --file .env
  dockra db connect myapp-db    (tunnel to 127.0.0.1)
  dockra open
  dockra upgrade                (new version from the same server)
  dockra restart
  dockra disk files media
  dockra disk upload media ./uploads --to /
  dockra disk download media /uploads
  dockra disk import media --ftp ftp://user@host/   (disk files from another host, pulled by the server)
  dockra db create myapp-db --engine postgres   (mysql, mariadb, mongodb, redis)
  dockra db link myapp-db
  dockra db backups myapp-db
  dockra db import myapp-db --file backup.dump --link   (new database from a backup file)
  dockra db restore myapp-db --file backup.dump
  dockra webhook enable

If dockra.json (or liara.json) is in the folder, the app ID is read from its app field.
Full guide: docs/cli.md
""".strip()


def _config_path() -> Path | None:
    return CONFIG if CONFIG.exists() else None


def load_config():
    # CI and scripts: an API token in the environment takes precedence over the stored login.
    env_token = os.environ.get("DOCKRA_TOKEN", "").strip()
    path = _config_path()
    saved = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    if env_token:
        return {"api": (os.environ.get("DOCKRA_API") or saved.get("api") or "http://127.0.0.1:8000").rstrip("/"), "token": env_token}
    if not saved:
        raise SystemExit("Sign in first: dockra login (or set DOCKRA_TOKEN and DOCKRA_API)")
    if os.environ.get("DOCKRA_API"):
        saved["api"] = os.environ["DOCKRA_API"].rstrip("/")
    return saved


def _error(exc: urllib.error.HTTPError):
    raw = exc.read().decode("utf-8", "replace")
    try:
        detail = json.loads(raw).get("detail")
    except json.JSONDecodeError:
        detail = raw
    raise SystemExit(detail or exc.reason)


_SSLIP_HOST = re.compile(
    r"^([a-z0-9-]+)\.(\d{1,3}(?:\.\d{1,3}){3})\.sslip\.io$",
    re.IGNORECASE,
)


def _direct_sslip(req, base):
    """sslip names do not resolve from these networks. Connect to the IP and send Host: dockra.local."""
    match = _SSLIP_HOST.match(urlsplit(base).hostname or "")
    if not match:
        return None
    name, ip = match.group(1), match.group(2)
    parts = urlsplit(req.full_url)
    port = f":{parts.port}" if parts.port else ""
    fixed = urlunsplit((parts.scheme, ip + port, parts.path, parts.query, ""))
    retry = urllib.request.Request(fixed, data=req.data, method=req.get_method())
    for key, value in req.headers.items():
        if key.lower() != "host":
            retry.add_header(key, value)
    retry.add_unredirected_header("Host", f"{name}.dockra.local")
    return retry


LOCAL_PORT = 45731


def _browser_base(api: str) -> str:
    """Page the browser opens. For sslip a local proxy sends the right Host header."""
    match = _SSLIP_HOST.match(urlsplit(api).hostname or "")
    if not match:
        return api.rstrip("/")
    name, ip = match.group(1), match.group(2)
    host_header = f"{name}.dockra.local"

    class Proxy(BaseHTTPRequestHandler):
        def _forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {
                key: value
                for key, value in self.headers.items()
                # Origin and Referer are this proxy's address and would not match the rewritten Host (CSRF check).
                if key.lower() not in ("host", "content-length", "connection", "origin", "referer")
            }
            outgoing = urllib.request.Request(
                "http://" + ip + self.path, data=body, headers=headers, method=self.command
            )
            outgoing.add_unredirected_header("Host", host_header)
            try:
                res = urllib.request.urlopen(outgoing, timeout=60)
                status, payload, header_items = res.status, res.read(), list(res.headers.items())
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, exc.read()
                header_items = list(exc.headers.items()) if exc.headers else []
            except OSError as exc:
                message = str(exc).encode()
                self.send_response(502)
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return
            self.send_response(status)
            for key, value in header_items:
                if key.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._forward()

        def do_POST(self):
            self._forward()

        def log_message(self, fmt, *args):
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), Proxy)
    except OSError:
        return f"http://127.0.0.1:{LOCAL_PORT}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{LOCAL_PORT}"


def _open_browser(url: str) -> None:
    print(url)
    webbrowser.open(url)


def _chosen_api(args) -> str:
    if getattr(args, "api", None):
        return args.api.rstrip("/")
    if os.environ.get("DOCKRA_API"):
        return os.environ["DOCKRA_API"].rstrip("/")
    path = _config_path()
    if path is not None:
        saved = json.loads(path.read_text(encoding="utf-8")).get("api")
        if saved:
            return saved.rstrip("/")
    return "http://127.0.0.1:8000"


def _save_login(api: str, token: str) -> None:
    # The file holds an API token: create it readable by the owner only, even on shared machines.
    body = json.dumps({"api": api.rstrip("/"), "token": token}, ensure_ascii=False).encode("utf-8")
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
    try:
        os.chmod(CONFIG, 0o600)  # also tighten a file created by an older version
    except OSError:
        pass


def request(path, method="GET", payload=None, token=None, api=None, timeout=30):
    cfg = {} if api else load_config()
    base = (api or cfg["api"]).rstrip("/")
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    auth = token or cfg.get("token")
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    direct = _direct_sslip(req, base)
    if direct is not None:
        try:
            with urllib.request.urlopen(direct, timeout=timeout) as res:
                body = res.read().decode()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            _error(exc)
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        _error(exc)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise SystemExit(f"Could not connect to {base}: {reason}")


def local_manifest():
    for name in (MANIFEST, LIARA_MANIFEST):
        path = Path(name)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def app_slug(args):
    slug = getattr(args, "app", None) or getattr(args, "slug", None)
    if slug:
        return slug
    slug = local_manifest().get("app")
    if slug:
        return slug
    raise SystemExit("Pass the app ID, or set the app field in dockra.json.")


def ensure_app(slug):
    apps = request("/api/apps")
    if any(item["slug"] == slug for item in apps):
        return
    manifest = local_manifest()
    body = {
        "name": manifest.get("name") or slug,
        "slug": slug,
        "platform": manifest.get("platform") or "django",
        "git_url": manifest.get("git") or "",
    }
    if manifest.get("port"):
        body["port"] = int(manifest["port"])
    created = request("/api/apps", "POST", body)
    host = next((item["hostname"] for item in created.get("domains") or [] if item.get("is_primary")), "")
    print(f"App created: {slug}" + (f"  {host}" if host else ""))


IGNORE_FILES = (".dockraignore", ".liaraignore", ".dockerignore", ".gitignore")
DEFAULT_IGNORES = (
    ".git", ".idea", ".vscode", ".next", ".dockerignore", "*.*~",
    "node_modules", "bower_components", ".DS_Store",
    "venv", ".venv", ".env", "ENV", ".python-version", ".cache", "__pycache__",
    "lib", "lib64", "*.py[cod]", "*$py.class", "pip-log.txt", "pip-delete-this-directory.txt",
    "celerybeat-schedule", "*.log", "local_settings.py", "staticfiles",
)


def _glob_regex(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("/.*")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        elif body[i] == "[" and "]" in body[i + 1:]:
            end = body.index("]", i + 1)
            inner = body[i + 1:end].replace("\\", "\\\\")
            if inner.startswith("!"):
                inner = "^" + inner[1:]
            out.append(f"[{inner}]")
            i = end + 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return "".join(out)


def _compile_ignore(lines, base: str) -> list:
    rules = []
    prefix = re.escape(base + "/") if base else ""
    for raw in lines:
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        if line.startswith("\\"):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        body = _glob_regex(line.lstrip("/"))
        pattern = f"^{prefix}{body}$" if anchored else f"^{prefix}(?:.*/)?{body}$"
        rules.append((re.compile(pattern), negate, dir_only))
    return rules


def _read_ignore(directory: Path, base: str) -> list:
    for name in IGNORE_FILES:
        path = directory / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            return _compile_ignore(text.splitlines(), base)
    return []


def _ignored(rel: str, is_dir: bool, rules: list) -> bool:
    hit = False
    for regex, negate, dir_only in rules:
        if dir_only and not is_dir:
            continue
        if regex.match(rel):
            hit = not negate
    return hit


def scan_files(root: Path) -> list[tuple[str, Path]]:
    """Files to upload after applying ignore rules, as relative paths."""
    root = root.resolve()
    rules_at = {".": _compile_ignore(DEFAULT_IGNORES, "") + _read_ignore(root, "")}
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).as_posix()
        rules = rules_at.pop(rel)
        kept = []
        for name in dirnames:
            child = name if rel == "." else f"{rel}/{name}"
            if name in SKIP_DIRS or _ignored(child, True, rules):
                continue
            if (Path(dirpath) / name).is_symlink():
                continue
            rules_at[child] = rules + _read_ignore(Path(dirpath) / name, child)
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = name if rel == "." else f"{rel}/{name}"
            if name in SKIP_FILES or name.endswith((".pyc", ".pyo")) or _ignored(child, False, rules):
                continue
            path = Path(dirpath) / name
            if path.is_file():
                found.append((child, path))
    found.sort()
    return found


def pack_dir(root: Path) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    tmp.close()
    with tarfile.open(tmp.name, "w:gz", compresslevel=3) as archive:
        for child, path in scan_files(root):
            archive.add(path, arcname=child, recursive=False)
    return tmp.name


def _cache_file(root: Path) -> Path:
    key = hashlib.sha1(str(root.resolve()).encode("utf-8", "replace")).hexdigest()[:16]
    return CACHE_DIR / f"hashes-{key}.json"


def hash_tree(root: Path) -> list[dict]:
    """sha256 of every file; files whose size and mtime did not change are read from the cache."""
    cache_path = _cache_file(root)
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    fresh = {}
    rows = []
    for child, path in scan_files(root):
        info = path.stat()
        stamp = [info.st_size, info.st_mtime_ns]
        cached = cache.get(child)
        if cached and cached[:2] == stamp:
            digest = cached[2]
        else:
            sha = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    sha.update(chunk)
            digest = sha.hexdigest()
        fresh[child] = [*stamp, digest]
        mode = 0o755 if os.access(path, os.X_OK) and os.name != "nt" else 0o644
        rows.append({"path": child, "hash": digest, "size": info.st_size, "mode": mode, "_local": str(path)})
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(fresh), encoding="utf-8")
    except OSError:
        pass
    return rows


def _size(n):
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _percent(done, total):
    width = 24
    filled = int(width * done / total) if total else width
    pct = int(done * 100 / total) if total else 100
    sys.stdout.write(f"\r[{'#' * filled}{'-' * (width - filled)}] {pct:3d}%")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


class HttpFail(Exception):
    def __init__(self, status, detail, data=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.data = data or {}


def _connect(base, timeout):
    parts = urlsplit(base.rstrip("/"))
    match = _SSLIP_HOST.match(parts.hostname or "")
    if match:
        host, host_header = match.group(2), f"{match.group(1)}.dockra.local"
    else:
        host, host_header = parts.hostname, parts.netloc
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return conn_cls(host, port, timeout=timeout), host_header, parts.path.rstrip("/")


def _raw(method, path, body=None, headers=None, progress=None, timeout=7200, stream=False):
    """Request with http.client; body is one or more bytes chunks or a file path that is streamed."""
    cfg = load_config()
    conn, host_header, prefix = _connect(cfg["api"], timeout)
    all_headers = {"Authorization": f"Bearer {cfg['token']}", "Accept": "application/json", **(headers or {})}
    parts = [] if body is None else (list(body) if isinstance(body, (list, tuple)) else [body])
    size = sum(Path(part).stat().st_size if isinstance(part, (str, Path)) else len(part) for part in parts)
    conn.putrequest(method, prefix + path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host_header)
    for key, value in all_headers.items():
        conn.putheader(key, value)
    if body is not None:
        conn.putheader("Content-Length", str(size))
    conn.endheaders()
    step = 256 * 1024
    for part in parts:
        if isinstance(part, (str, Path)):
            with open(part, "rb") as handle:
                for chunk in iter(lambda: handle.read(step), b""):
                    conn.send(chunk)
                    if progress:
                        progress(len(chunk))
        else:
            view = memoryview(part)
            for start in range(0, len(part), step):
                chunk = view[start:start + step]
                conn.send(chunk)
                if progress:
                    progress(len(chunk))
    resp = conn.getresponse()
    if resp.status >= 400:
        raw = resp.read().decode("utf-8", "replace")
        conn.close()
        try:
            data = json.loads(raw)
            detail = data.get("detail") if isinstance(data, dict) else raw
        except json.JSONDecodeError:
            data, detail = {}, raw
        raise HttpFail(resp.status, detail or resp.reason, data if isinstance(data, dict) else {})
    if stream:
        return conn, resp
    raw = resp.read()
    conn.close()
    return json.loads(raw.decode()) if raw else None


def _with_retry(func, label, attempts=3):
    for attempt in range(attempts):
        try:
            return func()
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt == attempts - 1:
                raise SystemExit(f"{label} interrupted: {exc}") from exc
            print(f"\n{label} interrupted, retry {attempt + 2}/{attempts}")
            time.sleep(1 + attempt * 2)


UPLOAD_PIECE = 4 << 20


def _upload_pieces(src):
    """Upload a file in chunks to /api/uploads and return the upload ID.
    A single long request hits the timeout of any proxy or CDN on the way; here only the interrupted chunk is resent."""
    size = Path(src).stat().st_size
    upload_id = _raw("POST", "/api/uploads", b"{}", {"Content-Type": "application/json"}, timeout=60)["upload"]
    progress = Progress(size)
    offset = failures = 0
    with open(src, "rb") as handle:
        while offset < size:
            handle.seek(offset)
            piece = handle.read(UPLOAD_PIECE)
            progress.done = offset
            try:
                res = _raw("PUT", f"/api/uploads/{upload_id}?offset={offset}", piece,
                           {"Content-Type": "application/octet-stream"}, progress=progress, timeout=120)
                offset = res["size"]
                failures = 0
                continue
            except HttpFail as exc:
                # The chunk arrived but its response was lost: the server tells us where to resume.
                if exc.status == 409 and isinstance(exc.data.get("size"), int):
                    offset = exc.data["size"]
                    continue
                if exc.status < 500 and exc.status not in (408, 429):
                    raise
                error = exc
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                error = exc
            failures += 1
            if failures > 10:
                raise SystemExit(f"\nUpload interrupted: {error}")
            wait = min(30, 2 ** failures)
            print(f"\nInterrupted ({error}); retrying from {_size(offset)} in {wait} seconds")
            time.sleep(wait)
    if size == 0:
        progress(0)
    return upload_id


class Progress:
    def __init__(self, total):
        self.total = max(total, 1)
        self.done = 0

    def __call__(self, n):
        self.done = min(self.done + n, self.total)
        _percent(self.done, self.total)

    def rewind(self, n):
        self.done = max(self.done - n, 0)


def _open_url(domains):
    hosts = [item.get("hostname") or "" for item in domains]
    hosts = [host for host in hosts if host]

    def rank(host):
        if host.endswith(".nip.io"):
            return 0
        if "127.0.0.1" in host:
            return 3
        if host.endswith(".sslip.io"):
            return 2
        return 1

    if not hosts:
        return ""
    host = sorted(hosts, key=rank)[0]
    return host if "://" in host else f"http://{host}"


def upload_deploy(slug, archive_path, message):
    """Legacy path: the whole folder as tar.gz, for servers without incremental uploads."""
    boundary = "dockraBoundary" + hashlib.sha1(os.urandom(8)).hexdigest()[:12]
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="message"\r\n\r\n{message}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="app.tar.gz"\r\n'
        f"Content-Type: application/gzip\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    total = len(head) + Path(archive_path).stat().st_size + len(tail)

    def send():
        return _raw(
            "POST",
            f"/api/apps/{slug}/deploy",
            [head, archive_path, tail],
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            progress=Progress(total),
        )

    return _with_retry(send, "Upload")


def _batches(rows, limit=BATCH_BYTES):
    batch, size = [], 0
    for row in rows:
        if batch and size + row["size"] > limit:
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += row["size"]
    if batch:
        yield batch


def _send_blobs(slug, rows):
    total = sum(row["size"] for row in rows)
    progress = Progress(total)
    for batch in _batches(rows):
        tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        tmp.close()
        try:
            # Level 1: fast compression; the bottleneck is usually the network, not the CPU.
            with tarfile.open(tmp.name, "w:gz", compresslevel=1) as archive:
                for row in batch:
                    archive.add(row["_local"], arcname=row["hash"], recursive=False)
            raw_size = sum(row["size"] for row in batch)
            packed = Path(tmp.name).stat().st_size
            scale = raw_size / packed if packed else 1

            def send(tmp=tmp, scale=scale):
                before = progress.done
                try:
                    return _raw(
                        "POST",
                        f"/api/apps/{slug}/blobs",
                        Path(tmp.name),
                        {"Content-Type": "application/gzip"},
                        progress=lambda n: progress(int(n * scale)),
                    )
                except (TimeoutError, OSError, http.client.HTTPException):
                    progress.rewind(progress.done - before)
                    raise

            _with_retry(send, "Upload")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    if progress.done < progress.total:
        progress(progress.total - progress.done)


def delta_deploy(slug, root, message):
    """Only files the server does not have are uploaded. Old server -> None."""
    print("Scanning files")
    rows = hash_tree(root)
    if not rows:
        raise SystemExit("The folder has no files to deploy")
    files = [{key: row[key] for key in ("path", "hash", "size", "mode")} for row in rows]
    total = sum(row["size"] for row in rows)
    try:
        plan = _with_retry(lambda: _raw("POST", f"/api/apps/{slug}/sync", json.dumps({"files": files}).encode(),
                                        {"Content-Type": "application/json"}), "Sync")
    except HttpFail as exc:
        if exc.status in (404, 405):
            return None
        raise SystemExit(exc.detail)
    wanted = set(plan["missing"])
    upload = []
    for row in rows:
        if row["hash"] in wanted:
            upload.append(row)
            wanted.discard(row["hash"])
    sent_bytes = sum(row["size"] for row in upload)
    print(f"{len(rows)} files, {_size(total)}; changed: {len(upload)} files, {_size(sent_bytes)}")
    if upload:
        print("Uploading changes")
        _send_blobs(slug, upload)
    body = json.dumps({"message": message, "files": files}).encode()
    try:
        return _with_retry(
            lambda: _raw("POST", f"/api/apps/{slug}/deploy", body, {"Content-Type": "application/json"}), "Deploy"
        )
    except HttpFail as exc:
        raise SystemExit(exc.detail)


_STEP = {
    "queued": "Queued",
    "building": "Building",
    "running": "Running",
    "failed": "Failed",
    "superseded": "Superseded",
    "rolled_back": "Rolled back",
    "canceled": "Canceled",
}


def _show_deploy(dep, seen_log, seen_status):
    log = dep.get("log") or ""
    fresh = log[len(seen_log):] if log.startswith(seen_log) else log
    for line in fresh.splitlines():
        if line.strip():
            print(f"  {line}")
    status = dep.get("status")
    if status != seen_status:
        print(f"{dep.get('version', '')}  {_STEP.get(status, status)}")
    return log, status


def print_app_line(app):
    host = ""
    domains = app.get("domains") or []
    primary = next((item for item in domains if item.get("is_primary")), None)
    if primary:
        host = primary["hostname"]
    elif domains:
        host = domains[0]["hostname"]
    extra = f"  {host}" if host else ""
    print(f"{app['slug']:18} {app['status_label']:14}{extra}")


def cmd_login(args):
    api = _chosen_api(args)
    if args.password:
        if not args.username:
            raise SystemExit("With --password, also pass --username.")
        password = args.password
        data = request(
            "/api/auth/login",
            "POST",
            {"username": args.username, "password": password},
            api=api,
        )
        _save_login(api, data["access"])
        print("Signed in:", data["user"]["username"], "->", api)
        return
    started = request("/api/auth/cli/start", "POST", {}, api=api)
    code = started["code"]
    url = _browser_base(api) + f"/cli?code={code}"
    print("If you are signed in to the panel in your browser, click Connect.")
    print(f"Code: {code[-6:].upper()}  (must match the code shown in the browser)")
    _open_browser(url)
    for _ in range(150):
        time.sleep(2)
        data = request(f"/api/auth/cli/poll?code={urllib.parse.quote(code)}", api=api)
        if data.get("status") == "ready":
            _save_login(api, data["token"])
            print("Signed in:", data["username"], "->", api)
            print("From now on, in any project: dockra deploy")
            return
    raise SystemExit("The sign-in request timed out. Run dockra login again.")


PLATFORM_PORTS = {"django": 8000, "node": 3000, "nextjs": 3000, "static": 80, "docker": 8000}


def cmd_init(args):
    path = Path(MANIFEST)
    if path.exists() and not args.force:
        raise SystemExit("dockra.json already exists. Use --force to replace it.")
    doc = {
        "app": args.slug,
        "platform": args.platform,
        "port": PLATFORM_PORTS[args.platform],
    }
    if args.platform == "django":
        doc["disks"] = [{"name": "media", "mountTo": "/app/media"}]
        doc["cron"] = ["* * * * * python manage.py run_due_jobs"]
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Wrote dockra.json")
    print("Next: dockra deploy")


def cmd_apps(_args):
    for app in request("/api/apps"):
        print(f"{app['slug']:18} {app['status_label']:14} {app['platform_label']}")


def cmd_deploy(args):
    _hint_upgrade()
    slug = app_slug(args)
    ensure_app(slug)
    root = Path(args.dir).resolve()
    print(load_config()["api"])
    print(slug)
    message = (args.message or "deploy from CLI").replace("\n", " ")
    started = time.monotonic()
    data = None if args.full else delta_deploy(slug, root, message)
    if data is None:
        print("Packing the project")
        archive = pack_dir(root)
        try:
            print(f"Packed {_size(Path(archive).stat().st_size)}")
            print("Uploading")
            try:
                data = upload_deploy(slug, archive, message)
            except HttpFail as exc:
                raise SystemExit(exc.detail)
        finally:
            Path(archive).unlink(missing_ok=True)
    print(f"Upload finished in {time.monotonic() - started:.1f}s")
    dep = data["deployment"]
    seen_log, seen_status = _show_deploy(dep, "", None)
    deadline = time.time() + 1800
    try:
        while dep["status"] in ("queued", "building") and time.time() < deadline:
            time.sleep(1.5)
            rows = request(f"/api/apps/{slug}/deployments")
            current = next((item for item in rows if item["id"] == dep["id"]), None)
            if current is None:
                break
            dep = current
            seen_log, seen_status = _show_deploy(dep, seen_log, seen_status)
    except KeyboardInterrupt:
        print("\nCanceling deployment…")
        _cancel_deployment(slug, dep["id"])
        raise SystemExit(130)
    if dep["status"] in ("failed", "canceled"):
        raise SystemExit(1)
    print(f"Done in {time.monotonic() - started:.1f}s")
    detail = request(f"/api/apps/{slug}")
    opened = _open_url(detail.get("domains") or [])
    if opened:
        print(opened)


def _follow_logs(slug, tail):
    """Live logs over the terminal service WebSocket. False means the server lacks it and polling is needed."""
    try:
        data = _raw("POST", f"/api/apps/{slug}/logs/ticket?tail={tail}", b"{}", {"Content-Type": "application/json"}, timeout=30)
    except HttpFail as exc:
        if exc.status in (404, 503):
            return False
        raise SystemExit(exc.detail)
    try:
        ws = WebSocket(data["url"])
    except OSError as exc:
        print(f"(live logs unavailable: {exc}; polling)", file=sys.stderr)
        return False
    pending = b""
    try:
        while True:
            frame = ws.recv()
            if frame is None:
                break
            op, payload = frame
            if op == 2:
                pending += payload
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    if line.strip():
                        print(line.decode("utf-8", "replace"), flush=True)
                continue
            event = json.loads(payload.decode("utf-8", "replace"))
            if event.get("type") == "error":
                raise SystemExit(event.get("detail"))
            if event.get("type") == "exit":
                break
    finally:
        if pending.strip():
            print(pending.decode("utf-8", "replace"), flush=True)
        ws.close()
    return True


def cmd_logs(args):
    slug = app_slug(args)
    if args.follow:
        while True:
            try:
                if not _follow_logs(slug, args.lines):
                    break
            except (TimeoutError, OSError, http.client.HTTPException):
                pass
            print("(reconnecting)", file=sys.stderr)
            time.sleep(3)
    previous = ""
    while True:
        text = request(f"/api/apps/{slug}/logs").get("text") or ""
        if text.startswith(previous) and previous:
            fresh = text[len(previous):].lstrip("\n")
        else:
            fresh = text
        if fresh:
            print(fresh)
        previous = text
        if not args.follow:
            return
        time.sleep(2)


def cmd_power(kind):
    def run(args):
        slug = app_slug(args)
        data = request(f"/api/apps/{slug}/{kind}", "POST", {})
        app = data["app"]
        print(f"{slug}  {app['status_label']}")
    return run


def cmd_deployments(args):
    slug = app_slug(args)
    for item in request(f"/api/apps/{slug}/deployments"):
        mark = "*" if item["is_current"] else " "
        print(f"{mark} {item['version']:6} {item['status_label']:16} {item['message']}")


def _cancel_deployment(slug, dep_id):
    try:
        data = request(f"/api/apps/{slug}/deployments/{dep_id}/cancel", "POST", {})
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    version = data["deployment"]["version"]
    if data.get("state") == "canceling":
        print(f"{version}  canceling (the build is being stopped)")
    else:
        print(f"{version}  canceled")


def cmd_cancel(args):
    slug = app_slug(args)
    rows = request(f"/api/apps/{slug}/deployments")
    if getattr(args, "version", None):
        number = args.version.lower().removeprefix("v")
        target = next((item for item in rows if str(item["number"]) == number), None)
        if target is None:
            raise SystemExit(f"Release v{number} not found")
        if target["status"] not in ("queued", "building"):
            raise SystemExit(f"{target['version']} is not queued or building and cannot be canceled")
        targets = [target]
    else:
        targets = [item for item in rows if item["status"] in ("queued", "building")]
        if not targets:
            raise SystemExit("There is no queued or building deployment to cancel")
    for item in targets:
        _cancel_deployment(slug, item["id"])


def cmd_rollback(args):
    slug = app_slug(args)
    number = args.version.lower().removeprefix("v")
    rows = request(f"/api/apps/{slug}/deployments")
    match = next((item for item in rows if str(item["number"]) == number), None)
    if match is None:
        raise SystemExit(f"Release v{number} not found")
    data = request(f"/api/apps/{slug}/rollback", "POST", {"deployment_id": match["id"]})
    print(f"{slug}  {data['deployment']['version']}  {data['app']['status_label']}")


def cmd_domain_ls(args):
    slug = getattr(args, "app", None)
    if slug:
        rows = request(f"/api/apps/{slug}/domains")
    else:
        manifest_slug = local_manifest().get("app")
        rows = request(f"/api/apps/{manifest_slug}/domains") if manifest_slug else request("/api/domains")
    for item in rows:
        print(f"{item['hostname']:40} {item['ssl_label']:10} {item.get('app_name') or item.get('app') or ''}")


def cmd_domain_add(args):
    slug = app_slug(args)
    ensure_app(slug)
    domain = request(f"/api/apps/{slug}/domains", "POST", {"hostname": args.hostname.strip().lower()})
    print(f"{domain['hostname']}  {domain['ssl_label']}")
    _print_dns(domain)


def _print_dns(domain):
    records = domain.get("dns") or []
    if not records:
        return
    print()
    print("Create this record at your DNS provider; keep any CDN proxy disabled:")
    print(f"  {'Type':6} {'Name':24} {'Value':18} TTL")
    for item in records:
        print(f"  {item['type']:6} {item['name']:24} {item['value']:18} {item['ttl']}")
    print(f"  (zone: {records[0]['zone']})")
    print()
    print(f"Then run: dockra domain check {domain['hostname']}")


def cmd_domain_check(args):
    slug = app_slug(args)
    rows = request(f"/api/apps/{slug}/domains")
    match = next((item for item in rows if item["hostname"] == args.hostname.strip().lower()), None)
    if match is None:
        raise SystemExit(f"Domain {args.hostname} is not on {slug}")
    if not match.get("dns"):
        print(f"{match['hostname']}  is the default domain and needs no record")
        return
    result = request(f"/api/apps/{slug}/domains/{match['id']}/check")
    print(f"{match['hostname']}  {result['message']}")
    print(f"  SSL: {match['ssl_label']}")
    if result["state"] in ("missing", "mismatch"):
        _print_dns(match)
        raise SystemExit(1)


def cmd_env_ls(args):
    for item in request(f"/api/apps/{app_slug(args)}/env"):
        flag = " secret" if item["is_secret"] else ""
        print(f"{item['key']}={item['value']}{flag}")


def cmd_env_set(args):
    slug = app_slug(args)
    current = {item["key"]: item for item in request(f"/api/apps/{slug}/env")}
    pairs = list(args.pairs)
    if args.file:
        loaded = parse_env_file(Path(args.file).read_text(encoding="utf-8"))
        pairs += [f"{key}={value}" for key, value in loaded.items()]
    if not pairs:
        raise SystemExit("Pass KEY=VALUE or --file .env")
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"Expected KEY=VALUE  ({pair})")
        key, value = pair.split("=", 1)
        previous = current.get(key, {})
        current[key] = {
            "key": key,
            "value": value,
            "is_secret": True if args.secret else previous.get("is_secret", False),
        }
    request(f"/api/apps/{slug}/env", "PUT", {"items": list(current.values())})
    print(f"Saved {len(pairs)} variables on {slug}")


def cmd_env_rm(args):
    slug = app_slug(args)
    items = [item for item in request(f"/api/apps/{slug}/env") if item["key"] not in args.keys]
    request(f"/api/apps/{slug}/env", "PUT", {"items": items})
    print("Removed:", ", ".join(args.keys))


DB_VERSIONS = {"postgres": "16", "mysql": "8.0", "mariadb": "11.4", "mongodb": "7", "redis": "7.4"}


def cmd_db_ls(_args):
    for item in request("/api/databases"):
        print(f"{item['name']:18} {item['engine']:10} {item['version']:6} {item['status_label']}")


def cmd_db_create(args):
    version = args.version or DB_VERSIONS[args.engine]
    db = request("/api/databases", "POST", {
        "name": args.name,
        "engine": args.engine,
        "version": version,
    })
    print(f"{db['name']}  {db['engine']} {db['version']}  {db['status_label']}")
    if db.get("url"):
        print(db["url"])


def _db_by_name(name):
    match = next((item for item in request("/api/databases") if item["name"] == name), None)
    if match is None:
        raise SystemExit(f"Database {name} not found")
    return match


def cmd_db_link(args):
    db = _db_by_name(args.name)
    slug = app_slug(args)
    body = {"app": slug}
    if args.key:
        body["env_key"] = args.key
    linked = request(f"/api/databases/{db['id']}/link", "POST", body)
    print(f"{db['name']} → {slug}")
    if linked.get("url"):
        print(linked["url"])


def cmd_db_backup(args):
    db = _db_by_name(args.name)
    data = request(f"/api/databases/{db['id']}/backup", "POST", {})
    backup = data["backup"]
    print(f"{db['name']}  {backup['status_label']}  {backup['size_bytes']} bytes")


def cmd_db_power(kind):
    def run(args):
        db = _db_by_name(args.name)
        if kind == "stop" and not args.yes:
            answer = input(f"Apps linked to {db['name']} lose access until it is started again. Continue? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                raise SystemExit("Aborted")
        data = request(f"/api/databases/{db['id']}/{kind}", "POST", {}, timeout=300)
        print(f"{db['name']}  {data['database']['status_label']}")
    return run


def cmd_db_resize(args):
    db = _db_by_name(args.name)
    body = {key: value for key, value in (("cpu", args.cpu), ("memory_mb", args.memory), ("disk_gb", args.disk)) if value}
    if not body:
        raise SystemExit("Pass --cpu, --memory or --disk")
    data = request(f"/api/databases/{db['id']}", "PATCH", body, timeout=300)
    item = data["database"]
    print(f"{item['name']}  cpu={item['cpu']}  memory={item['memory_mb']}MB  disk={item['disk_gb']}GB  {item['status_label']}")


def cmd_cron_ls(args):
    for item in request(f"/api/apps/{app_slug(args)}/cron"):
        state = "on " if item["enabled"] else "off"
        last = item.get("last_status") or "-"
        print(f"{item['id']:5} {state} {item['schedule']:16} {last:7} {item['command']}")


def cmd_cron_add(args):
    slug = app_slug(args)
    parts = args.line.split()
    if len(parts) < 6:
        raise SystemExit('Expected: dockra cron add "*/5 * * * * python manage.py clearsessions"')
    item = request(f"/api/apps/{slug}/cron", "POST", {"schedule": " ".join(parts[:5]), "command": " ".join(parts[5:])})
    print(f"{item['id']}  {item['schedule']}  {item['command']}")


def cmd_cron_set(enabled):
    def run(args):
        item = request(f"/api/apps/{app_slug(args)}/cron/{args.id}", "PATCH", {"enabled": enabled})
        print(f"{item['id']}  {'on' if item['enabled'] else 'off'}")
    return run


def cmd_cron_rm(args):
    request(f"/api/apps/{app_slug(args)}/cron/{args.id}", "DELETE")
    print("Removed:", args.id)


def cmd_disk_resize(args):
    slug = app_slug(args)
    body = {}
    if args.size:
        body["size_gb"] = args.size
    if args.mount:
        body["mount_to"] = args.mount
    if not body:
        raise SystemExit("--size GB or --mount PATH is required")
    try:
        disk = request(f"/api/apps/{slug}/disks/{args.disk}", "PATCH", body, timeout=300)
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    print(f"{disk['name']}  {disk['size_gb']} GB  {disk['mount_to']}")


def cmd_disk_rm(args):
    slug = app_slug(args)
    if not args.yes:
        answer = input(f"The files of disk {args.disk} on {slug} will be deleted (its backups are kept for 30 days). Continue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise SystemExit("Aborted")
    request(f"/api/apps/{slug}/disks/{args.disk}", "DELETE", timeout=300)
    print("Removed:", args.disk)


def cmd_disk_backup(args):
    slug = app_slug(args)
    print(f"Backing up disk {args.disk} of {slug}")
    item = request(f"/api/apps/{slug}/disks/{args.disk}/backups", "POST", {}, timeout=3600)
    print(f"{item['id']}  {item['status_label']}  {_size(item['size_bytes'])}")


def cmd_disk_backups(args):
    for item in request(f"/api/apps/{app_slug(args)}/disks/{args.disk}/backups"):
        print(f"{item['id']:6} {item['kind']:7} {item['status_label']:10} {_size(item['size_bytes']):>10}  {item['created_at']}")


def cmd_disk_backup_download(args):
    dest = args.output or f"{args.disk}-{args.backup}.tar.gz"
    try:
        _save_stream(f"/api/apps/{app_slug(args)}/disks/{args.disk}/backups/{args.backup}/download", dest)
    except HttpFail as exc:
        raise SystemExit(exc.detail)


def cmd_disk_restore(args):
    slug = app_slug(args)
    if not args.yes:
        answer = input(
            f"The current files of disk {args.disk} on {slug} will be replaced with backup {args.backup} "
            "(the current state is backed up first). Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            raise SystemExit("Aborted")
    request(f"/api/apps/{slug}/disks/{args.disk}/backups/{args.backup}/restore", "POST", {}, timeout=3600)
    print("Restore queued or completed:", args.disk)


def cmd_db_backups(args):
    db = _db_by_name(args.name)
    detail = request(f"/api/databases/{db['id']}")
    for item in detail.get("backups") or []:
        print(f"{item['id']:6} {item.get('kind', 'manual'):7} {item['status_label']:10} {_size(item['size_bytes']):>10}  {item['created_at']}")


def _save_stream(path, dest):
    conn, resp = _raw("GET", path, headers={"Accept": "*/*"}, stream=True)
    try:
        total = int(resp.getheader("Content-Length") or 0)
        progress = Progress(total) if total else None
        with open(dest, "wb") as handle:
            for chunk in iter(lambda: resp.read(256 * 1024), b""):
                handle.write(chunk)
                if progress:
                    progress(len(chunk))
    finally:
        conn.close()
    print(f"Saved {dest}")


def _filename(disposition, fallback):
    match = re.search(r'filename="?([^";]+)"?', disposition or "")
    return Path(match.group(1)).name if match else fallback


def cmd_db_download(args):
    db = _db_by_name(args.name)
    dest = args.output or f"{db['name']}-{args.backup}.backup"
    try:
        _save_stream(f"/api/databases/{db['id']}/backups/{args.backup}/download", dest)
    except HttpFail as exc:
        raise SystemExit(exc.detail)


def cmd_db_restore(args):
    db = _db_by_name(args.name)
    if not args.yes:
        answer = input(f"The current data of {db['name']} will be replaced. Continue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise SystemExit("Aborted")
    started = time.monotonic()
    try:
        if args.file:
            source = Path(args.file).expanduser()
            if not source.is_file():
                raise SystemExit(f"File {source} not found")
            path, temporary = _gzip_for_upload(source)
            try:
                print(f"Uploading {_size(path.stat().st_size)}")
                upload_id = _upload_pieces(path)
                data = request(f"/api/databases/{db['id']}/restore", "POST", {"upload": upload_id})
            finally:
                if temporary:
                    path.unlink(missing_ok=True)
        else:
            if not args.backup:
                raise SystemExit("--backup ID or --file PATH is required")
            data = request(f"/api/databases/{db['id']}/restore", "POST", {"backup_id": args.backup})
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    status = _follow(f"/api/jobs/{data['job']['id']}")
    if status["status"] == "failed":
        raise SystemExit(f"Restoring {db['name']} failed")
    print(f"✓ {db['name']} restored in {int(time.monotonic() - started)} s")


def _gzip_for_upload(path):
    """Plain SQL or uncompressed archives are gzipped before upload (usually several times smaller). The server accepts both."""
    with open(path, "rb") as handle:
        magic = handle.read(5)
    if magic[:2] == b"\x1f\x8b" or magic in (b"PGDMP", b"REDIS") or path.stat().st_size < 1 << 20:
        return path, False
    total = path.stat().st_size
    tmp = tempfile.NamedTemporaryFile(prefix="dockra-", suffix=".gz", delete=False)
    tmp.close()
    print(f"Compressing {path.name} ({_size(total)})")
    progress = Progress(total)
    try:
        with open(path, "rb") as src, gzip.GzipFile(tmp.name, "wb", compresslevel=3, mtime=0) as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
                progress(len(chunk))
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name), True


def _follow(path, interval=1.5):
    """Print the step log of a long-running job until it finishes and return its final state."""
    seen = 0
    try:
        while True:
            status = request(path)
            lines = (status.get("log") or "").splitlines()
            for line in lines[seen:]:
                print(f"  {line}")
            seen = len(lines)
            if status["status"] in ("done", "failed"):
                return status
            time.sleep(interval)
    except KeyboardInterrupt:
        raise SystemExit("\nThe job keeps running on the server; follow it in the panel.")


def cmd_db_import(args):
    """Create a new database from a backup file: engine and version are detected from the file itself."""
    if bool(args.file) == bool(args.url):
        raise SystemExit("One of --file PATH or --url URL is required")
    query = {"name": args.name}
    for key, value in (("engine", args.engine), ("version", args.version), ("cpu", args.cpu),
                       ("memory_mb", args.memory), ("disk_gb", args.disk), ("url", args.url)):
        if value:
            query[key] = value
    path = "/api/databases/import?" + urllib.parse.urlencode(query)
    if any(item["name"] == args.name for item in request("/api/databases")):
        raise SystemExit(f"Database {args.name} already exists; to replace its data: dockra db restore {args.name} --file ...")
    started = time.monotonic()
    try:
        if args.file:
            src = Path(args.file).expanduser()
            if not src.is_file():
                raise SystemExit(f"File {src} not found")
            upload, temporary = _gzip_for_upload(src)
            try:
                print(f"Uploading {_size(upload.stat().st_size)}")
                query["upload"] = _upload_pieces(upload)
                data = _raw("POST", "/api/databases/import?" + urllib.parse.urlencode(query), b"{}",
                            {"Content-Type": "application/json"}, timeout=600)
            finally:
                if temporary:
                    upload.unlink(missing_ok=True)
        else:
            print("The server downloads the backup directly from the URL")
            data = _raw("POST", path, b"", {"Content-Type": "application/octet-stream"})
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    detected = data.get("detected") or {}
    if detected:
        print(f"Format: {detected['format']} -> {detected['engine']} {detected['version']}")
    status = _follow(f"/api/databases/import/{data['job']['id']}")
    db = status.get("database") or {}
    if status["status"] == "failed":
        if db.get("status") == "running":
            raise SystemExit(f"Importing data failed; database {args.name} was created and you can retry: "
                             f"dockra db restore {args.name} --file ...")
        raise SystemExit(f"Import failed; delete database {args.name} in the panel and try again.")
    print(f"✓ {db.get('name')}  {db.get('engine')} {db.get('version')}  in {int(time.monotonic() - started)} s")
    if db.get("url"):
        print(db["url"])
    if args.link:
        body = {"app": app_slug(args)}
        if args.key:
            body["env_key"] = args.key
        try:
            request(f"/api/databases/{db['id']}/link", "POST", body)
        except HttpFail as exc:
            raise SystemExit(f"The database is ready but linking it to the app failed: {exc.detail}")
        print(f"{db['name']} → {body['app']}")


def cmd_exec(args):
    slug = app_slug(args)
    parts = args.command[1:] if args.command[:1] == ["--"] else args.command
    command = " ".join(parts).strip()
    if not command:
        raise SystemExit("Put the command after --: dockra exec -- python manage.py migrate")
    data = request(f"/api/apps/{slug}/exec", "POST", {"command": command}, timeout=300)
    sys.stdout.write(data["output"])
    if data["output"] and not data["output"].endswith("\n"):
        sys.stdout.write("\n")
    raise SystemExit(data["exit_code"])


class WebSocket:
    """Tiny WebSocket client (RFC 6455) using only the standard library."""

    def __init__(self, url, timeout=20):
        parts = urlsplit(url)
        secure = parts.scheme == "wss"
        host = parts.hostname or ""
        port = parts.port or (443 if secure else 80)
        host_header = parts.netloc
        match = _SSLIP_HOST.match(host)
        connect_host = host
        if match:
            connect_host, host_header = match.group(2), f"{match.group(1)}.dockra.local"
        raw = socket.create_connection((connect_host, port), timeout=timeout)
        if secure:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self.lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        handshake = (
            f"GET {target} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("WebSocket connection closed")
            head += chunk
        header, self.buffer = head.split(b"\r\n\r\n", 1)
        status = header.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status + " ":
            raise OSError(f"WebSocket was not accepted: {status}")
        self.sock.settimeout(None)

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, data, opcode=None):
        if isinstance(data, str):
            payload, op = data.encode(), opcode or 1
        else:
            payload, op = bytes(data), opcode or 2
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | op, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | op, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | op, 0x80 | 127, n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + body)

    def recv(self):
        """(opcode, payload); pings are answered automatically. None means closed."""
        message, first_op = b"", None
        while True:
            try:
                b1, b2 = self._read(2)
            except EOFError:
                return None
            op, fin = b1 & 0x0F, b1 & 0x80
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            payload = self._read(n)
            if op == 9:
                self.send(payload, opcode=10)
                continue
            if op == 10:
                continue
            if op == 8:
                return None
            if op:
                first_op = op
            message += payload
            if fin:
                return first_op, message

    def close(self):
        try:
            self.send(b"", opcode=8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _ws_ticket(path):
    try:
        data = _raw("POST", path, b"{}", {"Content-Type": "application/json"}, timeout=30)
    except HttpFail as exc:
        return None, exc
    return data["url"], None


def _tty_shell(slug):
    """Real terminal with a TTY; Linux and macOS only. None means fall back to line mode."""
    if os.name == "nt" or not sys.stdin.isatty():
        return None
    import signal
    import termios
    import tty

    url, error = _ws_ticket(f"/api/apps/{slug}/terminal")
    if error is not None:
        if error.status == 503:
            print(f"{error.detail}; falling back to line mode", file=sys.stderr)
            return None
        raise SystemExit(error.detail)
    try:
        ws = WebSocket(url)
    except OSError as exc:
        print(f"Terminal did not connect ({exc}); falling back to line mode", file=sys.stderr)
        return None

    def resize(*_):
        size = os.get_terminal_size()
        ws.send(json.dumps({"type": "resize", "cols": size.columns, "rows": size.lines}))

    resize()
    signal.signal(signal.SIGWINCH, resize)
    saved = termios.tcgetattr(sys.stdin.fileno())
    code = 0
    stop = threading.Event()

    def pump_in():
        fd = sys.stdin.fileno()
        while not stop.is_set():
            try:
                data = os.read(fd, 4096)
            except OSError:
                return
            if not data:
                return
            try:
                # Raw bytes, so multi-byte UTF-8 characters never split across two chunks.
                ws.send(data)
            except OSError:
                return

    try:
        tty.setraw(sys.stdin.fileno())
        threading.Thread(target=pump_in, daemon=True).start()
        while True:
            frame = ws.recv()
            if frame is None:
                break
            op, payload = frame
            if op == 2:
                os.write(sys.stdout.fileno(), payload)
                continue
            event = json.loads(payload.decode("utf-8", "replace"))
            if event.get("type") == "exit":
                code = event.get("code") or 0
                break
            if event.get("type") == "error":
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
                raise SystemExit(event.get("detail"))
    finally:
        stop.set()
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        ws.close()
    return code


def cmd_db_connect(args):
    db = _db_by_name(args.name)
    port = args.port or {"postgres": 15432, "mysql": 13306, "mariadb": 13306, "mongodb": 27018, "redis": 16379}.get(db["engine"], 15000)
    detail = request(f"/api/databases/{db['id']}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(f"Port {port} is busy; pass --port ({exc})")
    server.listen(16)
    url = (detail.get("url") or "").replace(f"@{detail.get('host')}:{detail.get('port')}", f"@127.0.0.1:{port}")
    print(f"{db['name']} -> 127.0.0.1:{port}  (Ctrl+C to stop)")
    if url:
        print(url)

    def serve(conn):
        ws = None
        try:
            ticket_url, error = _ws_ticket(f"/api/databases/{db['id']}/tunnel")
            if error is not None:
                print(error.detail, file=sys.stderr)
                return
            ws = WebSocket(ticket_url)

            def up():
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    ws.send(data)
                ws.close()

            threading.Thread(target=up, daemon=True).start()
            while True:
                frame = ws.recv()
                if frame is None:
                    break
                op, payload = frame
                if op == 2:
                    conn.sendall(payload)
                else:
                    event = json.loads(payload.decode("utf-8", "replace"))
                    if event.get("type") == "error":
                        print(event.get("detail"), file=sys.stderr)
                        break
        except OSError:
            pass
        finally:
            conn.close()
            if ws:
                ws.close()

    try:
        while True:
            conn, _addr = server.accept()
            threading.Thread(target=serve, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print()
    finally:
        server.close()


def parse_env_file(text):
    """A .env file, or JSON key/value output such as `liara env list --output json`."""
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            rows = json.loads(stripped)
        except ValueError:
            rows = None
        if isinstance(rows, list):
            return {
                str(row["key"]): str(row.get("value") or "")
                for row in rows
                if isinstance(row, dict) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(row.get("key") or ""))
            }
    items = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            items[key] = value
    return items


def cmd_open(args):
    detail = request(f"/api/apps/{app_slug(args)}")
    url = _open_url(detail.get("domains") or [])
    if not url:
        raise SystemExit("The app has no domain")
    _open_browser(url)


def cmd_shell(args):
    slug = app_slug(args)
    if not args.line:
        code = _tty_shell(slug)
        if code is not None:
            raise SystemExit(code)
    cwd = ""
    print(f"{slug}: each line runs inside the container (no TTY). Exit with exit or Ctrl+D")
    while True:
        try:
            line = input(f"{slug}:{cwd or '~'}$ ")
        except EOFError:
            print()
            return
        if line.strip() in ("exit", "quit"):
            return
        if not line.strip():
            continue
        try:
            data = request(f"/api/apps/{slug}/exec", "POST", {"command": line, "cwd": cwd}, timeout=300)
        except SystemExit as exc:
            print(exc)
            continue
        sys.stdout.write(data["output"])
        if data["output"] and not data["output"].endswith("\n"):
            sys.stdout.write("\n")
        cwd = data.get("cwd") or cwd


def cmd_disk_ls(args):
    for item in request(f"/api/apps/{app_slug(args)}/disks"):
        print(f"{item['name']:16} {item['size_gb']:>4} GB  {item['mount_to']}")


def cmd_disk_files(args):
    slug = app_slug(args)
    query = urllib.parse.urlencode({"path": args.path or "/"})
    data = request(f"/api/apps/{slug}/disks/{args.disk}/files?{query}")
    print(data["path"])
    for item in data["entries"]:
        kind = "d" if item["type"] == "dir" else "-"
        size = "" if item["type"] == "dir" else _size(item["size"])
        print(f"{kind} {size:>10}  {item['modified'][:19]}  {item['name']}")


def cmd_disk_download(args):
    slug = app_slug(args)
    query = urllib.parse.urlencode({"path": args.path or "/"})
    try:
        conn, resp = _raw("GET", f"/api/apps/{slug}/disks/{args.disk}/download?{query}",
                          headers={"Accept": "*/*"}, stream=True)
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    name = _filename(resp.getheader("Content-Disposition"), f"{args.disk}.tar.gz")
    dest = Path(args.output or name)
    total = int(resp.getheader("Content-Length") or 0)
    progress = Progress(total) if total else None
    try:
        with dest.open("wb") as handle:
            for chunk in iter(lambda: resp.read(256 * 1024), b""):
                handle.write(chunk)
                if progress:
                    progress(len(chunk))
    finally:
        conn.close()
    print(f"Saved {dest}")


def cmd_disk_upload(args):
    slug = app_slug(args)
    source = Path(args.local)
    if not source.exists():
        raise SystemExit(f"{source} not found")
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    tmp.close()
    try:
        with tarfile.open(tmp.name, "w:gz", compresslevel=1) as archive:
            if args.contents and source.is_dir():
                for child in sorted(source.iterdir()):
                    archive.add(child, arcname=child.name)
            else:
                archive.add(source, arcname=source.name)
        print(f"Uploading {_size(Path(tmp.name).stat().st_size)}")
        try:
            query = urllib.parse.urlencode({"path": args.to or "/", "upload": _upload_pieces(Path(tmp.name))})
            _raw("POST", f"/api/apps/{slug}/disks/{args.disk}/upload?{query}", b"{}",
                 {"Content-Type": "application/json"}, timeout=1800)
        except HttpFail as exc:
            raise SystemExit(exc.detail)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    label = f"{source.name}/*" if args.contents and source.is_dir() else source.name
    print(f"{label} → {args.disk}:{args.to or '/'}")


def _tar_dir_for_upload(source):
    tmp = tempfile.NamedTemporaryFile(prefix="dockra-", suffix=".tar.gz", delete=False)
    tmp.close()
    print(f"Packing {source}")
    try:
        # Media files are usually already compressed (jpg, mp4); level 1 is fast.
        with tarfile.open(tmp.name, "w:gz", compresslevel=1) as archive:
            for child in sorted(source.iterdir()):
                archive.add(child, arcname=child.name)
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name)


def cmd_disk_import(args):
    """Disk files from an archive or folder, a download link, or directly over FTP (pulled by the server, in parallel and incrementally)."""
    import getpass

    slug = app_slug(args)
    sources = [bool(args.file), bool(args.url), bool(args.ftp)]
    if sum(sources) != 1:
        raise SystemExit("One of --file, --url or --ftp is required")
    if args.replace and not args.yes:
        answer = input(f"Every current file on disk {args.disk} will be deleted and replaced (a backup is taken first). Continue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise SystemExit("Aborted")
    path = f"/api/apps/{slug}/disks/{args.disk}/import"
    started = time.monotonic()
    try:
        if args.file:
            source = Path(args.file).expanduser()
            if not source.exists():
                raise SystemExit(f"{source} not found")
            upload = _tar_dir_for_upload(source) if source.is_dir() else source
            try:
                print(f"Uploading {_size(upload.stat().st_size)}")
                body = {"replace": args.replace, "upload": _upload_pieces(upload)}
                data = _raw("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"}, timeout=600)
            finally:
                if upload != source:
                    upload.unlink(missing_ok=True)
        else:
            body = {"replace": args.replace}
            if args.url:
                body["url"] = args.url
                print("The server downloads the archive directly")
            else:
                ftp = args.ftp
                parts = urlsplit(ftp)
                if parts.username and parts.password is None:
                    # Keep the password out of shell history.
                    secret = urllib.parse.quote(getpass.getpass(f"FTP password for {parts.username}: "), safe="")
                    ftp = urlunsplit(parts._replace(netloc=f"{parts.username}:{secret}@{parts.netloc.split('@', 1)[1]}"))
                body["ftp"] = ftp
                body["workers"] = args.workers
                print("The server pulls the files directly over FTP")
            data = _raw("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    except HttpFail as exc:
        raise SystemExit(exc.detail)
    status = _follow(f"/api/jobs/{data['job']['id']}")
    if status["status"] == "failed":
        raise SystemExit(f"Importing files into disk {args.disk} failed")
    result = status.get("result") or {}
    print(f"✓ {args.disk}: {result.get('files', 0)} files transferred"
          + (f", {result['skipped']} already present" if result.get("skipped") else "")
          + f", in {int(time.monotonic() - started)} s")
    if result.get("failed"):
        print(f"  {result['failed']} files failed; run the same command again to fetch only those.")


def cmd_webhook(args):
    slug = app_slug(args)
    if args.action == "show":
        data = request(f"/api/apps/{slug}/webhook")
        print("enabled" if data["enabled"] else "disabled", data["url"])
    elif args.action == "enable":
        data = request(f"/api/apps/{slug}/webhook", "POST", {})
        print("URL:   ", data["url"])
        print("Secret:", data["secret"])
        print("In GitHub/GitLab/Gitea, add this URL for push events and enter the Secret as well.")
    else:
        request(f"/api/apps/{slug}/webhook", "DELETE")
        print("Webhook disabled")


def cmd_domain_rm(args):
    slug = app_slug(args)
    rows = request(f"/api/apps/{slug}/domains")
    match = next((item for item in rows if item["hostname"] == args.hostname.strip().lower()), None)
    if match is None:
        raise SystemExit(f"Domain {args.hostname} is not on {slug}")
    request(f"/api/apps/{slug}/domains/{match['id']}", "DELETE")
    print("Removed:", match["hostname"])


def cmd_domain_https(args):
    slug = app_slug(args)
    rows = request(f"/api/apps/{slug}/domains")
    match = next((item for item in rows if item["hostname"] == args.hostname.strip().lower()), None)
    if match is None:
        raise SystemExit(f"Domain {args.hostname} is not on {slug}")
    request(f"/api/apps/{slug}/domains/{match['id']}", "PATCH", {"redirect_https": args.state == "on"})
    print(f"{match['hostname']}  redirect https: {args.state}")


def _redirect_label(item):
    tail = "/…" if item["match"] == "prefix" and item["source_path"] != "/" else ""
    keep = "/…" if item["keep_path"] and item["match"] == "prefix" else ""
    code = "301" if item["permanent"] else "302"
    return f"{item['id']:>4}  {code}  {item['source_host'] or '*'}{item['source_path']}{tail} → {item['target']}{keep}"


def cmd_redirect_ls(args):
    slug = app_slug(args)
    rows = request(f"/api/apps/{slug}/redirects")
    if not rows:
        print("No redirects")
    for item in rows:
        print(_redirect_label(item))


def cmd_redirect_add(args):
    slug = app_slug(args)
    source = args.source.strip()
    if "://" in source:
        source = source.split("://", 1)[1]
    host, path = (args.host or "", source) if source.startswith("/") else (source.partition("/")[0], "/" + source.partition("/")[2])
    body = {
        "source_host": host.lower(),
        "source_path": path,
        "match": "exact" if args.exact else "prefix",
        "target": args.target,
        "keep_path": not args.drop_path,
        "permanent": not args.temporary,
    }
    print("Added:", _redirect_label(request(f"/api/apps/{slug}/redirects", "POST", body)))


def cmd_redirect_rm(args):
    slug = app_slug(args)
    request(f"/api/apps/{slug}/redirects/{args.id}", "DELETE")
    print("Removed:", args.id)


def _version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", text or "")[:3]) or (0,)


def _public_get(base, path, timeout=60):
    """Unauthenticated request to the server (to install and update the CLI)."""
    conn, host_header, prefix = _connect(base, timeout)
    conn.putrequest("GET", prefix + path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host_header)
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    if resp.status >= 400:
        raise SystemExit(f"{path}: HTTP {resp.status}")
    return body, resp


def _server_base(args=None):
    base = getattr(args, "api", "") if args else ""
    if base:
        return base.rstrip("/")
    path = _config_path()
    if path is None:
        raise SystemExit("Pass the server URL: dockra upgrade --api https://api.example.com")
    return json.loads(path.read_text(encoding="utf-8"))["api"].rstrip("/")


def _replace_file(target, data, mode=None):
    tmp = target.with_name(target.name + ".new")
    tmp.write_bytes(data)
    if mode is not None:
        os.chmod(tmp, mode)
    if os.name == "nt" and target.suffix == ".exe":
        # Windows cannot replace a running exe but allows renaming it.
        old = target.with_name(target.name + ".old")
        old.unlink(missing_ok=True)
        target.rename(old)
    os.replace(tmp, target)


def cmd_upgrade(args):
    base = _server_base(args)
    meta = json.loads(_public_get(base, "/cli/version", timeout=15)[0])
    latest = meta.get("version") or ""
    if _version_tuple(latest) <= _version_tuple(__version__) and not args.force:
        print(f"dockra {__version__} is up to date")
        return
    if getattr(sys, "frozen", False):
        name = {"win32": "dockra-windows.exe", "darwin": "dockra-macos"}.get(sys.platform, "dockra-linux")
        if name not in (meta.get("binaries") or []):
            raise SystemExit(f"{name} is not available on the server; ask an administrator to upload it")
        data, resp = _public_get(base, f"/cli/bin/{name}", timeout=600)
        target = Path(sys.executable).resolve()
    else:
        target = Path(__file__).resolve()
        if (target.parent.parent / ".git").exists():
            raise SystemExit("This dockra was installed from the repository; run git pull there instead of upgrade")
        data, resp = _public_get(base, "/cli/dockra.py")
        compile(data.decode("utf-8"), "dockra", "exec")
    expected = resp.getheader("X-Dockra-Sha256") or ""
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("The downloaded file does not match the server hash; please try again")
    _replace_file(target, data, mode=0o755 if os.name != "nt" else None)
    print(f"dockra {__version__} → {latest or '?'}  ({target})")


def _hint_upgrade():
    """Ask the server for its version once a day; any error is silently ignored."""
    marker = CACHE_DIR / "version-check.json"
    try:
        seen = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    now = time.time()
    if now - seen.get("at", 0) < 86_400:
        latest = seen.get("latest", "")
    else:
        try:
            latest = json.loads(_public_get(_server_base(), "/cli/version", timeout=3)[0]).get("version", "")
        except (SystemExit, OSError, ValueError, http.client.HTTPException):
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"at": now, "latest": latest}), encoding="utf-8")
        except OSError:
            pass
    if _version_tuple(latest) > _version_tuple(__version__):
        print(f"A newer dockra ({latest}) is available: dockra upgrade", file=sys.stderr)


def _add_app_flag(parser):
    parser.add_argument("--app", help="App ID; defaults to dockra.json")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv == ["help"]:
        print(CHEATSHEET)
        return 0
    if argv in (["--version"], ["version"]):
        print(f"dockra {__version__}")
        return 0
    parser = argparse.ArgumentParser(prog="dockra", description="Deploy apps to Dockra. From code to cloud.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Open the browser and store the connection on this machine")
    login.add_argument("--api", default="")
    login.add_argument("--username", default="")
    login.add_argument("--password", default="")
    login.set_defaults(func=cmd_login)

    init = sub.add_parser("init", help="Create dockra.json")
    init.add_argument("slug")
    init.add_argument("--platform", default="django", choices=sorted(PLATFORM_PORTS))
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    sub.add_parser("apps", help="List apps").set_defaults(func=cmd_apps)

    deploy = sub.add_parser("deploy", help="Create the app if missing, then deploy the folder")
    deploy.add_argument("slug", nargs="?")
    deploy.add_argument("--dir", default=".")
    deploy.add_argument("--message", default="")
    deploy.add_argument("--full", action="store_true", help="Upload the whole folder, not just changes")
    deploy.set_defaults(func=cmd_deploy)

    logs = sub.add_parser("logs", help="Deployment logs")
    _add_app_flag(logs)
    logs.add_argument("slug", nargs="?")
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--lines", "-n", type=int, default=200)
    logs.set_defaults(func=cmd_logs)

    for kind, help_text in (("restart", "Restart"), ("stop", "Stop"), ("start", "Start")):
        power = sub.add_parser(kind, help=help_text)
        _add_app_flag(power)
        power.add_argument("slug", nargs="?")
        power.set_defaults(func=cmd_power(kind))

    deps = sub.add_parser("deployments", help="Release history")
    _add_app_flag(deps)
    deps.add_argument("slug", nargs="?")
    deps.set_defaults(func=cmd_deployments)

    rollback = sub.add_parser("rollback", help="Roll back to a release, e.g. v3")
    _add_app_flag(rollback)
    rollback.add_argument("version")
    rollback.set_defaults(func=cmd_rollback)

    cancel = sub.add_parser("cancel", help="Cancel a queued or building deployment")
    _add_app_flag(cancel)
    cancel.add_argument("version", nargs="?", help="A specific release such as v5; without it every queued/building deployment is canceled")
    cancel.set_defaults(func=cmd_cancel)

    domain = sub.add_parser("domain", help="Domains")
    domain_sub = domain.add_subparsers(dest="domain_cmd", required=True)
    domain_ls = domain_sub.add_parser("ls")
    _add_app_flag(domain_ls)
    domain_ls.set_defaults(func=cmd_domain_ls)
    domain_add = domain_sub.add_parser("add")
    domain_add.add_argument("hostname")
    _add_app_flag(domain_add)
    domain_add.set_defaults(func=cmd_domain_add)
    domain_check = domain_sub.add_parser("check", help="Required DNS record and its status")
    domain_check.add_argument("hostname")
    _add_app_flag(domain_check)
    domain_check.set_defaults(func=cmd_domain_check)
    domain_rm = domain_sub.add_parser("rm")
    domain_rm.add_argument("hostname")
    _add_app_flag(domain_rm)
    domain_rm.set_defaults(func=cmd_domain_rm)
    domain_https = domain_sub.add_parser("https", help="Automatic http to https redirect")
    domain_https.add_argument("hostname")
    domain_https.add_argument("state", choices=["on", "off"])
    _add_app_flag(domain_https)
    domain_https.set_defaults(func=cmd_domain_https)

    redirect = sub.add_parser("redirect", help="Domain or path redirects (answered before the app)")
    redirect_sub = redirect.add_subparsers(dest="redirect_cmd", required=True)
    redirect_ls = redirect_sub.add_parser("ls")
    _add_app_flag(redirect_ls)
    redirect_ls.set_defaults(func=cmd_redirect_ls)
    redirect_add = redirect_sub.add_parser("add", help="Source: old.example.com, old.example.com/old or /old")
    redirect_add.add_argument("source")
    redirect_add.add_argument("target", help="https://example.com/... or /new")
    redirect_add.add_argument("--host", default="", help="Source domain when the source is only a path (default: all domains)")
    redirect_add.add_argument("--exact", action="store_true", help="Only this path, not paths below it")
    redirect_add.add_argument("--temporary", action="store_true", help="302 instead of 301")
    redirect_add.add_argument("--drop-path", action="store_true", help="Do not append the rest of the path and query to the target")
    _add_app_flag(redirect_add)
    redirect_add.set_defaults(func=cmd_redirect_add)
    redirect_rm = redirect_sub.add_parser("rm")
    redirect_rm.add_argument("id", type=int)
    _add_app_flag(redirect_rm)
    redirect_rm.set_defaults(func=cmd_redirect_rm)

    env = sub.add_parser("env", help="Environment variables")
    env_sub = env.add_subparsers(dest="env_cmd", required=True)
    env_ls = env_sub.add_parser("ls")
    _add_app_flag(env_ls)
    env_ls.set_defaults(func=cmd_env_ls)
    env_set = env_sub.add_parser("set")
    env_set.add_argument("pairs", nargs="*")
    env_set.add_argument("--file", default="", help="Read from a .env file or a JSON key/value export")
    env_set.add_argument("--secret", action="store_true")
    _add_app_flag(env_set)
    env_set.set_defaults(func=cmd_env_set)
    env_rm = env_sub.add_parser("rm")
    env_rm.add_argument("keys", nargs="+")
    _add_app_flag(env_rm)
    env_rm.set_defaults(func=cmd_env_rm)

    db = sub.add_parser("db", help="PostgreSQL, MySQL, MariaDB, MongoDB and Redis")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("ls").set_defaults(func=cmd_db_ls)
    db_create = db_sub.add_parser("create")
    db_create.add_argument("name")
    db_create.add_argument("--engine", required=True, choices=sorted(DB_VERSIONS))
    db_create.add_argument("--version", default="")
    db_create.set_defaults(func=cmd_db_create)
    db_link = db_sub.add_parser("link")
    db_link.add_argument("name")
    _add_app_flag(db_link)
    db_link.add_argument("--key", default="")
    db_link.set_defaults(func=cmd_db_link)
    db_backup = db_sub.add_parser("backup")
    db_backup.add_argument("name")
    db_backup.set_defaults(func=cmd_db_backup)
    db_backups = db_sub.add_parser("backups", help="List backups")
    db_backups.add_argument("name")
    db_backups.set_defaults(func=cmd_db_backups)
    db_download = db_sub.add_parser("download", help="Download a backup")
    db_download.add_argument("name")
    db_download.add_argument("backup", type=int)
    db_download.add_argument("-o", "--output", default="")
    db_download.set_defaults(func=cmd_db_download)
    db_restore = db_sub.add_parser("restore", help="Restore from a backup or a file (e.g. another provider's export)")
    db_restore.add_argument("name")
    db_restore.add_argument("--backup", type=int, default=0)
    db_restore.add_argument("--file", default="")
    db_restore.add_argument("--yes", "-y", action="store_true")
    db_restore.set_defaults(func=cmd_db_restore)
    db_import = db_sub.add_parser(
        "import",
        help="Create a new database from a backup file; engine and version are detected automatically",
    )
    db_import.add_argument("name", help="Name of the new database")
    db_import.add_argument("--file", default="", help="Backup file: pg_dump, mysqldump, mongodump --archive or dump.rdb (gzip too)")
    db_import.add_argument("--url", default="", help="Backup download link; the server downloads it itself (faster than uploading)")
    db_import.add_argument("--engine", choices=sorted(DB_VERSIONS), default="")
    db_import.add_argument("--version", default="", help="Default: the source version")
    db_import.add_argument("--cpu", default="")
    db_import.add_argument("--memory", default="", help="MB")
    db_import.add_argument("--disk", default="", help="GB; defaults from the file size")
    db_import.add_argument("--link", action="store_true", help="Link to the app (dockra.json or --app) after the import")
    db_import.add_argument("--app", default="")
    db_import.add_argument("--key", default="", help="Variable name, e.g. DATABASE_URL")
    db_import.set_defaults(func=cmd_db_import)
    db_connect = db_sub.add_parser("connect", help="Secure tunnel from a local port to the database")
    db_connect.add_argument("name")
    db_connect.add_argument("--port", type=int, default=0)
    db_connect.set_defaults(func=cmd_db_connect)
    for kind in ("stop", "start"):
        power = db_sub.add_parser(kind, help="Stop the database" if kind == "stop" else "Start the database")
        power.add_argument("name")
        if kind == "stop":
            power.add_argument("--yes", "-y", action="store_true")
        else:
            power.set_defaults(yes=True)
        power.set_defaults(func=cmd_db_power(kind))
    db_resize = db_sub.add_parser("resize", help="Change CPU, memory and disk (disk grows only)")
    db_resize.add_argument("name")
    db_resize.add_argument("--cpu", default="")
    db_resize.add_argument("--memory", type=int, default=0, help="MB")
    db_resize.add_argument("--disk", type=int, default=0, help="GB")
    db_resize.set_defaults(func=cmd_db_resize)

    cron = sub.add_parser("cron", help="Scheduled jobs")
    cron_sub = cron.add_subparsers(dest="cron_cmd", required=True)
    cron_ls = cron_sub.add_parser("ls")
    _add_app_flag(cron_ls)
    cron_ls.set_defaults(func=cmd_cron_ls)
    cron_add = cron_sub.add_parser("add", help='e.g. dockra cron add "0 3 * * * python manage.py clearsessions"')
    cron_add.add_argument("line")
    _add_app_flag(cron_add)
    cron_add.set_defaults(func=cmd_cron_add)
    for name, enabled in (("enable", True), ("disable", False)):
        toggle = cron_sub.add_parser(name)
        toggle.add_argument("id", type=int)
        _add_app_flag(toggle)
        toggle.set_defaults(func=cmd_cron_set(enabled))
    cron_rm = cron_sub.add_parser("rm")
    cron_rm.add_argument("id", type=int)
    _add_app_flag(cron_rm)
    cron_rm.set_defaults(func=cmd_cron_rm)

    upgrade = sub.add_parser("upgrade", help="Update dockra from the same server")
    upgrade.add_argument("--api", default="")
    upgrade.add_argument("--force", action="store_true")
    upgrade.set_defaults(func=cmd_upgrade)

    open_cmd = sub.add_parser("open", help="Open the app URL in the browser")
    _add_app_flag(open_cmd)
    open_cmd.set_defaults(func=cmd_open)

    exec_cmd = sub.add_parser("exec", help="Run a command inside the container: dockra exec -- python manage.py migrate")
    _add_app_flag(exec_cmd)
    exec_cmd.add_argument("command", nargs=argparse.REMAINDER)
    exec_cmd.set_defaults(func=cmd_exec)
    shell = sub.add_parser("shell", help="Terminal inside the app container")
    _add_app_flag(shell)
    shell.add_argument("--line", action="store_true", help="Line mode without a TTY")
    shell.set_defaults(func=cmd_shell)

    disk = sub.add_parser("disk", help="Disks and their files")
    disk_sub = disk.add_subparsers(dest="disk_cmd", required=True)
    disk_ls = disk_sub.add_parser("ls")
    _add_app_flag(disk_ls)
    disk_ls.set_defaults(func=cmd_disk_ls)
    disk_files = disk_sub.add_parser("files", help="List the files of a disk")
    disk_files.add_argument("disk")
    disk_files.add_argument("path", nargs="?", default="/")
    _add_app_flag(disk_files)
    disk_files.set_defaults(func=cmd_disk_files)
    disk_download = disk_sub.add_parser("download", help="Download a file or folder (folders as tar.gz)")
    disk_download.add_argument("disk")
    disk_download.add_argument("path", nargs="?", default="/")
    disk_download.add_argument("-o", "--output", default="")
    _add_app_flag(disk_download)
    disk_download.set_defaults(func=cmd_disk_download)
    disk_upload = disk_sub.add_parser("upload", help="Upload a local file or folder to a disk")
    disk_upload.add_argument("disk")
    disk_upload.add_argument("local")
    disk_upload.add_argument("--to", default="/")
    disk_upload.add_argument("--contents", action="store_true", help="Folder contents, without the folder itself")
    _add_app_flag(disk_upload)
    disk_upload.set_defaults(func=cmd_disk_upload)
    disk_import = disk_sub.add_parser(
        "import",
        help="Import files into a disk: an archive or folder, a download link, or directly over FTP",
    )
    disk_import.add_argument("disk")
    disk_import.add_argument("--file", default="", help="tar, tar.gz or zip, or a folder (its contents go to the disk root)")
    disk_import.add_argument("--url", default="", help="Archive download link; the server downloads it itself")
    disk_import.add_argument("--ftp", default="", help="ftp://user@host/path or ftps://...; the password is prompted")
    disk_import.add_argument("--workers", type=int, default=8, help="Concurrent FTP connections")
    disk_import.add_argument("--replace", action="store_true", help="Delete current disk files first (a backup is taken)")
    disk_import.add_argument("--yes", "-y", action="store_true")
    _add_app_flag(disk_import)
    disk_import.set_defaults(func=cmd_disk_import)
    disk_backup = disk_sub.add_parser("backup", help="Back up the disk files now")
    disk_backup.add_argument("disk")
    _add_app_flag(disk_backup)
    disk_backup.set_defaults(func=cmd_disk_backup)
    disk_backups = disk_sub.add_parser("backups", help="List disk backups")
    disk_backups.add_argument("disk")
    _add_app_flag(disk_backups)
    disk_backups.set_defaults(func=cmd_disk_backups)
    disk_backup_dl = disk_sub.add_parser("backup-download", help="Download a disk backup (tar.gz)")
    disk_backup_dl.add_argument("disk")
    disk_backup_dl.add_argument("backup", type=int)
    disk_backup_dl.add_argument("-o", "--output", default="")
    _add_app_flag(disk_backup_dl)
    disk_backup_dl.set_defaults(func=cmd_disk_backup_download)
    disk_restore = disk_sub.add_parser("restore", help="Restore a disk from a backup")
    disk_restore.add_argument("disk")
    disk_restore.add_argument("backup", type=int)
    disk_restore.add_argument("--yes", "-y", action="store_true")
    _add_app_flag(disk_restore)
    disk_restore.set_defaults(func=cmd_disk_restore)
    disk_resize = disk_sub.add_parser("resize", help="Grow a disk or change its mount path (size only grows)")
    disk_resize.add_argument("disk")
    disk_resize.add_argument("--size", type=int, default=0, help="GB")
    disk_resize.add_argument("--mount", default="", help="New path inside the container")
    _add_app_flag(disk_resize)
    disk_resize.set_defaults(func=cmd_disk_resize)
    disk_rm = disk_sub.add_parser("rm", help="Delete a disk and its files (backups are kept for 30 days)")
    disk_rm.add_argument("disk")
    disk_rm.add_argument("--yes", "-y", action="store_true")
    _add_app_flag(disk_rm)
    disk_rm.set_defaults(func=cmd_disk_rm)

    webhook = sub.add_parser("webhook", help="Automatic deploys on Git push")
    webhook.add_argument("action", choices=["show", "enable", "disable"])
    _add_app_flag(webhook)
    webhook.set_defaults(func=cmd_webhook)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


def cli(argv=None):
    try:
        code = main(argv)
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    cli()
