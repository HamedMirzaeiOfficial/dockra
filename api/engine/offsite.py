"""Ship backups to S3-compatible storage outside the server (AWS, MinIO, Backblaze, any S3 service)."""

import hashlib
import hmac
import http.client
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from django.conf import settings


def enabled() -> bool:
    return bool(settings.DOCKRA_S3_ENDPOINT and settings.DOCKRA_S3_BUCKET and settings.DOCKRA_S3_ACCESS_KEY)


def _sign(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def _headers(method: str, host: str, path: str, now: datetime) -> dict:
    region = settings.DOCKRA_S3_REGION
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    # The body is not signed so large files stream without being read twice; TLS provides integrity.
    payload = "UNSIGNED-PAYLOAD"
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{amz_date}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    request = "\n".join([method, path, "", canonical_headers, signed, payload])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = _sign(("AWS4" + settings.DOCKRA_S3_SECRET_KEY).encode(), day)
    key = _sign(_sign(_sign(key, region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={settings.DOCKRA_S3_ACCESS_KEY}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        ),
    }


def upload(path: Path, key: str) -> str:
    """Upload the file with PUT (path-style) and return the final key."""
    endpoint = urlsplit(settings.DOCKRA_S3_ENDPOINT)
    host = endpoint.netloc
    prefix = settings.DOCKRA_S3_PREFIX.strip("/")
    full_key = f"{prefix}/{key}" if prefix else key
    target = "/" + quote(settings.DOCKRA_S3_BUCKET) + "/" + quote(full_key, safe="/")
    headers = _headers("PUT", host, target, datetime.now(dt_timezone.utc))
    size = path.stat().st_size
    conn_cls = http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(endpoint.hostname, endpoint.port, timeout=600)
    try:
        conn.putrequest("PUT", target, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.putheader("Content-Length", str(size))
        conn.putheader("Content-Type", "application/octet-stream")
        conn.endheaders()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                conn.send(chunk)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
    finally:
        conn.close()
    if resp.status >= 300:
        raise RuntimeError(f"Uploading the backup to S3 failed ({resp.status}): {body[:300]}")
    return full_key
