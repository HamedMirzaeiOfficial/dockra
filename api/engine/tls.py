import socket
import ssl
import time

from django.conf import settings

_cache: dict[str, tuple[float, str]] = {}


def probe_certificate(hostname: str) -> str:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=2) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as tls:
                return "active" if tls.getpeercert() else "pending"
    except ssl.SSLCertVerificationError:
        return "error"
    except (TimeoutError, socket.timeout, OSError, ssl.SSLError):
        return "pending"


def sync_certificates(domains, force: bool = False) -> None:
    if settings.DOCKRA_DRIVER != "docker":
        return
    now = time.monotonic()
    for domain in domains:
        cached = _cache.get(domain.hostname)
        if not force and cached and now - cached[0] < 45:
            status = cached[1]
        else:
            status = probe_certificate(domain.hostname)
            _cache[domain.hostname] = (now, status)
        if domain.ssl_status != status:
            domain.ssl_status = status
            domain.save(update_fields=["ssl_status"])
