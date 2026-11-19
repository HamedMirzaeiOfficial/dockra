# The `dockra.json` manifest

Place it at the project root. The server reads `dockra.json`, falling back to `liara.json`, so projects using that format work unchanged. Every field except `app` is optional and the file is re-read on every deployment.

```json
{
  "app": "shop",
  "platform": "django",
  "port": 8000,
  "release": "python manage.py migrate --noinput",
  "healthCheck": { "path": "/health/" },
  "django": { "pythonVersion": "3.12", "collectStatic": true },
  "packages": ["ffmpeg"],
  "disks": [{ "name": "media", "mountTo": "/app/media", "sizeGb": 5 }],
  "cron": ["*/10 * * * * python manage.py clearsessions"]
}
```

## General fields

| Field | Type | Description |
| --- | --- | --- |
| `app` | string | app ID: lowercase letters, digits and dashes, starting with a letter, at most 40 characters |
| `name` | string | display name when `dockra deploy` creates the app |
| `platform` | string | `django`, `node`, `nextjs`, `static` or `docker` |
| `port` | number | port the app listens on inside the container. Defaults: django and docker 8000, node and nextjs 3000, static 80 |
| `git` | string | Git repository URL when the app is created (for webhook deploys) |
| `start` | string | start command instead of the platform default |
| `release` | string or list | runs once before traffic reaches the new release; failure stops the deployment and the current release stays. A list is joined with `&&` |
| `healthCheck` | `{"path": "/…"}` or string | HTTP path used to judge health; any response below 500 is healthy. Without it, an open port is enough |
| `packages` | list | apt packages for Django builds (e.g. `ffmpeg`, `libmagic1`) |
| `disks` | list | `name` (letters, digits, `_.-`), `mountTo` (path in the container), `sizeGb` (default 1). Removing an entry never deletes the disk |
| `cron` | list | five schedule fields followed by the command. When present, it replaces every cron job of the app on each deployment |

## Django

| Field | Default | Description |
| --- | --- | --- |
| `django.pythonVersion` (or `pythonVersion`) | auto | `3.6`–`3.14`; `3.10.4` is accepted and rounded. Image `python:3.X-slim-bookworm` (`bullseye` for 3.6/3.7). Without it: `runtime.txt`, then `.python-version`, then the Django version pinned in `requirements.txt` (e.g. `Django==2.2` → 3.9, `3.2` → 3.10, `4.2` → 3.12), finally 3.12 |
| `django.collectStatic` | `true` | run `collectstatic --noinput` before Gunicorn. When unset, a failure does not stop the app; `false` disables it. The platform serves `STATIC_ROOT` and `MEDIA_ROOT` under `STATIC_URL` and `MEDIA_URL` regardless of `DEBUG`, without WhiteNoise in the project |
| `django.compileMessages` | `false` | run `compilemessages` before start |

The template creates a venv with `requirements.txt` and Gunicorn, runs as a non-root user (uid 1000) and adds a `HEALTHCHECK`. Gunicorn uses the project's `wsgi.py` (or `config.wsgi`) with workers = memory / 200 MB (1–4); `WEB_CONCURRENCY` in the app env overrides it. `libpq5` is installed automatically when `psycopg2` (not binary) is required; if wheels fail to build, the install is retried with `build-essential`.

## Node.js and Next.js

| Field | Default | Description |
| --- | --- | --- |
| `nodeVersion` (or `node.version`) | `20` | image `node:<version>-alpine` |
| `build` (or `node.build`) | nextjs: `npm run build` | build command |
| `start` (or `node.start`) | node: `npm start`; nextjs: `next start` on the port | start command |

`npm ci` is used with a `package-lock.json`, `npm install` otherwise (dev dependencies are installed for the build). `PORT` is set to `port`.

## Static sites

```json
{ "app": "site", "platform": "static", "static": { "root": "dist", "spa": true } }
```

| Field | Default | Description |
| --- | --- | --- |
| `static.root` (or `root`) | `.` | folder with the files |
| `static.spa` | `false` | serve `index.html` for unknown paths (client-side routers) |

Served by nginx with gzip. Build the folder before `dockra deploy`, or use `nextjs`/`docker` to build on the server.

## Docker

With `"platform": "docker"` the project's own `Dockerfile` is built and `port` must be the port inside the container. Use it for PHP, Go, Rust or anything else. On isolated networks remove `# syntax=` lines.

## Cron syntax

```text
┌ minute (0-59)
│ ┌ hour (0-23)
│ │ ┌ day of month (1-31)
│ │ │ ┌ month (1-12)
│ │ │ │ ┌ day of week (0-7, 0 and 7 are Sunday)
* * * * * command
```

Each field accepts `*`, a number, a range (`1-5`), a step (`*/15`) or a list (`1,15,30`); values outside the field's range are rejected. Jobs run in the platform time zone (`DOCKRA_TIME_ZONE`, UTC by default) inside the app's first container with `sh -c`, for at most `DOCKRA_CRON_TIMEOUT` seconds. A run is skipped if the previous one is still going. The last output and status are shown in the panel.
