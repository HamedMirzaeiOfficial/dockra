# Chapter 9 — Databases and backups

**English** · [فارسی](../09-databases.md)

## Goal of this chapter

See what a "managed database" is in practice, and why the backup and restore code is more **careful** than any other part of the project.

## Database = container + disk + encrypted password

`DockerDriver.provision_database` creates a container from the official image (postgres, mysql, mariadb, mongo, redis) with these properties:

- Data on a separate ext4 disk (chapter 5) mounted with `--mount`, so that if the disk is not mounted the container **does not start**, instead of initializing on an empty folder.
- A random password stored encrypted in the platform (`Database.password`, `EncryptedTextField`).
- No port on the host; only on the `dockra` network and the networks of linked apps.
- A lower `oom_score_adj` than apps.

`link` writes the connection URL (`postgres://user:pass@host:port/db`) into the app as a **secret** env var and attaches the database to the app's network. If the same variable name is already linked to another database, it fails; without this check, the app would silently connect to a different database (see the comment in `databases/present.py`).

A versioning detail: since postgres 18 the official image changed its data path (`/var/lib/postgresql/<version>/docker`). The code parses the numeric version and mounts the right path; the `latest` tag keeps the old path so the data of existing databases does not move. That is exactly why only numeric versions are accepted.

## Backups and retention

`_backup` in `engine/runner.py` creates a file with the engine's own tool (`pg_dump -Fc`, `mysqldump`, `mongodump --archive`, `redis-cli --rdb`), optionally sends it to S3 (`engine/offsite.py`, with hand-written SigV4 signing and no SDK), and then `_prune_backups`:

- deletes only **after** a successful backup. If the new backup fails, the old ones stay. The opposite order (delete first, back up second) means that on your worst day you have no backup at all.
- keeps the newest `backup_keep` ready backups.
- deletes old failed backups so the list does not clutter.

Scheduling (`schedule_auto_backups`): a backup that is running, or a successful backup newer than one period, means "not yet". A failed backup does not block the next round; it is retried after `DOCKRA_BACKUP_RETRY_MINUTES`, not 24 hours later. The platform also backs up **its own database** daily (`backup_panel`).

## Restore: a way back at every step

Restore is the platform's most dangerous operation: it replaces the current data. `_restore` has three layers of caution:

1. **Safety backup** (`_safety_backup`): before anything else, the current state is backed up. If that backup fails, the restore **does not happen**: "The safety backup failed; the database was not touched".
2. **Stopping linked apps**: an app writing during a restore creates duplicate rows and breaks primary-key creation. Apps are stopped and then started again.
3. **Checking after the restore**: the number of tables and rows is reported. A subtle bug: `psql` without `ON_ERROR_STOP` returns exit code zero even for a completely incompatible file! So if there were errors and **no table** came back, the job is reported as failed, not as "success with an empty database".

App disks follow the same pattern (`engine/disk_backup.py::restore`): a "before restore" backup, the current files are not deleted until the end (they are moved to `.dockra-old`), and the app is stopped during the swap so it never sees half old and half new files.

## Moving in from anywhere: detecting the format

`dockra db import` takes a dump file and detects the engine and version **without asking** (`engine/dumps.py::detect`):

- It unpacks gzip and tar (some services hand out a tar.gz with one file inside).
- From the first few bytes: `PGDMP` means a pg_dump custom archive (and the source server's version is in the header), `REDIS0011` means RDB version 11 (which needs Redis 7.2 or later), a BSON header means mongodump, and SQL text carries MySQL or Postgres markers.
- `needs_newer` checks that the requested version is not older than the source; an old pg_restore cannot read a newer pg_dump file, and an old Redis cannot load a newer RDB and falls into a crash loop. The error is raised **before** touching any data.

Statements in the source dump that refer to the source's roles or database (`OWNER TO`, `CREATE DATABASE`, GTIDs in MySQL) are filtered out; the equivalent of `--no-owner --no-privileges` for plain SQL.

## Exercises

1. Change `_prune_backups` so it runs **before** the backup, and write a test that shows why that is bad.
2. Create a real `pg_dump -Fc` file and look at its first 20 bytes with `xxd`. Where in the header is the server version?
3. Read the test in `test_import.py` that checks "No tables". Which real scenario does it cover?
4. Suppose the safety backup succeeded but the restore itself failed halfway with a full-disk error. How does the user get back to the previous state? Which button do they press in the panel?
