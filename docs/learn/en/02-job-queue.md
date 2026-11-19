# Chapter 2 — A job queue on Postgres

**English** · [فارسی](../02-job-queue.md)

## Goal of this chapter

Understand how a reliable job queue is built on an ordinary database table: no job runs twice, two jobs of the same app never run at the same time, and if a worker dies mid-job the system does not get stuck.

## Why not Redis or RabbitMQ?

A single-server platform already has Postgres. Adding a broker means one more service to install, monitor and back up. More importantly: when a Job is created **in the same transaction** as the state change (say, a new `Deployment`), you can never end up with the row created but the queue message lost, or the other way round. That is the well-known **dual write** problem. The cost: workers have to poll every second, and throughput is limited; for a few hundred jobs an hour that is plenty.

## The `Job` model

Open `api/engine/models.py`. The important fields:

| Field | Purpose |
| --- | --- |
| `kind` | deploy, rollback, backup, restore, … (which handler runs) |
| `queue` | `build`, `ops` or `rpc` (which kind of worker picks it up) |
| `target` | `app:<slug>` or `db:<id>`: a logical lock |
| `status` | `queued` → `running` → `done`/`failed` |
| `heartbeat_at` | the last time the worker said "still alive" |
| `cancel_requested` | the user asked to cancel |

## Claiming a job atomically

`_claim` in `engine/runner.py`:

```python
busy = set(Job.objects.filter(status=RUNNING).exclude(target="").values_list("target", flat=True))
for job_id, target in rows.order_by("id").values_list("id", "target")[:200]:
    if target and target in busy:
        continue
    claimed = Job.objects.filter(pk=job_id, status=QUEUED).update(status=RUNNING, ...)
    if claimed:
        return job_id
```

The key line is the `update`: `UPDATE ... WHERE id=? AND status='queued'`. If two workers see the same row at the same time, the database tells only one of them "one row changed". The other gets zero and moves on to the next row. This is **compare-and-set** on a row, and it makes a double run impossible without any explicit lock.

One subtle problem remains: two builders read `busy` at the same moment, both see that `app:shop` is free, and each claims **a different job** of the same app. Compare-and-set does not help here because the rows differ. So on Postgres the whole selection runs under a short lock:

```python
with transaction.atomic():
    cursor.execute("SELECT pg_advisory_xact_lock(7302)")
    job_id = _claim(queues)
```

`pg_advisory_xact_lock` is a lock with an arbitrary name (a number) that is held until the transaction ends. It serializes only the "selection" step, not the execution of the job.

## A lock on an app's containers

Separately from the queue, `engine/locks.py::app_lock` uses `pg_advisory_lock(7301, app_id)` to guarantee that anything which swaps an app's containers (a build, a settings sync, a rollback) never runs alongside another such action. Without it, a build could remove the containers a sync had just created, treating them as the "old release".

## Heartbeats and recovery

A worker can die mid-job (OOM, server restart). The Job then stays `running` forever and the app stays "deploying" forever; and because the `target` is busy, no other deployment can start either. The solution:

1. `_Heartbeat` (in `_execute`) is a thread that refreshes `heartbeat_at` every 15 seconds.
2. Every 30 seconds, `recover_stale_jobs` marks jobs silent for more than `DOCKRA_JOB_STALE_SECONDS` as `failed` and releases the related app or database.

Notice that `recover_stale_jobs` uses the same `filter(status=RUNNING).update(...)` pattern, so if two workers run it at the same time each job is "recovered" only once. This property is called **idempotency**: running it again does not change the result. The `FailedJobRecoveryTests` in `engine/test_platform.py` check exactly this.

## Why failed deployments are not retried

A failed automatic backup is retried an hour later, but a failed deployment is not. Why? Backups usually fail because of a transient condition (a full disk, the network), while builds usually fail because of the code (a syntax error, a bad migration), and retrying only wastes resources. A retry decision always depends on **the kind of failure**, not on a blanket rule.

## Exercises

1. With `DOCKRA_EAGER=0`, call `POST /api/apps/shop/deploy` twice and look at the `Job` table in `python manage.py shell`. Run `run_worker --queues build` and follow the order of execution.
2. Temporarily delete the line `if target and target in busy: continue` in `_claim` and run `test_two_deploys_of_one_app_never_run_together`. What happens? What would have happened in the real world?
3. Why do `rpc` jobs have no `target`? If they had one, what would it feel like for a user who types `ls` in the console in the middle of a ten-minute build?
4. Suppose `DOCKRA_JOB_STALE_SECONDS=10` but the heartbeat runs every 15 seconds. Which bug appears?
