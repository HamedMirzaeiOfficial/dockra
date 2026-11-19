# The `dockra` CLI

A single Python file (`cli/dockra.py`) using only the standard library. Install it from your own server — no PyPI, npm or GitHub access needed:

```bash
curl -fsSL https://api.example.com/cli/install.sh | sh      # Linux and macOS
irm https://api.example.com/cli/install.ps1 | iex           # Windows PowerShell
dockra login --api https://api.example.com
```

`dockra` without arguments prints a cheat sheet; `dockra <command> --help` documents every flag. In a folder with `dockra.json` (or `liara.json`), the app ID is read from its `app` field; otherwise pass `--app`.

## Getting started

| Command | Description |
| --- | --- |
| `dockra login` | opens the browser; approve the request whose code matches the terminal. `--username/--password` for headless use |
| `dockra init <app>` | write `dockra.json` |
| `dockra deploy` | create the app if needed, upload changed files only and follow the build; `--full` uploads the whole folder |
| `dockra apps` | list apps |
| `dockra open` | open the app URL |
| `dockra upgrade` | update the CLI from the server (verified by sha256) |

## Releases

| Command | Description |
| --- | --- |
| `dockra deployments` | release history |
| `dockra logs [-f]` | build and container logs; `-f` streams live |
| `dockra rollback v3` | roll back to a release |
| `dockra cancel [v5]` | cancel a queued or building deployment (all of them without a version) |
| `dockra restart` / `stop` / `start` | lifecycle |

## Configuration

| Command | Description |
| --- | --- |
| `dockra env set KEY=VALUE …` / `--file .env` | set variables (a JSON key/value export is accepted too) |
| `dockra env rm KEY …` | remove variables |
| `dockra domain add app.example.com` | add a custom domain and show the DNS record |
| `dockra domain check app.example.com` | check the record |
| `dockra domain https app.example.com on` | force HTTPS |
| `dockra redirect add old.example.com https://example.com` | redirect a domain (path kept); `/old /new` for paths, `--exact`, `--temporary`, `--drop-path` |
| `dockra cron add "0 3 * * * python manage.py clearsessions"` | scheduled job |
| `dockra webhook enable` | print the webhook URL and secret for GitHub/GitLab/Gitea |

## Runtime access

| Command | Description |
| --- | --- |
| `dockra shell` | interactive terminal in the container (`--line` for line mode on Windows) |
| `dockra exec -- python manage.py migrate` | run one command |

## Databases

| Command | Description |
| --- | --- |
| `dockra db create myapp-db --engine postgres` | also `mysql`, `mariadb`, `mongodb`, `redis`; `--version` |
| `dockra db link myapp-db` | write `DATABASE_URL` (or `REDIS_URL`) into the app; `--key` for another name |
| `dockra db connect myapp-db` | secure tunnel to `127.0.0.1:<port>` |
| `dockra db backups` / `download` / `restore` | backups; restore takes a safety backup first |
| `dockra db import new-db --file dump.sql.gz --link` | create a database from a dump file (engine and version detected) or `--url` |
| `dockra db resize` / `stop` / `start` | resources and lifecycle |

## Disks

| Command | Description |
| --- | --- |
| `dockra disk files media` | list files |
| `dockra disk upload media ./uploads --to /` | upload a file or folder (`--contents` for the folder's contents) |
| `dockra disk download media /uploads` | download a file or a folder as tar.gz |
| `dockra disk import media --ftp ftp://user@host/` | pull files on the server over FTP (parallel, incremental), from `--url` or an archive `--file`; `--replace` empties the disk first (after a backup) |
| `dockra disk backup` / `backups` / `backup-download` / `restore` | disk backups |
| `dockra disk resize media --size 10` | grow a disk or change its mount path |
| `dockra disk rm media` | delete a disk (its backups stay 30 days) |

## Environment

| Variable | Description |
| --- | --- |
| `DOCKRA_API` | API URL (overrides the stored login) |
| `DOCKRA_TOKEN` | API token for CI (create one in Settings → API tokens) |

The CLI stores its login in `~/.dockra.json`, created with mode 600 (owner read/write only).
