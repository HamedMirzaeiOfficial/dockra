# Chapter 7 — Terminal and WebSocket

**English** · [فارسی](../07-terminal.md)

## Goal of this chapter

See how a real interactive terminal (with colors, `vim`, `htop`) gets from the browser into a container, and why this service runs separately from the API.

## Why a separate service?

Gunicorn has a few processes with a few threads each (`--workers 3 --threads 8` in `api/Dockerfile`). A terminal WebSocket connection may stay open for hours. If it ran on Gunicorn, 24 open terminals would lock the API completely. The `terminal` service (`engine/terminal.py`) runs on `asyncio` and the `websockets` library: thousands of open connections in one process, because each connection uses CPU only when it has data.

## Tickets: authentication without sharing a session

The terminal service has no Django session. So how does it know who the user is?

1. The panel calls `POST /api/apps/shop/terminal` (with its normal JWT). The API checks authorization.
2. The API creates a **signed ticket** (`django.core.signing`) holding the kind, the user and the target, valid for 60 seconds, and returns the WebSocket URL.
3. The browser connects to the terminal service with the ticket. The terminal service verifies the signature, **consumes the ticket once** (against replay), and reads the role and organization from the database **again** (the user may have been deactivated in those 60 seconds).

This pattern recurs in every system with separate services: one service authorizes and issues a short-lived, narrowly scoped token; the other service accepts only that.

## pty: why `docker exec -it` is not enough

Programs like `vim` work correctly only when their stdin is a **terminal**, not a pipe. The terminal service:

1. opens a pty (pseudo-terminal): a master/slave pair.
2. runs `docker exec -it ... sh` with the slave as stdin/stdout/stderr, using `setsid` (a new session) and `TIOCSCTTY` (this pty is the session's controlling terminal). Without these, Ctrl+C does not reach the right process.
3. forwards the master's bytes to the WebSocket (binary frames) and back.
4. when the browser window changes size, the message `{"type":"resize","cols":..,"rows":..}` is applied to the pty with `ioctl(TIOCSWINSZ)` so `vim` knows the right size.

One point from a code comment: a **login** shell is not started, because `/etc/profile` on Debian and Alpine overwrites `PATH` and the Django image's `/venv/bin` would be lost.

In the panel, `xterm.js` (`web/components/terminal.tsx`) is the same terminal emulator VS Code uses, and it renders the raw bytes.

## Bytes, not text

Output is sent as **binary** frames, not text. Why? A multi-byte UTF-8 character (for example «ش» is two bytes) can be split between two `read()` calls. If each chunk were decoded separately, the character would be corrupted. By sending raw bytes, `xterm.js` uses its own streaming decoder. The CLI does the same (see the comment "Raw bytes, so multi-byte UTF-8 characters never split" in `cli/dockra.py`).

## Database tunnels

`dockra db connect myapp-db` opens a port on the laptop's `127.0.0.1`. Every TCP connection to it opens a WebSocket with a `tunnel` ticket to the terminal service, which connects to the database's `host:port` on the `dockra` network and relays bytes both ways. In other words: TCP over WebSocket over HTTPS. The result: the database has no port on the internet, yet `psql` and DBeaver on the laptop work directly.

## Live logs and detecting a dropped connection

Live logs read `docker logs -f` of every instance in a thread. The problem: if the user closes the tab and no log lines arrive, the server never notices and the `docker logs` processes linger. The fix: an empty line is sent every 15 seconds; if sending fails, the connection is dead and the processes are killed. It is the heartbeat idea from chapter 2, this time for a network connection.

## Exercises

1. Enter a container with `dockra shell`, open `vim` and resize the terminal window. Now try the same with `dockra shell --line`. What is the difference, and why?
2. In `engine/terminal.py`, find the function that makes a ticket single-use. What happens if two browsers connect with the same ticket at the same time?
3. Open a `psql` connection through `dockra db connect` and on the server use `ss -tnp` to see which process is connected to the database's port 5432.
4. Why a 60-second ticket and not 10 minutes? What would making it even shorter break?
