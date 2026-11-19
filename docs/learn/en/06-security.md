# Chapter 6 — Security

**English** · [فارسی](../06-security.md)

## Goal of this chapter

Learn to start security from a **threat model**, not from a list of tools, and see which threat each of Dockra's security controls answers.

## First: whom are we afraid of?

[security.md](../../security.md) has a table: in scope and out of scope. In scope: an attacker on the internet, a team member going beyond their role, another organization on the same platform, a bug in one app. Out of scope: deliberately malicious code, a hacked server. This split matters, because without it you either protect everything (impossible) or nothing.

## Authorization in one place

`accounts/permissions.py::IsMember`:

```python
if request.method in ("GET", "HEAD", "OPTIONS"):
    return True
needed = getattr(view, "write_role", "developer")
return ROLE_RANK[membership.role] >= ROLE_RANK[needed]
```

And every lookup:

```python
get_object_or_404(App, organization=request.membership.organization, slug=slug)
```

Two lessons:

1. **Secure by default**: if a developer forgets to set `write_role`, writing needs developer, not nothing.
2. **404, not 403**: for a resource of another organization the answer is "not found". A 403 tells the attacker "this ID exists" (enumeration). `TenantIsolationTests` checks exactly that.

## Sessions that are really revoked

A JWT is stateless; its classic problem is that "logout" does nothing and the token stays valid until it expires. Dockra keeps a `SessionVersion` number per user and puts it into the token's `sv` claim (`accounts/authentication.py`). Logout, a password change, a password reset or deactivation bumps the number, and `VersionedJWTAuthentication` rejects any token with an old `sv`. The cost: one small query per request. The result: "sign out everywhere" actually works.

## Login throttling and the real IP

8 failed attempts in 15 minutes per IP **or** per username. Why both? IP only: the attacker guesses from 100 IPs. Username only: the attacker locks everyone's account from one IP… which is still possible, of course; this is the trade-off between brute force and lockout.

The subtle part: where does the IP come from? If we blindly trust `X-Forwarded-For`, the attacker sends a fake IP every time and throttling is useless. `accounts/security.py::_client_ip` accepts this header only when the request comes from Traefik's own IP (`DOCKRA_TRUSTED_PROXIES`), which discards the outside value.

## 2FA done right

TOTP (`accounts/security.py`) has important details: each code is accepted only **once** (`last_step` is stored; this blocks replay), comparison uses `hmac.compare_digest` (constant time, against timing attacks), and recovery codes are stored only as hashes.

## Encryption at rest

`config/crypto.py::EncryptedTextField` encrypts env values, database passwords, webhook secrets and TOTP keys with Fernet. The test `test_secret_values_are_encrypted_at_rest` reads the table with raw SQL and checks that the plain text is not there. But know what it protects against: a leaked backup file of the platform database. It does **not** protect against an attacker on the server itself who has the key.

Env vars reach the container through `--env-file`, not `-e KEY=VALUE`, because arguments are visible in `ps aux` to every user on the server.

## SSRF: when your server makes requests for the attacker

Wherever the server connects to an address **chosen by the user**, there is an SSRF risk: the user supplies `http://169.254.169.254/` (cloud metadata) or `http://db:5432` (the internal database). In Dockra there are two such places:

- **Downloading a backup from a URL** (`engine/dumps.py`): only http/https; the name is resolved, and private, loopback, link-local and reserved IPs are rejected.
- **The domain's CDN check** (`apps_core/views.py::edge_server`): users register their own domain and can point its A record anywhere, for example `10.0.0.5`. The first version sent a HEAD request to that IP! Now only `is_global` IPs are contacted. This bug was found while preparing this open-source release (chapter 12).

## Path traversal

Wherever a file name comes from the user (an uploaded archive, a path in the disk file manager), you must make sure the result does not leave the allowed folder: `../../etc/passwd`, or a symlink pointing at `/`. Dockra:

- extracts archives with Python's `data` filter (`extractall(..., filter="data")`), and in disk backups with `tarfile.tar_filter`, which rejects links leading outside and device files.
- `apps_core/extra.py::_inside` calls `resolve()` on the path (symlinks are followed) and checks that the disk root is one of the result's parents.

## Webhooks

`_hook_authorized` computes the HMAC-SHA256 signature of the body with the app's secret and compares it with `compare_digest`. An app without a secret rejects every webhook (`if not secret: return False`; without that line, `compare_digest("", "")` would be True!). The `?token=` mode (for GitLab) puts the secret in the URL, where it may end up in proxy logs; that is honestly recorded as a limitation.

## What is not protected

Read the "Known limitations" section of [security.md](../../security.md) carefully: Docker is not a sandbox, builds run on the host's daemon, disk creation is privileged, apps have free egress, rate limiting covers sign-in only. Writing this list is not a sign of weakness; it is a sign that the team knows what its system is.

## Exercises

1. Remove the line `if not secret: return False` in `_hook_authorized` and write a test that shows what happens without it.
2. Write a function that checks that `edge_server` does not contact a private IPv6 address (`fd00::1`) either. What does `ipaddress.ip_address("fd00::1").is_global` return?
3. Design a hypothetical endpoint "download the app's favicon from a URL". Which controls does it need? How do you handle redirects? (Hint: DNS rebinding.)
4. Why does the panel keep its token in `localStorage` and not in an HttpOnly cookie? Write down the pros and cons of each and explain the role of the CSP.
