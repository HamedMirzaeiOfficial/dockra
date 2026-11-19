# Chapter 5 — Isolation: networks, resources and disks

**English** · [فارسی](../05-isolation.md)

## Goal of this chapter

See how several apps on one server are kept apart: they cannot connect to each other, they cannot grab all the CPU or memory, and their disks have a real size limit. And know honestly **where this isolation ends**.

## Networks: every app is an island

Docker's default is one shared network in which every container reaches every other container. That means app A could connect directly to app B's Postgres, or even to the platform's own database. Dockra changes this (`_ensure_app_network` and `connect_database` in `drivers.py`):

| Network | Members |
| --- | --- |
| `dockra-app-<slug>` | this app's instances, Traefik, the worker/builder (health checks only), databases **linked to this app** |
| `dockra` | platform services and databases (for tunnels and backups) |
| `dockra-dbadmin-<name>` | one database admin UI + Traefik + that one database |

The consequence: apps cannot reach each other, unlinked databases, or the platform's `api` and `db`. They **can** reach the outside internet, though (there is no egress filtering).

## CPU: a guaranteed share and a burst

`cpu_flags` and `container_flags` in `drivers.py` set two things together:

- `--cpu-shares` (the cgroup weight): when the server is busy, CPU is divided in proportion to weight. A 1-core app weighs twice as much as a 0.5-core app.
- `--cpus` (the ceiling): up to `DOCKRA_CPU_BURST` times its cores while the others are idle.

This is the model cloud providers call "burstable". Idle cores are not wasted, but under load every app gets its share.

## Memory: no swap, with an OOM priority

`--memory` and `--memory-swap` are equal, so there is no swap (swap on a shared server means everyone becomes slow). When the whole server runs out of memory, the kernel has to kill something. `oom_score_adj` sets the order: **apps first, then databases, platform last**. The reasoning: an app is stateless and restarts; a database killed mid-write may need a long recovery.

`--pids-limit` stops fork bombs as well.

## Admission: deliberate overcommit

`engine/resources.py::ensure_capacity` checks before every deployment or resize that the sum of reservations does not exceed capacity. Two subtleties:

- **Overcommit**: apps usually use less than half of their memory limit, so the sum of limits may reach `DOCKRA_MEMORY_OVERCOMMIT` times the memory.
- **But** real memory is checked too: if the server's real usage is above `DOCKRA_MEMORY_REAL_PERCENT`, a new app is rejected. Deciding on reservations alone is optimistic, on usage alone pessimistic; combining them is realistic.

Disk has no overcommit, because disk files really take up space with `fallocate`.

## Disks: a real filesystem with a real limit

An ordinary Docker volume has no size limit; one app can fill the server's whole disk and bring everyone down. For every disk, Dockra (`engine/volumes.py`):

1. creates a file of the exact size with `fallocate`,
2. runs `mkfs.ext4` on it,
3. mounts it through a loop device at `mnt/<name>` and records it in `/etc/fstab`,
4. bind-mounts `mnt/<name>/data` into the container.

Now, when an app's disk is full, the app gets `No space left on device`, not the whole server. Growing is done online (`resize2fs`); shrinking is not supported (it is dangerous).

But all this has to happen on the **host**, not inside a container. The trick: a short-lived privileged container runs `nsenter -t 1 -a` to enter the namespaces of the host's process 1 and runs the script there. This is powerful and dangerous, and it is recorded as a limitation in [security.md](../../security.md).

Another real bug: if a database container starts **before** its disk is mounted (after a server reboot, say), `-v` creates an empty folder and Postgres initializes from scratch on it: an empty database! The fixes: use `--mount`, which fails in that situation (`_bind`), plus `remount_all` and restarting containers that are not on their real disk.

## Names must be isolated too

A volume is named `dockra-disk-<slug>-<name>`. App `shop` with disk `media-x` and app `shop-media` with disk `x` both map to `dockra-disk-shop-media-x` and **see each other's files**. `apps_core/validation.py::check_disk` rejects such collisions. The lesson: wherever you join strings with a separator that is allowed inside those strings, collisions are possible.

## Where does isolation end?

All of this is containers, and containers **share the kernel**. A kernel or runtime vulnerability means a container escape. Dockra is designed for a team running its own code; running code from anonymous users (like a public PaaS) needs another layer: gVisor, Kata Containers or a separate VM. Being honest about this boundary is part of good engineering.

## Exercises

1. With the docker driver, create two apps and from the first app's console `curl` the second app's container name. What happens? Now link one database to both and try again.
2. Create an app with 512 MB of memory and write a Python script in it that allocates 1 GB. What do you see in the log? What does `docker inspect` say?
3. Read the test `test_volume_names_cannot_collide_across_apps`. Why did the length limit of volume names have to change when the resource prefix (`dockra`) became longer? (Chapter 12)
4. Suppose you want to restrict app egress (only to `api.stripe.com`). Where in this architecture would you do it, and with which tool?
