# Chapter 3 — Zero-downtime deployment

**English** · [فارسی](../03-zero-downtime.md)

## Goal of this chapter

See how a new release comes up without a single user request failing, and why the previous release stays untouched when the new one is broken.

## The simple, wrong way

```
docker stop app && docker rm app && docker run new-image
```

Between `stop` and the moment the new release is ready (which may take 20 seconds) the site is down. Worse: if the new release never comes up, the site stays down.

## Dockra's way: side by side, then replace

`DockerDriver.build_and_run` and `_launch` in `api/engine/drivers.py`:

1. **Build** with BuildKit. If it fails, no container has changed.
2. **Release**: a command such as `python manage.py migrate` runs once in a one-off container with the same env and disks. If it fails: "the current release was not touched".
3. **Launch**: new instances named `dockra-app-<slug>-<release>-<n>` start with **the same Traefik service label**. Traefik immediately spreads traffic across the old and the new ones.
4. **Health check**: `_wait_ready` checks the container state, the image `HEALTHCHECK`, and a direct probe (TCP, or HTTP on `healthCheck.path`) within `DOCKRA_HEALTH_TIMEOUT`.
5. **Success**: the old instances are stopped with `docker stop -t 10` (time to finish in-flight requests), their logs are archived, and they are removed with `rm`.
6. **Failure**: the **new** instances are removed and the error is raised together with their logs.

This pattern is a simple form of **rolling / blue-green** deployment on one machine. Steps 3 to 6 all run under `app_lock` (chapter 2).

## A subtlety: Traefik must be able to reach the app

Every app has its own network (chapter 5). Before anything starts, `_ensure_app_network` makes sure Traefik is attached to that network. If Traefik is recreated (for example by a compose update), it is no longer attached to the app networks and every site times out! That is why `reconnect_routes` reattaches missing networks every 30 seconds. This was a real bug in the project's history (chapter 12).

## Migrations and backward compatibility

During step 3, for a few seconds **both releases** receive traffic, and both are connected to one database on which the new migration has already run. So a migration must be written so that **the old code still works with the new schema**. For example, drop a column in two deployments: first change the code so it no longer uses the column, then drop the column in the next deployment. The same rule applies to Dockra itself (`docs/development.md`).

## Rollback

`_rollback` in `engine/runner.py` starts the stored image of that release through **the same zero-downtime path**, but with `release=""`; migrations are neither re-run nor reverted. A rollback brings back code, not data. That is why backward-compatible migrations matter.

## Cancel

Cancel has two cases (`request_cancel` in the runner):

- **Queued**: the Job and the Deployment end immediately. Simple.
- **Building**: the `docker build` process runs in another worker. The API only sets `cancel_requested=True`. In `_stream` (drivers.py) a watcher thread reads this flag from the database every few seconds; if it is set, it kills the process and raises `BuildCanceled`. The processes communicate **through the database**, not through signals.

## Settings changes are zero-downtime too

Changing env vars, a domain, a disk or resources creates a `Job(sync)` that goes through the same `_launch`. If applying it to the container fails, the API honestly says "saved but not applied" (502), not "success".

## Exercises

1. Read `_wait_ready`. What happens if the app listens on its port but `/health/` returns 500? And if it returns 404? Why was that decision made?
2. Read the test `test_failed_build_keeps_the_previous_release_live` and write a similar test for a failing **release command**.
3. Why `docker stop -t 10` and not `docker kill`? What does a Django app under Gunicorn do on SIGTERM?
4. Design a migration that renames a column without causing an error at any moment of a zero-downtime deployment.
