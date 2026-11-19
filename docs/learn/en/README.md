# Dockra learning guide

**English** · [فارسی](../README.md)

This guide is written for **study**, not as a quick reference. Its goal is to teach platform and software engineering concepts in depth by reading the real code of this project: how a real PaaS takes code, builds it, replaces a running version without downtime, keeps apps isolated, and what it does when something breaks.

> Precise technical reference: [architecture.md](../../architecture.md), [deployment-lifecycle.md](../../deployment-lifecycle.md), [security.md](../../security.md) and [api.md](../../api.md).

## Prerequisites

- Python and the basics of Django (models, views, migrations)
- JavaScript/TypeScript and the basics of React
- The Linux command line, HTTP and Git
- A first acquaintance with Docker (image, container, volume)

You do not need to know Traefik, BuildKit, cgroups, WebSocket or Tailwind; the chapters explain them.

## Chapters

| # | Chapter | Main topic |
| --- | --- | --- |
| 1 | [The architecture map](01-architecture.md) | one codebase in several roles; why the API has no Docker access |
| 2 | [A job queue on Postgres](02-job-queue.md) | `Job`, atomic claiming, advisory locks, heartbeats, idempotency |
| 3 | [Zero-downtime deployment](03-zero-downtime.md) | build, release, health check, switch, rollback, cancel |
| 4 | [Incremental upload](04-incremental-upload.md) | content-addressed storage, trees, the mirror and hardlinks |
| 5 | [Isolation](05-isolation.md) | a network per app, cgroups and overcommit, ext4 disks on loop devices |
| 6 | [Security](06-security.md) | threat model, JWT with a session version, 2FA, encryption, SSRF, path traversal |
| 7 | [Terminal and WebSocket](07-terminal.md) | one-time tickets, pty, database tunnels, live logs |
| 8 | [Monitoring and alerts](08-observability.md) | Traefik's cumulative counters, quantiles from histograms, the alert state machine |
| 9 | [Databases and backups](09-databases.md) | provisioning, backups with retention, safe restore, detecting dump formats |
| 10 | [The dashboard: design and tokens](10-dashboard.md) | Next.js, CSS tokens, flash-free theming, accessibility |
| 11 | [A dependency-free CLI](11-cli.md) | standard library only, phishing-resistant browser login, resumable upload |
| 12 | [Tests, CI and lessons from bugs](12-testing-and-bugs.md) | the fake driver, machine-independent tests, e2e, real bugs from this project |
| 13 | [Going bilingual: i18n and right-to-left](13-i18n.md) | English text as the key, flash-free rendering, logical classes, API translation, automatic checks |

## Suggested reading order

1. Read chapter 1 and run the project with the Quick start in the README (`seed_demo` creates sample data). Click around the panel.
2. Read chapters 2 to 5 (the platform core) in order, with the files they mention open next to you.
3. Read chapter 6 carefully; then look at the list of "what is not protected" in [security.md](../../security.md).
4. Chapters 7 to 11 and 13 in whatever order interests you.
5. Read chapter 12 last and run the tests yourself; break one on purpose.

Every chapter ends with a few **exercises**. Actually do them; that is where most of the learning happens.

## How to read the code

- **Start from the contract, not the details.** Look at [api.md](../../api.md) first, then the corresponding view.
- **Follow one request end to end.** For example, follow "deploy" from `cmd_deploy` in `cli/dockra.py` to `DockerDriver._launch` in `api/engine/drivers.py`.
- **Tests are executable documentation.** When you are unsure what a function does exactly, read its test in `api/engine/test_*.py`.
- **Play with the fake driver.** Everything works without Docker; with `DOCKRA_EAGER=0` and `run_worker` you can watch the queue with your own eyes.
