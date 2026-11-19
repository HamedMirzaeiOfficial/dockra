"""Per-app request statistics from Traefik's own Prometheus metrics (internal network only).

Traefik exposes cumulative counters; the worker reads them every minute and stores the delta from the previous read.
"""

import re
import urllib.request

from django.conf import settings

_LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([-+0-9.eEInfa]+)')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

# Last cumulative value of each series, to compute minute-by-minute deltas (in this worker's memory).
_previous: dict[tuple, float] = {}


def parse(text: str) -> list[tuple[str, dict, float]]:
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        name, labels, value = match.groups()
        try:
            number = float(value)
        except ValueError:
            continue
        rows.append((name, dict(_LABEL.findall(labels or "")), number))
    return rows


def _slug(service: str) -> str:
    # Each app's Traefik service is named after its slug: "shop@docker"
    return service.split("@", 1)[0]


def _delta(key: tuple, value: float) -> float:
    before = _previous.get(key)
    _previous[key] = value
    if before is None:
        return 0.0
    # Traefik restarted and the counter started from zero.
    return value - before if value >= before else value


def _quantile(buckets: list[tuple[float, float]], total: float, q: float) -> float | None:
    """Estimate a quantile from cumulative histogram buckets with linear interpolation (like histogram_quantile)."""
    if total <= 0 or not buckets:
        return None
    rank = q * total
    previous_bound, previous_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= rank:
            if bound == float("inf"):
                return previous_bound
            span = count - previous_count
            if span <= 0:
                return bound
            return previous_bound + (bound - previous_bound) * (rank - previous_count) / span
        previous_bound, previous_count = bound, count
    return previous_bound


def collect(text: str) -> dict[str, dict]:
    """From the metrics text, per app: requests, errors_5xx, avg_ms and p95_ms between two reads."""
    stats: dict[str, dict] = {}
    buckets: dict[str, dict[float, float]] = {}
    sums: dict[str, float] = {}
    counts: dict[str, float] = {}
    for name, labels, value in parse(text):
        service = labels.get("service")
        if not service or not service.endswith("@docker"):
            continue
        slug = _slug(service)
        if name == "traefik_service_requests_total":
            key = (name, service, labels.get("code", ""), labels.get("method", ""), labels.get("protocol", ""))
            delta = _delta(key, value)
            row = stats.setdefault(slug, {"requests": 0.0, "errors_5xx": 0.0})
            row["requests"] += delta
            if labels.get("code", "").startswith("5"):
                row["errors_5xx"] += delta
        elif name == "traefik_service_request_duration_seconds_bucket":
            bound = float("inf") if labels.get("le") in ("+Inf", "Inf") else float(labels.get("le", "inf"))
            key = (name, service, labels.get("le"), labels.get("code", ""), labels.get("method", ""), labels.get("protocol", ""))
            delta = _delta(key, value)
            per = buckets.setdefault(slug, {})
            per[bound] = per.get(bound, 0.0) + delta
        elif name == "traefik_service_request_duration_seconds_sum":
            key = (name, service, labels.get("code", ""), labels.get("method", ""), labels.get("protocol", ""))
            sums[slug] = sums.get(slug, 0.0) + _delta(key, value)
        elif name == "traefik_service_request_duration_seconds_count":
            key = (name, service, labels.get("code", ""), labels.get("method", ""), labels.get("protocol", ""))
            counts[slug] = counts.get(slug, 0.0) + _delta(key, value)
    for slug in set(stats) | set(counts):
        row = stats.setdefault(slug, {"requests": 0.0, "errors_5xx": 0.0})
        count = counts.get(slug, 0.0)
        row["avg_ms"] = round(sums.get(slug, 0.0) / count * 1000, 1) if count > 0 else None
        ordered = sorted(buckets.get(slug, {}).items())
        p95 = _quantile(ordered, count, 0.95) if count > 0 else None
        row["p95_ms"] = round(p95 * 1000, 1) if p95 is not None else None
        row["requests"] = int(round(row["requests"]))
        row["errors_5xx"] = int(round(row["errors_5xx"]))
    return stats


def scrape() -> dict[str, dict]:
    url = settings.DOCKRA_TRAEFIK_METRICS_URL
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            text = response.read().decode("utf-8", "replace")
    except OSError:
        return {}
    return collect(text)
