# Chapter 12 — Tests, CI and bug stories

**English** · [فارسی](../12-testing-and-bugs.md)

## Goal of this chapter

See how a platform that touches Docker, disks and the network is tested with 179 tests in two minutes, without Docker, and learn from the real bugs of this very project.

## Dockra's test pyramid

| Layer | Tool | What | Speed |
| --- | --- | --- | --- |
| Unit and integration | `manage.py test` with the fake driver | API, authorization, queue, deployment lifecycle, backups, validation, SSRF, response language | ~2 minutes |
| Lint, types, translations | `ruff`, `tsc`, `npm run i18n:check`, `next build` | code errors, types, missing Persian strings, panel build | seconds |
| UI smoke | `web/e2e/screenshots.cjs` (Playwright) | sign-in and rendering of every page in both themes and in Persian, no console errors | ~1 minute |
| End to end | `deploy/e2e.sh` on real Docker | install, deploy, zero downtime, rollback, database, backup, isolated network | ~20 minutes |

## The fake driver: a seam for testing

Because all Docker work sits behind `get_driver()` (chapter 1), tests get a `FakeDriver` that pretends a "build" with one log line and a "container" with one database row. When a specific behavior is needed, only that method is mocked:

```python
with mock.patch.object(FakeDriver, "build_and_run", side_effect=RuntimeError("build failed")):
    runner._execute(job_id)
```

This test (`test_failed_build_keeps_the_previous_release_live`) proves, without a single container, that a failed build does not touch the live release.

## Machine-independent tests

A good test gives **the same result** on your laptop, on CI and on a colleague's server. Dockra pins host capacity in tests with `@override_settings(DOCKRA_HOST_CPU=16, ...)`. Bug story 1 below shows what happens when this rule is not followed.

## Tests that lock in guarantees

`engine/test_platform.py` was written for this release, and every test in it is a platform **guarantee**, not a function:

- Secrets are encrypted in the database (checked with raw SQL, not through the ORM, which decrypts).
- Another organization cannot even learn that resources exist (404).
- Two deployments of one app are never claimed at the same time; two different apps are.
- A dead worker's job is recovered, and recovery is idempotent.
- The CLI token file is `0600`.

The rule: every security or concurrency bug you fix gets a test that **turns red if the bug comes back**.

---

## Bug stories and their lessons

These are **real** bugs of this project; some were found in the original history (and their traces remain in code comments), others while preparing this open-source release.

### 1. Tests that depended on the machine's disk

**What happened:** nine disk backup tests failed on the development server but were green on the author's machine. The cause: before starting, the backup code checks that the server's free space minus `DOCKRA_RESERVED_DISK_GB` (20 GB by default) is enough. The development server had 18 GB free.

**Lesson:** anything read from the environment (disk, clock, network, core count) must be pinned in tests. The fix: `@override_settings(DOCKRA_RESERVED_DISK_GB=0)` on those classes, plus a **separate** test that checks the space guard itself by mocking `shutil.disk_usage`. Do not remove the guard; make its test independent.

### 2. A project rename that broke a test

**What happened:** after the Docker resource prefix changed to `dockra`, `test_volume_names_cannot_collide_across_apps` went red. A volume name is `<prefix>-disk-<slug>-<name>` with a 61-character limit, and the new prefix was one character longer than the old one.

**Lesson:** length limits built from several strings depend on every part, even constants that "never change". A boundary test (39 characters accepted, 40 rejected) showed it immediately; without it, a user with a long disk name would have hit a strange error in production.

### 3. SSRF in the CDN check

**What happened:** if a domain did not point at the server's IP, the "check DNS record" feature sent a `HEAD /` to the IP it found, to see whether it was behind Cloudflare or another CDN. But the domain's A record is set by **the user**: it could be `10.0.0.5` or `169.254.169.254`, and the API server (on the platform's internal network) would connect to it.

**Lesson:** wherever the server connects to an address the user controls **indirectly** (even through DNS), there is SSRF. The fix: only `ipaddress.ip_address(ip).is_global`. `DomainCheckSsrfTests` tries seven private addresses and checks that no connection is made.

### 4. A CLI token readable by everyone

**What happened:** `~/.dockra.json` was created with the default umask (`644`). On a shared machine, any user could read another user's full API token. (Chapter 11)

**Lesson:** create files that hold secrets with `os.open(..., 0o600)`, not "write, then chmod".

### 5. Sensitive data in "harmless" code

**What happened:** before publishing, the code contained the company server's real IP (as the default in `push.ps1` and in a test), internal domains, a non-standard SSH port and package mirrors specific to one country, all hard-coded. None of them was a "password", but all of it was information that should not be public.

**Lesson:** before open-sourcing, do not only search for `password=`. Search for IPs, domains, e-mail addresses, ports and host names too, and **do not carry over the git history** (this project started from `git archive` and a fresh repository), because a secret committed once is not removed by deleting it from HEAD.

### 6. The mask that (almost) overwrote a secret

**What happened:** the API returns secret values to viewers as `••••••`. If a client sent that same list back with `PUT`, the real secret would be replaced by `••••••`. In practice it never happened, because viewers are not allowed to `PUT`.

**Lesson:** "it does not happen in practice because something else prevents it" means one layer of defense. One more line (if the value is the mask, keep the stored value) is the second layer. **Defense in depth.**

### 7. A recreated Traefik and every site down

**What happened (history):** a compose update recreated the Traefik container. The new Traefik was not attached to the app networks and every site timed out, while every container was healthy.

**Lesson:** state that is configured "once, at creation" is lost when things are recreated. A **reconciliation loop** (`reconnect_routes`, every 30 seconds) that reapplies the desired state is more reliable than any "remember to do Y after X". Kubernetes is built entirely on this idea.

### 8. An empty database after a server reboot

**What happened (history):** after a server reboot, the Postgres container started before the loop disk was mounted. `-v` created an empty folder and Postgres initialized a **fresh, empty** database on it. The apps came up and "all the data was gone" (the data was intact on the disk, just not visible).

**Lesson:** an error is better than silent wrong behavior. `--mount` fails in this situation and the container does not start (`_bind` in `drivers.py`). In addition, after `remount_all`, containers that are not on their real disk are restarted.

### 9. `PathPrefix(/old)` that also caught `/older`

**What happened (history):** a redirect `/old` → `/new` also sent `/older-posts` to `/newer-posts`.

**Lesson:** a "prefix" in a URL means a **path** prefix, not a string prefix. Write the boundary explicitly (`/old` or `/old/...`). The comment in `engine/redirects.py` says exactly that.

### 10. `psql` that "succeeded" with a broken file

**What happened (history):** restoring an incompatible dump ended with exit code zero, and an empty database was recorded as a "successful restore". The cause: `psql` without `ON_ERROR_STOP` keeps going after errors and returns zero.

**Lesson:** do not blindly trust the exit code of external tools; measure the **result** (the number of tables). (Chapter 9)

### 11. A CPU chart that hit 100 % with every cron run

**What happened (history):** `docker stats` is a two-second snapshot; a minutely cron running at that moment showed the chart at 100 % and raised a CPU alert.

**Lesson:** do not alert on instantaneous metrics; compute the average over an interval from a cumulative counter. (Chapter 8)

### 12. A Tailwind class with spaces

**What happened:** while converting colors, a script turned `rgba(112,236,210,0.8)` into `rgb(99 102 241 / 0.8)` inside an arbitrary Tailwind class such as `shadow-[0_8px_24px_-10px_rgb(99 102 241 / 0.8)]`. A space in a class name means **several separate classes**; the shadow silently disappeared. Only the screenshot of the sign-in page revealed it.

**Lesson:** check automatic code transformations with your eyes (or a visual test) too. And instead of repeating arbitrary values, create a token (`shadow-glow`).

### 13. A React hook in a Server Component

**What happened:** `layout.tsx` (a Server Component) imported a constant from a file that also used `useState`. `tsc` was green but `next build` failed. (Chapter 10)

**Lesson:** a type check is not enough; run the real build in CI. That is exactly why `ci.yml` has a `next build` step.

### 14. A "back" arrow that pointed forward, in English

**What happened:** while making the panel bilingual, it turned out that in the app and database creation wizards, the "Back" chevron pointed **right**, even in the English version. It was a leftover from the original right-to-left Persian panel that nobody had noticed after the first conversion to English.

**Lesson:** components should know **meanings**, not directions. The chevron now takes `to="back" | "forward"` and mirrors itself with `rtl:-scale-x-100`. Once you build for both directions, a whole class of these bugs disappears. (Chapter 13)

### 15. Logic that read the translated text

**What happened:** two places made decisions from display text: the usage bar chose its color with `label === "RAM"`, and the upload progress bar appeared only when `phase.startsWith("Uploading")`. In Persian, the label is «رم» and the phase is «در حال آپلود …», so the RAM bar got the wrong color and the progress bar never showed up.

**Lesson:** text is for display; decisions belong to identifiers or state. The progress bar now has its own `uploading` state. (Chapter 13)

## Final exercises

1. Reintroduce bug 3, 4 or 6 in the code and see which test turns red. If none does, write one.
2. Write a test for bug 9 that checks `/older` is **not** redirected by a rule for `/old`.
3. Read the "Known limitations" list in the README and pick one (for example rate limiting for every endpoint). Write an implementation plan: where, with which storage, and which test.
4. Read `deploy/e2e.sh`. Which part simulates a network without internet, and how?
