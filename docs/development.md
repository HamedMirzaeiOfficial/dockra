# Development

**English** · [فارسی](fa/development.md)

## Prerequisites

- Python 3.12, Node.js 20+ (22 recommended), Git
- Docker only if you want to run the real driver or the end-to-end test

## Run locally without Docker

The default driver is `fake`: deployments, databases and backups "complete" instantly (`DOCKRA_EAGER=1`) without touching Docker. This is enough for working on the dashboard, the API and the CLI.

```bash
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r api/requirements.txt -r api/requirements-dev.txt
cd api
python manage.py migrate
python manage.py seed_demo          # admin / dockra, sample apps, databases and 7 days of metrics
python manage.py runserver 127.0.0.1:8000
```

`seed` creates only the organization and the admin user; `seed_demo` creates realistic sample data **through the public API** and refuses to run unless `DOCKRA_DRIVER=fake`.

Dashboard:

```bash
cd web
npm ci
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev      # http://127.0.0.1:3000
```

CLI from the repository (code changes apply immediately):

```bash
sh cli/install.sh            # Windows: .\cli\install.ps1, or dockra.cmd in the repo root
dockra login --api http://127.0.0.1:8000
```

Useful settings:

| Variable | Effect |
| --- | --- |
| `SQLITE_PATH` | use a separate SQLite file (default `api/db.sqlite3`) |
| `DOCKRA_DATA_ROOT` | data folder for blobs, disks, media and build folders (default `api/var`, `api/media`, `api/work`) |
| `DOCKRA_DRIVER=docker` | the real driver against your local Docker |
| `DOCKRA_EAGER=0` | queue jobs instead of running them inline; then run `python manage.py run_worker` |
| `DJANGO_DEBUG=0` | production-like mode (needs a real `DJANGO_SECRET_KEY`) |

Terminal service: `python manage.py run_terminal --port 8001`.

## Run locally with Docker

```bash
cd deploy
cp .env.example .env.local
docker compose -p dockra-local --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build traefik db api worker builder terminal
```

Then run the dashboard with `npm run dev` as above, pointing `NEXT_PUBLIC_API_URL` at `http://127.0.0.1:8000`.

## Tests and checks

```bash
cd api
python manage.py test                                   # 179 tests, ~2 minutes, no Docker needed
python manage.py makemigrations --check --dry-run       # models and migrations in sync
cd .. && ruff check api cli                             # lint (configuration in pyproject.toml)

cd web
npx tsc --noEmit                                        # types
npm run i18n:check                                      # every panel string has a Persian entry
npx next build                                          # production build
```

UI smoke test and screenshots (needs a running API with `seed_demo` data and a running panel, plus Playwright):

```bash
npm i -D playwright@1.49 && npx playwright install chromium
PANEL_URL=http://127.0.0.1:3000 node e2e/screenshots.cjs ../docs/screenshots dark       # fails on any console error
PANEL_LANG=fa PANEL_URL=http://127.0.0.1:3000 node e2e/screenshots.cjs ../docs/screenshots dark   # Persian panel (*-fa.png)
```

End to end on real Docker (installs the stack, deploys, redeploys without downtime, rolls back, creates a database and a backup, simulates an isolated network, then removes everything):

```bash
sudo deploy/e2e.sh
```

### What the API tests cover

| File | Focus |
| --- | --- |
| `engine/tests.py` | app and database CRUD, deploy, capacity, cron, viewer restrictions |
| `engine/test_platform.py` | secrets at rest and masking, tenant isolation, concurrent job claiming, stale-worker recovery, failed build keeps the live release |
| `engine/test_operations.py` | cancel and rollback, CLI distribution and login pages, live logs, isolated-network settings |
| `engine/test_hardening.py` | throttling, trusted proxies, disk backups and restore safety, mounts, metrics math |
| `engine/test_features.py` | incremental upload, webhooks, build templates, mirrors, domain checks, SSRF guards |
| `engine/test_validation.py` | disk and redirect validation |
| `engine/test_i18n.py` | response language from `Accept-Language`, Persian labels, CLI page direction, every API message translated |
| `engine/test_import.py`, `test_disk_import.py`, `test_backups.py`, `test_dbadmin.py`, `test_logs.py`, `test_sharing.py`, `test_mirrors.py`, `test_db_observe.py` | feature-specific suites |

Tests never depend on the machine they run on: capacity is overridden with `DOCKRA_HOST_*`, and disk-space reserves with `DOCKRA_RESERVED_DISK_GB=0` where a test needs it.

## Translations (English and Persian)

Both the panel and the API use the same small model: **the English text is the key**, and a Persian dictionary maps it to its translation. A missing entry falls back to readable English, never to a raw key.

**Panel** (`web/`):

```tsx
import { msg, t } from "@/lib/i18n";

<Button>{t("Deploy")}</Button>
<p>{t("{n} files transferred.", { n: fmt(count) })}</p>
const TABS = [["logs", msg("Logs")]];   // msg() marks a constant; render it with t(label)
```

Add the Persian text to the matching file in `web/lib/locales/fa/` (`common`, `apps`, `databases`, `platform`) and run `npm run i18n:check`; `--report` also lists JSX text that is not wrapped in `t()` yet. Rules:

- Never build a sentence from pieces (`"Deleted " + name`); use one key with a placeholder, because word order differs between languages.
- Numbers and dates go through `fmt()`, `relTime()` and `dateTime()` from `lib/format.ts`; they switch to Persian digits and the Solar Hijri calendar.
- Use logical Tailwind classes (`ms-`, `pe-`, `start-`, `text-end`) instead of `ml-`, `pr-`, `left-`, `text-right`, and mark technical values (hostnames, paths, commands, logs) with `dir="ltr"`.

**API** (`api/`):

```python
from config.i18n import tr

return Response({"detail": tr("Disk {name} is full", name=disk.name)}, status=400)
```

Persian entries live in `api/config/locale/fa.json`; `python manage.py i18n_check` (also part of the test suite) fails when one is missing. Model choice labels are translated where they are presented (`tr(obj.get_status_display())`). Work done by the worker has no request and stays English.

## Migrations

Migrations were squashed into a single `0001_initial` per app for the first public release. From now on:

- migrations must be additive and backward compatible (old code keeps running during a zero-downtime platform update);
- data migrations need a reverse operation or an explicit `noop`;
- CI fails if `makemigrations --check` finds pending changes.

## Conventions

- **Python:** ruff (configured in `pyproject.toml`), type hints for new code, business rules in `engine/` and `*/validation.py`, views stay thin.
- **TypeScript:** strict mode, Prettier defaults, colors only from design tokens ([design-system.md](design-system.md)).
- **Commits:** small and focused, Conventional Commits (`feat:`, `fix:`, `docs:` …). Work on a branch; open a pull request.
- **Secrets:** never commit `.env` files, keys, dumps or databases; `.gitignore` covers the usual suspects.

## Releasing the CLI

1. Bump `__version__` in `cli/dockra.py`. Servers serve the repository version, and installed CLIs offer `dockra upgrade`.
2. For standalone binaries, push a tag `cli-v<version>`; `.github/workflows/cli-release.yml` builds them with PyInstaller for Linux, Windows and macOS. Put them in `/var/lib/dockra/cli/` on servers.
