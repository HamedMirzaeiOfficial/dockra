# Chapter 4 — Incremental upload and content-addressed storage

**English** · [فارسی](../04-incremental-upload.md)

## Goal of this chapter

Understand why the second deployment of a 500 MB project uploads only a few kilobytes, and how that works with hashes, a "mirror" on the server and hardlinks.

## The problem

The simple way: tar.gz the whole folder every time and send it. For a Django project with a few hundred static and media files, every deployment uploads tens of megabytes; on a slow or unstable connection it takes minutes, and one interruption means starting over. Yet between two deployments usually only a handful of files change.

## The idea: addressing by content

If every file on the server is named after **the hash of its content** (`sha256`), two questions become trivial:

- "Do you have this file?" = "Do you have a file with this hash?"
- Two files with identical content (even in two different apps) are stored only once.

This is the same idea behind Git and the BuildKit cache: **content-addressed storage**.

## A three-step protocol

`cli/dockra.py` and `api/engine/blobs.py`:

1. **sync**: the CLI sends the list `{path, hash, size, mode}` of every file. `blobs.missing()` returns the hashes the server lacks.
2. **blobs**: the CLI sends only those files, in tar.gz batches of at most 32 MB; each tar member is named after its hash. `store_tar` **hashes again** on receipt and rejects anything that does not match (never trust the client).
3. **deploy**: the CLI sends the same list with `deploy`. The server stores the list itself as a blob (`save_tree`), and `Deployment.tree` is its hash.

If a blob disappeared between steps 1 and 3, the server answers `409 {missing}` and the CLI sends it again. The protocol is **repeatable**: every step can safely be run again.

## A hash cache on the client

Hashing a thousand files takes time too. The CLI keeps a cache in `~/.cache/dockra`: if a file's `size` and `mtime` have not changed, the previous hash is reused. Git and make rely on the same assumption.

## GC: when is something deleted?

Blobs cannot be deleted right after a deployment (a rollback does not need them because it uses the image, but the next deployment wants to know what the server already has). Every hour, `gc()` deletes blobs that:

- are not in the tree of any of the last `DOCKRA_BLOB_KEEP_DEPLOYS` deployments of any app,
- are not in the tree of a queued deployment,
- and are older than one day.

A subtle race: the CLI runs step 1, the server says "I have everything", and right before step 3 the GC deletes that old blob. The fix: `missing()` **refreshes the mtime** of existing blobs, which resets the "older than a day" condition. See the comment around line 79 of `blobs.py`.

## The mirror and hardlinks

From the blobs, the builder has to create a real folder for `docker build`. Writing a thousand files from scratch every time is slow. So:

- `sync_mirror` keeps a **mirror** of the latest tree for each app (`work/mirror/<slug>/`) and writes only the changed files. Its state lives in `<slug>.state.json`; if the work is interrupted, the state stays cleared and the mirror is rebuilt from scratch next time (safer than a half-correct state).
- `link_tree` builds each deployment's build folder from the mirror with **hardlinks**: two names for one inode, no data copied.
- The danger: if the build template wrote `Dockerfile` directly, then, because it is a hardlink, **the mirror would change too**! That is why `source.py` first `unlink`s generated files and only then writes them. It is one of those bugs you only see once you really understand what a hardlink is.

## Exercises

1. Deploy a project twice with `dockra deploy` and compare the output. Then change one file. How many bytes were uploaded?
2. Imagine the hash check in `store_tar` were removed. How could an attacker with the developer role change the code of **another** app? (Hint: blobs are shared between apps.)
3. Run `ls -li` on a file in the mirror and on the same file in a build folder (with the docker driver). Compare the inode numbers.
4. Why must paths in a tree never contain `..`? Where does `blobs.py` check this?
