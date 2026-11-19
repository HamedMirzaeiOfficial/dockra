"""Interactive terminal (docker exec -it under a pty) and database tunnel over WebSocket.

The API hands out a short-lived signed ticket; this service runs separately from gunicorn so
long-lived connections never tie up API threads.
"""

import asyncio
import fcntl
import json
import os
import pty
import secrets
import struct
import subprocess
import termios
import time
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.core import signing
from config.i18n import tr

SALT = "dockra-terminal"
TICKET_SECONDS = 60
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 4 * 3600

_used: dict[str, float] = {}


def make_ticket(kind: str, user_id: int, target: int) -> str:
    return signing.dumps({"k": kind, "u": user_id, "t": target, "n": secrets.token_hex(8)}, salt=SALT)


def read_ticket(raw: str) -> dict | None:
    """A ticket is accepted only once and only within 60 seconds."""
    try:
        data = signing.loads(raw, salt=SALT, max_age=TICKET_SECONDS)
    except signing.BadSignature:
        return None
    now = time.monotonic()
    for nonce, at in list(_used.items()):
        if now - at > TICKET_SECONDS * 2:
            _used.pop(nonce, None)
    if data.get("n") in _used:
        return None
    _used[data["n"]] = now
    return data


def public_url(request, kind: str, ticket: str) -> str:
    base = settings.DOCKRA_TERMINAL_PUBLIC_URL
    if not base:
        secure = request.is_secure()
        host = request.get_host()
        name, _, port = host.partition(":")
        # On the raw API port the terminal service is on the next port; behind Traefik it is the /ws path.
        if port == "8000":
            host = f"{name}:{settings.DOCKRA_TERMINAL_PORT}"
        base = f"{'wss' if secure else 'ws'}://{host}/ws"
    return f"{base.rstrip('/')}/{kind}?ticket={ticket}"


def _resize(fd: int, cols: int, rows: int) -> None:
    cols = max(10, min(int(cols), 500))
    rows = max(5, min(int(rows), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _controlling_tty():
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _db(func, *args):
    from asgiref.sync import sync_to_async

    return sync_to_async(func, thread_sensitive=False)(*args)


def _load_shell(data: dict):
    from django.db import close_old_connections

    from accounts.models import Membership
    from apps_core.models import App
    from audit.models import AuditLog
    from engine.drivers import get_driver

    close_old_connections()
    app = App.objects.filter(pk=data["t"]).first()
    # The membership in the app's own organization: a user can belong to several, each with its own role.
    member = (
        Membership.objects.filter(user_id=data["u"], organization_id=app.organization_id).select_related("user").first()
        if app is not None
        else None
    )
    if app is None or member is None:
        return None, "Access denied"
    if member.role == Membership.Role.VIEWER:
        return None, "Viewers have no terminal access"
    ids = get_driver()._containers(app, running_only=True) if settings.DOCKRA_DRIVER == "docker" else []
    if not ids:
        return None, "The app container is not running"
    AuditLog.objects.create(organization=app.organization, actor=member.user, action="terminal", target=app.slug)
    return ids[0], ""


def _load_logs(data: dict):
    from django.db import close_old_connections

    from accounts.models import Membership
    from apps_core.models import App

    close_old_connections()
    app = App.objects.filter(pk=data["t"]).first()
    member = (
        Membership.objects.filter(user_id=data["u"], organization_id=app.organization_id).first() if app is not None else None
    )
    if app is None or member is None:
        return None, "Access denied"
    return app, ""


def _load_db(data: dict):
    from django.db import close_old_connections

    from accounts.models import Membership
    from audit.models import AuditLog
    from databases.models import Database

    close_old_connections()
    database = Database.objects.filter(pk=data["t"]).first()
    member = (
        Membership.objects.filter(user_id=data["u"], organization_id=database.organization_id).select_related("user").first()
        if database is not None
        else None
    )
    if database is None or member is None:
        return None, "Access denied"
    if member.role == Membership.Role.VIEWER:
        return None, "Viewers have no database access"
    if not database.host:
        return None, "The database is not ready yet"
    AuditLog.objects.create(organization=database.organization, actor=member.user, action="database tunnel", target=database.name)
    return (database.host, database.port), ""


async def _shell(ws, cid: str) -> None:
    master, slave = pty.openpty()
    _resize(master, 120, 32)
    # Not a login shell: /etc/profile on Debian and Alpine rewrites PATH and the image's /venv/bin would be lost.
    script = "command -v bash >/dev/null 2>&1 && exec bash || exec sh"
    proc = subprocess.Popen(
        ["docker", "exec", "-it", "-e", "TERM=xterm-256color", cid, "sh", "-c", script],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=_controlling_tty,
        close_fds=True,
    )
    os.close(slave)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue = asyncio.Queue()
    last_input = time.monotonic()

    def readable():
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        output.put_nowait(data)
        if not data:
            loop.remove_reader(master)

    loop.add_reader(master, readable)

    async def pump_out():
        while True:
            data = await output.get()
            if not data:
                return
            await ws.send(data)

    async def pump_in():
        nonlocal last_input
        async for message in ws:
            if isinstance(message, bytes):
                os.write(master, message)
                last_input = time.monotonic()
                continue
            try:
                event = json.loads(message)
            except ValueError:
                continue
            if event.get("type") == "input":
                os.write(master, str(event.get("data") or "").encode())
                last_input = time.monotonic()
            elif event.get("type") == "resize":
                _resize(master, event.get("cols") or 120, event.get("rows") or 32)

    async def watchdog():
        started = time.monotonic()
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            if now - last_input > IDLE_SECONDS or now - started > MAX_SECONDS:
                return

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in()), asyncio.create_task(watchdog())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        try:
            loop.remove_reader(master)
        except (ValueError, OSError):
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), 3)
            except asyncio.TimeoutError:
                proc.kill()
        os.close(master)
    try:
        await ws.send(json.dumps({"type": "exit", "code": proc.returncode}))
    except Exception:
        pass


async def _tunnel(ws, target: tuple[str, int]) -> None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(*target), 10)
    except (OSError, asyncio.TimeoutError) as exc:
        await ws.send(json.dumps({"type": "error", "detail": tr("Could not connect to the database: {exc}", exc=exc)}))
        return

    async def up():
        async for message in ws:
            if isinstance(message, bytes):
                writer.write(message)
                await writer.drain()

    async def down():
        while True:
            data = await reader.read(65536)
            if not data:
                return
            await ws.send(data)

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        writer.close()


async def _logs(ws, app, tail: int) -> None:
    """Live logs of all instances; when the WebSocket closes, the docker logs processes are killed."""
    from engine.drivers import get_driver

    loop = asyncio.get_running_loop()
    chunks: asyncio.Queue = asyncio.Queue(maxsize=1000)
    closed = False

    def produce():
        stream = get_driver().stream_logs(app, tail)
        try:
            for chunk in stream:
                if closed:
                    break
                asyncio.run_coroutine_threadsafe(chunks.put(chunk), loop).result()
        finally:
            stream.close()
            asyncio.run_coroutine_threadsafe(chunks.put(None), loop)

    worker = loop.run_in_executor(None, produce)

    async def drain_input():
        async for _message in ws:
            pass

    reader = asyncio.create_task(drain_input())
    try:
        while True:
            getter = asyncio.create_task(chunks.get())
            done, _pending = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                getter.cancel()
                break
            chunk = getter.result()
            if chunk is None:
                await ws.send(json.dumps({"type": "exit"}))
                break
            if chunk.strip():
                await ws.send(chunk.encode("utf-8", "replace"))
    finally:
        closed = True
        reader.cancel()
        # The producer notices the closed connection on the next empty line (at most 15 seconds) and kills the processes.
        try:
            while not worker.done():
                chunks.get_nowait()
        except asyncio.QueueEmpty:
            pass


async def handle(ws) -> None:
    parts = urlsplit(ws.request.path)
    kind = parts.path.rstrip("/").rsplit("/", 1)[-1]
    ticket = (parse_qs(parts.query).get("ticket") or [""])[0]
    data = read_ticket(ticket)
    if data is None or data.get("k") != kind or kind not in ("terminal", "tunnel", "logs"):
        await ws.send(json.dumps({"type": "error", "detail": tr("Invalid or expired ticket")}))
        return
    if kind == "logs":
        app, error = await _db(_load_logs, data)
        if error:
            await ws.send(json.dumps({"type": "error", "detail": error}))
            return
        try:
            tail = max(0, min(int((parse_qs(parts.query).get("tail") or ["200"])[0]), 5000))
        except ValueError:
            tail = 200
        await _logs(ws, app, tail)
    elif kind == "terminal":
        cid, error = await _db(_load_shell, data)
        if error:
            await ws.send(json.dumps({"type": "error", "detail": error}))
            return
        await _shell(ws, cid)
    else:
        target, error = await _db(_load_db, data)
        if error:
            await ws.send(json.dumps({"type": "error", "detail": error}))
            return
        await _tunnel(ws, target)


def _background(stop) -> None:
    """Immediate jobs (console, logs, disk preparation) the API waits on, plus this service's heartbeat."""
    import socket
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections, connection

    from engine.models import Job
    from engine.runner import beat, process_next_job, watch_services

    name = f"terminal@{socket.gethostname()}"

    def rpc_loop():
        while not stop.is_set():
            try:
                worked = process_next_job([Job.Queue.RPC])
            except Exception:
                worked = False
                close_old_connections()
            stop.wait(0.05 if worked else 0.2)
        connection.close()

    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")
    for _ in range(4):
        pool.submit(rpc_loop)

    def beat_loop():
        while not stop.is_set():
            try:
                beat(name, "terminal", "rpc")
                watch_services()
            except Exception:
                close_old_connections()
            stop.wait(15)

    threading.Thread(target=beat_loop, daemon=True).start()


async def serve(host: str, port: int) -> None:
    import threading

    from websockets.asyncio.server import serve as ws_serve

    stop = threading.Event()
    _background(stop)
    try:
        async with ws_serve(handle, host, port, max_size=2 ** 20, ping_interval=20, ping_timeout=40):
            await asyncio.Future()
    finally:
        stop.set()
