# Chapter 14 — Challenge stories: from symptom to lesson

**English** · [فارسی](../14-challenge-stories.md)

## Goal of this chapter

Chapter 12 went through the bugs briefly. This chapter goes slower: each story starts from a **symptom** a user or operator sees, follows it to the **root cause**, shows the **fix** that is in the code today, and ends with a **lesson** and a question.

> These stories are reconstructed from the project's own code and comments; wherever a comment says "without this, X would happen", there is a story behind it. The scenes are told as narratives, but the root cause and the fix are exactly what you will find in the named file. Keep that file open while you read.

A suggestion: before reading the "root cause" part of each story, spend a few minutes guessing. A wrong guess is part of learning too.

---

## 1. The backup that sent Redis into a crash loop

**Scene.** A user is migrating from another service. They take the `dump.rdb` file, create a Redis 7.0 in Dockra and run an import. The job finishes "successfully", but the Redis container comes up and dies every few seconds. The logs are full of restarts and the user's app cannot connect to Redis.

**Root cause.** An RDB file is a versioned binary format. The first five bytes are `REDIS` and the next four are the format version (e.g. `REDIS0011`). Each Redis release reads RDB only up to a certain version: 7.0 up to 10, 7.2 up to 11, 7.4 up to 12. The source service ran a newer Redis. Redis 7.0 does not recognize the file at startup and exits, and Docker's restart policy repeats that again and again. The nasty part: copying the file worked; the problem only showed **after** the copy, when the container started.

**The fix in the code.** `engine/drivers.py`:

- `RDB_MAX` is the "which Redis reads up to which RDB" table, and `RDB_MIN_REDIS` is its inverse for the error message.
- `_rdb_version` reads the first nine bytes of the file (after decompressing, if it is gzip).
- In `restore_database`, the `redis` branch compares **before** `docker stop`, and if the file is newer it rejects it with a message that says exactly which version is needed.

The same idea exists for Postgres: an older `pg_restore` cannot read a newer `pg_dump` file, and the code translates the `unsupported version` message into an understandable error.

**Lesson.** Start a destructive operation (replacing current data) only after **fully validating the input**. File formats often carry their version in the first few bytes; reading those bytes is much cheaper than a crash loop.

**Question.** What does `_rdb_supported` return for a version missing from `RDB_MAX` (say, Redis 8)? If Redis 8 brings RDB format 13, where does the code need to change?

---

## 2. Postgres 18 and the path that moved under our feet

**Scene.** Everything works for Postgres 12 to 17. One day a user picks version 18 and the container never starts. Not a single line of Dockra's code has changed.

**Root cause.** From version 18 the official Postgres image keeps the database data in `/var/lib/postgresql/<version>/docker`, not in `/var/lib/postgresql/data`. If the disk is mounted on the old path, the image errors out on purpose, so nobody accidentally creates the database outside its disk. In other words, an **external dependency** changed its contract.

**The fix in the code.** `db_data_path` in `engine/drivers.py` parses the version with `_version_tuple` and, for 18 and later, mounts `/var/lib/postgresql` itself. Existing databases on earlier versions keep the old path and are not moved.

There is a related decision in `databases/present.py`: a database version is accepted only if it is **numeric** (the regex on line 125). If `latest` were accepted, nobody would know whether it means 17 or 18 today, and the mount path, backup compatibility and upgrades would all become ambiguous.

**Lesson.** Tags like `latest` mean "this system's behavior changes without anyone changing the code". Anything behavior depends on (paths, file formats, compatibility) should come from a **specific** version.

**Question.** If a user wants to upgrade a 17 database to 18, is changing the `version` field enough? What has to move?

---

## 3. Styles that vanished with every deployment

**Scene.** A Django project works after its first deployment. A few days later it is deployed again with a small change, and now the pages have no CSS and the Django admin looks broken.

**Root cause.** Django gathers static files into `STATIC_ROOT` with `collectstatic`. To keep the image small and safe, Dockra puts `staticfiles` in `.dockerignore` (`engine/source.py`, where `.dockerignore` is written), because build output should not come from the user's laptop. So unless someone runs `collectstatic` **inside** the container, every release has an empty `STATIC_ROOT`. And gunicorn does not serve static files.

**The fix in the code.** `_django_dockerfile` in `engine/source.py`:

- `collectstatic` runs by default in the release step (the `dockra-start.sh` file). If the project has not set `collectStatic`, its failure does not stop the deployment (`|| echo ...`); if it is explicitly `true`, a failure is taken seriously.
- `DJANGO_WSGI` (the `dockra_wsgi.py` file) wraps the user's app in WhiteNoise so `/static/` and `/media/` are served from disk regardless of `DEBUG` and the project's middleware. For media, `autorefresh` is on, because uploaded files appear after the app starts.

**Lesson.** When you remove something for a good reason (a clean image), ask "who was relying on it?". And make defaults **lenient**, explicit settings **strict**.

**Question.** Why is `autorefresh` off for static but on for media? What does it cost?

---

## 4. Two gunicorn versions and a pip that gave up

**Scene.** An old project has `gunicorn==20.1.0` in its `requirements.txt`. The build breaks with a long pip error about "ResolutionImpossible".

**Root cause.** Dockra needs gunicorn to run the app and added it to the install list (`gunicorn>=22,<24`). When the project also pins its own version, pip sees two contradictory constraints and no version satisfies both.

**The fix in the code.** In `_django_dockerfile`, if `requirements.txt` has a line starting with `gunicorn`, Dockra adds nothing and the project's choice wins. The same logic applies to `whitenoise`. And for Python 3.6, which does not know gunicorn 22, the range `>=20.1,<22` is chosen.

Another layer of the same story: a project that does not declare its Python version may not install at all on a new default Python. `_python_version` looks at `dockra.json`, `runtime.txt`, `.python-version` and finally at the **Django version**, picking the highest officially supported Python for it from the `DJANGO_PYTHON` table.

**Lesson.** A platform that "adds something" to the user's environment should assume the user has already decided. Make additions **conditional**, not mandatory.

**Question.** The gunicorn detection uses `^\s*gunicorn\b`. What happens if the project installs gunicorn through `-r requirements/base.txt`?

---

## 5. Hardlinks: a copy that was not a copy

**Scene.** No user sees this one, but it is the most dangerous story in the chapter. Imagine an app's second build, after a successful first one, finding strange files in the mirror folder: a `Dockerfile` the user never uploaded.

**Root cause.** Chapter 4 explained that each app has a "mirror" of its files and that a cheap copy is made for each build with **hardlinks** (`link_tree` in `engine/blobs.py`). A hardlink is not a copy: it is two names for **the same inode**. If the build code later overwrites a file in the build folder with `open(path, "w")`, the content of that same inode changes and the mirror is polluted too. The next build, assuming "the mirror is exactly what the user uploaded", produces wrong files, and because the mirror state only stores hashes, it never notices.

**The fix in the code.** Both sides are protected:

- `_write` in `engine/source.py` calls `path.unlink(missing_ok=True)` before writing. Unlink removes only **this name**, and the following write creates a fresh inode. The mirror stays intact.
- `sync_mirror` in `engine/blobs.py` calls `_clear` before replacing a mirror file. Otherwise writing to the mirror file would change a running build that links to the same inode.

**Lesson.** Optimizations that create **sharing** (hardlinks, copy-on-write, caches, shared mutable objects in memory) carry a hidden contract: "nobody writes in place". Make that contract explicit in code (one `_write` function everyone uses) and write down why in a comment.

**Question.** If `os.link` fails in `link_tree` (e.g. two different filesystems), it falls back to `copy2`. Is the `unlink` in `_write` still needed in that case? Does it hurt?

---

## 6. The terminal that did not know `python`

**Scene.** A user opens the panel's terminal and types `python manage.py migrate`. Answer: `ModuleNotFoundError: No module named 'django'`. Yet the same app is running right now with that same Django.

**Root cause.** Dockra's Python images install packages into `/venv` and put `/venv/bin` first on `PATH`. But if the terminal opens a **login shell** (`bash -l`), `/etc/profile` on Debian and Alpine runs and rewrites `PATH`. `/venv/bin` is lost and `python` points to the system Python, which has no Django.

**The fix in the code.** `_shell` in `engine/terminal.py`:

```python
# Not a login shell: /etc/profile on Debian and Alpine rewrites PATH and the image's /venv/bin would be lost.
script = "command -v bash >/dev/null 2>&1 && exec bash || exec sh"
```

bash if present, otherwise sh, but neither as a login shell. The container's environment stays untouched.

**Lesson.** "The same container" does not mean "the same environment". Every layer (login shell, sudo, cron, `su -`) may rewrite environment variables. When two execution paths behave differently, compare `env` in both first.

**Question.** Cron jobs in Dockra run through `exec_cron`. Do they face the same risk? Look for the answer in `engine/drivers.py`.

---

## 7. FTPS that was FTPS in name only

**Scene.** A user wants to bring their site's files from their old host to a Dockra disk. The old host's panel says "FTPS access", so they enter an `ftps://...` address. The connection fails right away with `500 AUTH not understood`.

**Root cause.** Explicit FTPS starts with the `AUTH TLS` command. Some providers call their service FTPS, but their server is plain FTP in practice and rejects `AUTH`. The user has no way of knowing that.

**The first fix.** `_connect` in `engine/disk_import.py`: if `auth()` failed with `error_perm`, it closed the connection, turned `tls` off and reconnected over plain FTP, then wrote in the job log that it continued without TLS. The user's problem was solved.

**A fix that was itself a problem.** That fix opened a security hole: someone on the network path can answer `AUTH` with `500` themselves and force the connection to continue unencrypted. The FTP username and password were then sent in plain text, and the log message was written **after** the password had been sent. This is called a **downgrade** attack (the thing HSTS prevents for HTTPS). The hole was found while writing this chapter.

**The fix today.** Now, if the user asked for `ftps://` and the server rejects `AUTH TLS`, `_connect` closes the connection and raises a `DiskImportError` saying the server has no TLS and that, if that is really so, they should knowingly enter an `ftp://` address. Plain FTP still works, but only when the user **chooses** it. The test `test_ftps_never_downgrades_to_plain_ftp` in `engine/test_disk_import.py` locks this in: the fake server counts `login` calls and the test proves no password is sent after `AUTH` is rejected.

**Lesson.** "If it cannot be secure, continue insecurely" is almost always a downgrade attack. The insecure choice should be **explicit**, not automatic. And a second lesson: any fix that works around a security check deserves the same scrutiny as the bug itself.

**Question.** Read the current error message in `config/locale/fa.json` (and its English key). Would a non-technical user know what to do? Write a better one.

---

## 8. The variable that silently pointed to another database

**Scene.** An app is linked to a Postgres database "main" (`DATABASE_URL`). The user creates a second database called "analytics" and presses link. The default variable name for Postgres is that same `DATABASE_URL`. A success message appears. A few minutes later the main app comes up with empty tables and users think their data has been deleted.

**Root cause.** Linking means "write the database address into an env var". Writing with `update_or_create` on the `DATABASE_URL` key **replaced** the previous value. Nothing had been deleted; the app was just connected to a different database. From the user's point of view, there was no difference.

**The fix in the code.** `DatabaseLinkView` in `databases/present.py` checks, before writing, whether this variable name in this app is already linked to **another** database. If so, it returns 400 and even suggests an alternative name (`DATABASE_URL_2`).

**Lesson.** An operation that looks like "create" but is implemented with `update_or_create` can actually be a "replace". Wherever the key is chosen by the user (or a default), check for collisions explicitly. The most dangerous bugs are the ones that show a **success message**.

**Question.** What happens if the user first set `DATABASE_URL` by hand in the env tab to an external value and then presses link? Does the current check catch that?

---

## 9. "Ready" before it was ready

**Scene.** A user creates a MySQL, the panel says it is ready, they link the app and the app crashes with `Can't connect to MySQL server`. A minute later everything works.

**Root cause.** On first run, the official MySQL and MariaDB images start a **temporary** server without networking, create the user and database, shut it down and then start the real server. If you check readiness with a connection over the socket inside the container, the temporary server answers and you say "ready" too early.

MongoDB had its own version of this: releases 4.4 and 5 do not ship `mongosh`, only the legacy `mongo` shell. A readiness check that only called `mongosh` would never succeed on those versions.

**The fix in the code.** `_db_ping` in `engine/drivers.py`:

- For MySQL/MariaDB it connects with `-h127.0.0.1`, over **TCP**. The temporary server does not listen on TCP, so a successful connection means the real server is up.
- For Mongo, `"$(command -v mongosh || echo mongo)"` runs whichever exists and checks the output of `ping`, not just the exit code.
- `_wait_db` tries up to 120 times, once a second.

**Lesson.** Measure "ready" through the same path the real consumer uses (here: the network). A health check that takes a shortcut answers for the shortcut, not for the real question.

**Question.** The Postgres check uses `pg_isready -h 127.0.0.1`. Postgres also runs a temporary server during init; does TCP play the same role there? Check the official image's documentation.

---

## 10. The script that did nothing and succeeded

**Scene.** A new disk is created for an app. The "create disk" step is green, but the app container fails to start with a confusing `bind source path does not exist` error. Whoever reads the log suspects Docker first, not a disk that was created "successfully". (And if the mount used `-v`, the worse case would happen: Docker would create an ordinary empty folder, the app would write to the root disk and there would be no size limit at all.)

**Root cause.** Dockra creates disks with a shell script on the **host**: a privileged container enters the host's namespaces with `nsenter` and `sh -s` reads the script from stdin (`_host_script` in `engine/volumes.py`). The catch: `docker run` without `-i` does **not** attach stdin to the container. `sh -s` sees empty input, runs nothing and exits with code **zero**. No error, no log.

**The fix in the code.** One letter, `-i`, plus a comment so nobody "cleans it up":

```python
# -i is required: sh -s reads the script from stdin and docker does not forward it without -i.
```

And other defensive layers: the script starts with `set -eu`, and `_bind` in `drivers.py` uses `--mount` (not `-v`) so that if the disk is not mounted the container does not start at all (story 8 in chapter 12).

**Lesson.** "Exit code zero" means "no error was reported", not "the work was done". We saw the same thing with `psql` in chapter 12. For important operations, check the **result**: after creating a disk, does `/proc/mounts` show it?

**Question.** `ensure_volume` does not check after running the script that the mount actually happened. Where should such a check go, and what test does it need?

---

## 11. A corrupt gzip file from an "obvious" line

**Scene.** Before the old container is removed on each deployment, its full log is compressed and archived. But when the user opens the archive they get `gzip: invalid compressed data`.

**Root cause.** The most natural code is this:

```python
with gzip.open(dest, "wb") as handle:
    subprocess.run(["docker", "logs", cid], stdout=handle)
```

But `subprocess` only takes the Python object's **file descriptor** (`handle.fileno()`). The fd of a `GzipFile` is the raw file on disk. So `docker logs` writes **uncompressed** text straight into the file, and `GzipFile` wraps its own gzip header and trailer around it. The result: a file that is neither gzip nor text.

**The fix in the code.** `archive_container` in `engine/logs.py` brings the output into Python with `stdout=PIPE` and writes it into the `GzipFile` with `shutil.copyfileobj`, with a `Timer` that kills `docker logs` if it takes more than 120 seconds. If the output is empty or the exit code is non-zero, the partial file is deleted. And an archive failure never breaks a deployment.

**Lesson.** Know the boundary between a "Python object" and an "operating system resource". Every wrapper (gzip, encryption, buffering) exists only inside Python; another process only sees the fd.

**Question.** Why does log archiving "never break a deployment"? Which failure is worse: a deployment stopped because of a log, or a lost log?

---

## 12. The blob deleted right between two requests

**Scene.** A developer runs `dockra deploy`. The CLI asks which files are needed and the answer is "none, we have them all". Moments later, the deploy request is rejected with a "blob missing" error.

**Root cause.** Incremental upload (chapter 4) has two steps: 1) the CLI sends the list of hashes and the server says which ones it lacks, 2) the CLI uploads only those and then requests the deploy. Meanwhile `gc` in `engine/blobs.py` deletes blobs that no recent deployment refers to. An old blob, used only in a very old deployment, was reported as "present" in step 1 and deleted before step 2.

**The fix in the code.** Two pieces working together:

- `missing()` calls `os.utime(path)` on every blob that exists. "I saw you" means "you are fresh".
- `gc` only deletes blobs whose mtime is older than `min_age_seconds` (one day by default), and also counts the trees of `queued` and `building` deployments as references.

So there is a one-day window between "it exists" and "deploy".

**Lesson.** This is a **race between two processes** with no lock, and it does not need one: it is solved with a **lease**. Wherever "check" and "use" happen at two separate times (TOCTOU), ask "what if someone else changes something in between?".

**Question.** What happens to this logic if the server's system clock is set backwards? And forwards?

---

## Common patterns

Put these twelve stories side by side and a few patterns keep repeating:

| Pattern | Stories |
| --- | --- |
| Fully validate input before destructive work | 1, 8 |
| Measure success by the **result**, not by an exit code or a success message | 8, 10, 11 |
| External dependencies change; pin versions explicitly | 2, 4, 9 |
| Make hidden assumptions (shared inodes, PATH, fds) explicit in code | 5, 6, 11 |
| The world changes between "check" and "use" | 12 |
| The fix for one problem may itself be a new problem | 7 |
| A comment that says "why" keeps a bug from coming back | all |

The last one matters most. Almost every one of these stories is just a one-line comment in the code today. That one line is the only thing stopping someone who does not know the story from "cleaning it up".

## Exercises

1. Find a "why" comment in `engine/` that is not covered in this chapter or chapter 12 (for example `traffic.py`, the Traefik counter that resets to zero after a restart). Write its story in the same format: scene, root cause, fix, lesson, question.
2. Write a test for story 5: after `link_tree` and a `_write` to a file in the build folder, the mirror's content must not have changed. Then remove the `unlink` from `_write` and watch the test turn red.
3. In story 7, temporarily put the old fallback back into `_connect` and see which assertion of `test_ftps_never_downgrades_to_plain_ftp` turns red. Why is counting `login` calls more reliable than checking the log text?
4. Answer the question in story 10: add a check after `_host_script` that proves the disk is mounted. How would you test that check with the fake driver?
