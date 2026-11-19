# Chapter 11 — A dependency-free CLI

**English** · [فارسی](../11-cli.md)

## Goal of this chapter

See why `dockra` is a Python file that uses **only the standard library**, what that costs, and the security and networking details a good CLI has to get right.

## Why no dependencies?

The goal was a CLI that installs on any machine with Python, **even without access to PyPI or GitHub** (restricted networks, earlier chapters). So:

- `curl -fsSL https://api.example.com/cli/install.sh | sh` fetches `dockra.py` **from the server itself** (`accounts/cli_dist.py`).
- The server sends an `X-Dockra-Sha256` header, and `dockra upgrade` checks the downloaded file against it.
- For Windows without Python, the `cli-release.yml` workflow builds a single executable with PyInstaller.

The cost: HTTP with `http.client`, a complete hand-written WebSocket client (RFC 6455) in about a hundred lines (`class WebSocket` in `cli/dockra.py`), lengthy argparse setup. In return: zero installation problems.

## Browser login and phishing

`dockra login`:

1. The CLI calls `POST /api/auth/cli/start` → a random code.
2. The browser opens `/cli?code=...`; the user signs in (if needed) and clicks "Connect".
3. The CLI polls every few seconds and, once approved, receives an API token.

The attack: the attacker runs `dockra login` themselves and **sends the approval link to the victim** ("please approve this"). The victim, already signed in to the panel, approves, and the attacker gets a full token for the victim's account. This attack is well known from the OAuth device flow. Dockra's defenses (`accounts/cli_web.py`):

- The approval page shows **the last six characters of the code** that the CLI printed in the terminal; the user should see that they match.
- An explicit warning: "If someone sent you this link, do not approve".
- The code is valid for 10 minutes and can be approved only once.
- Forms carry a CSRF token and the page is served with a strict CSP (`default-src 'none'`).

The page follows the browser's language (English or Persian), so the warning is read in the user's own language.

## The token file

The CLI keeps its token in `~/.dockra.json`. The first version wrote it with `write_text`, that is with the default umask, usually `644`: **any other user on the same machine could read the token**. On shared development servers that is real. Now (`_save_login`):

```python
fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
```

The file is readable only by its owner **from the moment it is created** (writing first and running `chmod` afterwards would leave a small window open). For CI, `DOCKRA_TOKEN` and `DOCKRA_API` are read from the environment so the token is never written to disk. `CliCredentialFileTests` checks both.

## Resumable upload

`_upload_pieces` sends large files (database backups, disk archives) to `/api/uploads` in chunks. The hard question: if a chunk arrived but **its response was lost**, the client thinks it never arrived and sends it again. The server (`engine/uploads.py`) compares each chunk's `offset` with the file's current size; if they do not match, it returns `409` with the current `size` and the client continues from there. Without this, a chunk could be appended twice and the backup would be corrupted. It is chapter 2's idempotency idea applied to a network protocol. Dropped connections are retried with a backoff (a wait of a few seconds).

## Small details that make a difference

- **Smart compression**: SQL and uncompressed archives are gzipped before upload (several times smaller), but media files (jpg, mp4) that are already compressed use level 1 so no time is wasted.
- **FTP password**: asked with `getpass`, not as a command-line argument, so it does not stay in the shell history.
- **sslip.io on restricted networks**: if the sslip name does not resolve, the CLI connects directly to the IP and sends the right `Host` header.
- **Friendly output**: messages say what to do next ("Next: dockra deploy", "run dockra login again").

## Exercises

1. Run `dockra login` and compare the printed code with the code on the browser page. Now open the link in another browser with a different account. What prevents a mistake, and what does not?
2. Read `class WebSocket`. Why is masking client frames necessary? (RFC 6455, section 5.3)
3. Cut the network connection in the middle of uploading a large backup (with `iptables`, or by switching Wi-Fi off) and reconnect. What does the CLI log say?
4. Why is `os.open(..., 0o600)` better than `write_text` followed by `os.chmod`? Write down the race scenario.
