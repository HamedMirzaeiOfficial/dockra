"""App redirects as Traefik labels (redirectregex middleware).

Each redirect gets a router on web and one on websecure with a higher priority than the app's own router,
so the request is answered before it reaches the container. Names contain `_`, which app slugs cannot,
so they never collide with another app's router.
"""

import re

from django.conf import settings

from apps_core.models import Redirect

PRIORITY_PREFIX = 10000
PRIORITY_EXACT = 20000


def regex_and_replacement(item: Redirect) -> tuple[str, str]:
    """Group 1 is the origin (scheme://host) and group 2 the remainder after the source path."""
    if item.match == Redirect.Match.EXACT:
        regex = rf"^(https?://[^/]+){re.escape(item.source_path)}(\?.*)?$"
    else:
        regex = rf"^(https?://[^/]+){re.escape(item.source_path.rstrip('/'))}(.*)$"
    target = item.target
    if target.startswith("/"):
        target = "${1}" + target
    if item.keep_path and not (item.match == Redirect.Match.EXACT and "?" in target):
        target = target.rstrip("/") + "${2}" if item.match == Redirect.Match.PREFIX else target + "${2}"
    return regex, target


def _rule(item: Redirect, hosts: list[str]) -> str:
    host_rule = " || ".join(f"Host(`{host}`)" for host in hosts)
    path = item.source_path
    if item.match == Redirect.Match.EXACT:
        path_rule = f"Path(`{path}`)"
    elif path == "/":
        return host_rule
    else:
        # PathPrefix(`/old`) also matches /older; make the path boundary explicit.
        path_rule = f"(Path(`{path}`) || PathPrefix(`{path}/`))"
    return f"({host_rule}) && {path_rule}"


def redirect_labels(app, domains) -> list[str]:
    hosts = sorted(item.hostname for item in domains)
    labels: list[str] = []
    for item in app.redirects.all():
        # If the source domain was removed the redirect stays inactive until it is added back.
        sources = [item.source_host] if item.source_host else hosts
        if not sources or (item.source_host and item.source_host not in hosts):
            continue
        name = f"{app.slug}_redirect{item.id}"
        regex, replacement = regex_and_replacement(item)
        base = PRIORITY_EXACT if item.match == Redirect.Match.EXACT else PRIORITY_PREFIX
        priority = base + len(item.source_path)
        labels += [
            f"traefik.http.middlewares.{name}.redirectregex.regex={regex}",
            f"traefik.http.middlewares.{name}.redirectregex.replacement={replacement}",
            f"traefik.http.middlewares.{name}.redirectregex.permanent={'true' if item.permanent else 'false'}",
        ]
        for router, entrypoint in ((name, "web"), (f"{name}-s", "websecure")):
            labels += [
                f"traefik.http.routers.{router}.rule={_rule(item, sources)}",
                f"traefik.http.routers.{router}.entrypoints={entrypoint}",
                f"traefik.http.routers.{router}.priority={priority}",
                f"traefik.http.routers.{router}.middlewares={name}",
                f"traefik.http.routers.{router}.service={app.slug}",
            ]
        labels.append(f"traefik.http.routers.{name}-s.tls=true")
        if settings.DOCKRA_TLS_RESOLVER:
            labels.append(f"traefik.http.routers.{name}-s.tls.certresolver={settings.DOCKRA_TLS_RESOLVER}")
    return labels
