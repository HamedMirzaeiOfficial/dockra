import base64
import hashlib
import hmac
import secrets
import socket
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from accounts.models import LoginAttempt, TwoFactor

STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def otpauth_url(secret: str, username: str, issuer: str = "Dockra") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits=6&period={STEP}"


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def new_recovery_codes(count: int = 8) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def verify_totp(row: TwoFactor, code: str, save: bool = True) -> bool:
    """Six-digit code or one of the recovery codes. Each code is accepted only once."""
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False
    if len(code) == 6 and code.isdigit():
        now = int(time.time()) // STEP
        for step in (now - 1, now, now + 1):
            if step > row.last_step and hmac.compare_digest(_code_at(row.secret, step), code):
                if save:
                    row.last_step = step
                    row.save(update_fields=["last_step"])
                return True
        return False
    hashed = _hash_recovery(code)
    if hashed in row.recovery_hashes:
        row.recovery_hashes = [item for item in row.recovery_hashes if item != hashed]
        row.save(update_fields=["recovery_hashes"])
        return True
    return False


def confirm(row: TwoFactor, code: str) -> list[str] | None:
    if not verify_totp(row, code):
        return None
    codes = new_recovery_codes()
    row.enabled = True
    row.recovery_hashes = [_hash_recovery(item) for item in codes]
    row.save(update_fields=["enabled", "recovery_hashes"])
    return codes


def needs_otp(user) -> TwoFactor | None:
    row = TwoFactor.objects.filter(user=user, enabled=True).first()
    return row


_proxy_cache: tuple[float, set[str]] = (0.0, set())


def _trusted_proxies() -> set[str]:
    """Trusted proxy IPs (default: the traefik container), re-resolved every minute."""
    global _proxy_cache
    now = time.monotonic()
    if now - _proxy_cache[0] < 60:
        return _proxy_cache[1]
    found: set[str] = set()
    for name in settings.DOCKRA_TRUSTED_PROXIES:
        try:
            found.update(info[4][0] for info in socket.getaddrinfo(name, None))
        except OSError:
            continue
    _proxy_cache = (now, found)
    return found


def _client_ip(request) -> str:
    """Real client IP. X-Forwarded-For is only honoured when the request came from Traefik;
    Traefik discards any value sent from outside and puts the real IP first."""
    remote = request.META.get("REMOTE_ADDR", "") or "-"
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded and remote in _trusted_proxies():
        return forwarded.split(",")[0].strip() or remote
    return remote


def throttle_keys(request, username: str) -> list[str]:
    return [f"ip:{_client_ip(request)}", f"user:{(username or '').strip().lower()}"]


def retry_after(keys: list[str]) -> int:
    """Seconds left until the next attempt is allowed; zero means free."""
    window = timedelta(seconds=settings.DOCKRA_LOGIN_WINDOW_SECONDS)
    since = timezone.now() - window
    limit = settings.DOCKRA_LOGIN_MAX_FAILS
    for key in keys:
        rows = LoginAttempt.objects.filter(key=key, created_at__gte=since).order_by("-created_at")
        if rows.count() >= limit:
            oldest = list(rows.values_list("created_at", flat=True)[:limit])[-1]
            wait = (oldest + window - timezone.now()).total_seconds()
            return max(int(wait) + 1, 1)
    return 0


def record_failure(keys: list[str]) -> None:
    LoginAttempt.objects.bulk_create([LoginAttempt(key=key) for key in keys])
    LoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()


def clear_failures(username: str) -> None:
    LoginAttempt.objects.filter(key=f"user:{(username or '').strip().lower()}").delete()
