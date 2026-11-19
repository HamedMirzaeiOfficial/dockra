# Contributing to Dockra

Thanks for taking the time to contribute! Bug reports, documentation fixes and pull requests are all welcome.

## Getting set up

Follow [docs/development.md](docs/development.md). The fake driver lets you run the API, the dashboard and the CLI without Docker.

## Before opening a pull request

- `cd api && python manage.py test` passes and `python manage.py makemigrations --check --dry-run` reports nothing.
- `ruff check api cli` passes.
- `cd web && npx tsc --noEmit && npm run i18n:check && npx next build` passes.
- Behavior changes come with a test, especially anything touching authorization, the job queue, deployments, secrets or file paths.
- UI changes use the design tokens and components described in [docs/design-system.md](docs/design-system.md); attach a screenshot in both themes.
- New user-facing text is translated: `t("…")` in the panel and `tr("…")` in the API, with a Persian entry in `web/lib/locales/fa/` or `api/config/locale/fa.json` ([how](docs/development.md#translations-english-and-persian)). Use logical Tailwind classes (`ms-`, `pe-`, `start-`) so the layout mirrors in Persian.
- Docs are updated when an API contract, a setting or a CLI command changes; the Persian copies in `docs/fa/` too when you can (otherwise say so in the pull request).

## Guidelines

- Keep views thin; business rules belong in `engine/` and `*/validation.py`.
- Migrations must be additive and backward compatible (the platform updates itself without downtime).
- Never commit secrets, `.env` files, database dumps or local databases.
- Small, focused commits in Conventional Commits style (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).

## Reporting security issues

Please do **not** open a public issue. See [SECURITY.md](SECURITY.md).
