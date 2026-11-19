import json
import secrets
from datetime import datetime, timedelta

from urllib.parse import quote

from django.contrib.auth import authenticate, login
from django.http import HttpResponse, JsonResponse
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.html import escape
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from django.conf import settings

from accounts import security
from accounts.authentication import hash_key
from accounts.models import ApiToken, CliLogin, CliWatch, Membership
from apps_core.models import App
from config.i18n import active_language, tr

PAGE = """<!DOCTYPE html>
<html lang="__LANG__" dir="__DIR__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>__TITLE__</title>
<style>
  :root { --bg: #0a0b10; --card: #111218; --line: #23253a; --text: #e8e9f1; --muted: #8b90a7;
    --accent: #6e6cf6; --accent-ink: #ffffff; --ok: #3ccf91; --bad: #f26d78; --field: #0d0e14; }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f6f7fb; --card: #ffffff; --line: #e3e5ee; --text: #10121c; --muted: #5d6279;
      --accent: #4f46e5; --field: #f9fafc; --ok: #138a5b; --bad: #c8323f; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--text);
    font: 15px/1.6 Inter, Vazirmatn, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Tahoma, sans-serif;
    -webkit-font-smoothing: antialiased; }
  main { width: min(440px, calc(100% - 32px)); background: var(--card); border: 1px solid var(--line);
    border-radius: 14px; padding: 28px; box-shadow: 0 24px 60px -30px rgba(0,0,0,.5); }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 22px; font-weight: 600; letter-spacing: -.01em; }
  .brand svg { width: 26px; height: 26px; }
  h1 { margin: 0 0 6px; font-size: 20px; letter-spacing: -.02em; }
  p { color: var(--muted); margin: 8px 0; }
  b { color: var(--text); font-weight: 600; }
  label { display: block; margin: 14px 0 6px; font-size: 13px; font-weight: 500; }
  input, button { font: inherit; }
  input { width: 100%; border-radius: 8px; border: 1px solid var(--line); background: var(--field); color: inherit;
    padding: 9px 12px; outline: none; }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }
  button { width: 100%; margin-top: 18px; border: 0; border-radius: 8px; padding: 10px; background: var(--accent);
    color: var(--accent-ink); font-weight: 600; cursor: pointer; }
  button:hover { filter: brightness(1.08); }
  .err { color: var(--bad); }
  code, pre { font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; }
  .track { margin: 22px 0 8px; }
  .step { display: flex; align-items: flex-start; gap: 14px; position: relative; padding-bottom: 18px; }
  .step i { position: relative; z-index: 1; flex: 0 0 26px; width: 26px; height: 26px; border-radius: 50%;
    display: grid; place-items: center; background: var(--field); border: 1px solid var(--line); color: var(--muted);
    font-style: normal; font-size: 12px; font-weight: 600; }
  .step b { display: block; font-size: 14px; line-height: 26px; }
  .step em { display: block; margin-top: -3px; color: var(--muted); font-style: normal; font-size: 12px; }
  .step::after { content: ""; position: absolute; top: 26px; bottom: 0; inset-inline-start: 12px; width: 2px; background: var(--line); }
  .step:last-child { padding-bottom: 0; }
  .step:last-child::after { display: none; }
  .step.on i { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .step.ok i { color: var(--ok); border-color: var(--ok); }
  .step.ok b { color: var(--ok); }
  .step.fail i { color: var(--bad); border-color: var(--bad); }
  pre { display: none; white-space: pre-wrap; background: var(--field); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px; max-height: 220px; overflow: auto; font-size: 12px; margin: 16px 0 0; }
  pre.show { display: block; }
</style>
</head>
<body><main><div class="brand">__LOGO__<span>Dockra</span></div>__BODY__</main></body>
</html>"""

LOGO = (
    '<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="dg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#7c7cf8"/><stop offset="1" stop-color="#3b82f6"/></linearGradient></defs>'
    '<rect width="32" height="32" rx="8" fill="url(#dg)"/>'
    '<path d="M10 9h5.5a7 7 0 0 1 0 14H10z" fill="none" stroke="#fff" stroke-width="2.4" stroke-linejoin="round"/>'
    '<path d="M14 18.5l2.5-2.5 2.5 2.5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _fresh(code: str) -> CliLogin | None:
    row = CliLogin.objects.filter(code=code).first()
    if row is None:
        return None
    if timezone.now() - row.created_at > timedelta(minutes=10):
        row.delete()
        return None
    return row


def _h(text: str, **markup: str) -> str:
    """Translated text as HTML: the translation is escaped, then trusted markup fills its {placeholders}."""
    out = escape(tr(text))
    for key, value in markup.items():
        out = out.replace("{" + key + "}", value)
    return out


def _page(title: str, body: str, status: int = 200) -> HttpResponse:
    lang = active_language()
    html = (
        PAGE.replace("__LANG__", lang, 1)
        .replace("__DIR__", "rtl" if lang == "fa" else "ltr", 1)
        .replace("__TITLE__", escape(title), 1)
        .replace("__LOGO__", LOGO, 1)
        .replace("__BODY__", body, 1)
    )
    response = HttpResponse(html, status=status)
    # These pages load nothing from outside and only submit forms or requests to this same API.
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response



class CliWatchStart(APIView):
    def post(self, request):
        row = CliWatch.objects.create(
            code=secrets.token_urlsafe(24),
            user=request.user,
            app_slug=(request.data.get("app") or "").strip()[:40],
            after=(request.data.get("after") or "").strip()[:40],
        )
        return Response({"code": row.code})


@csrf_exempt
@require_POST
def cli_start(request):
    row = CliLogin.objects.create(code=secrets.token_urlsafe(24))
    return JsonResponse({"code": row.code})


@require_GET
def cli_poll(request):
    row = _fresh((request.GET.get("code") or "").strip())
    if row is None:
        return JsonResponse({"detail": tr("The login code has expired")}, status=410)
    if not row.user_id or not row.token:
        return JsonResponse({"status": "pending"})
    token = row.token
    username = row.user.username
    row.token = ""
    row.save(update_fields=["token"])
    return JsonResponse({"status": "ready", "token": token, "username": username})


def _csrf(request) -> str:
    return f"<input type='hidden' name='csrfmiddlewaretoken' value='{escape(get_token(request))}'>"


def short_code(code: str) -> str:
    """Last part of the code, also printed by the CLI, so the user can confirm it is their own request."""
    return code[-6:].upper()


@require_GET
def api_home(request):
    # The API URL appears in install commands and chat apps turn it into a link; show a guide instead of a JSON "not found".
    base = escape(request.build_absolute_uri("/").rstrip("/"))
    body = (
        f"<h1>Dockra</h1><p>{_h('This is the API address and is not meant to be opened in a browser. Run these commands in a terminal on your computer:')}</p>"
        f"<pre class='show' dir='ltr'># Linux / macOS\ncurl -fsSL {base}/cli/install.sh | sh\n\n"
        f"# Windows (PowerShell)\nirm {base}/cli/install.ps1 | iex\n\n"
        f"dockra login --api {base}</pre>"
    )
    return _page("Dockra", body)


@require_GET
@ensure_csrf_cookie
def cli_page(request):
    code = (request.GET.get("code") or "").strip()
    row = _fresh(code)
    if row is None:
        return _page("Dockra", f"<h1>Dockra</h1><p class='err'>{_h('This link has expired. Run dockra login again.')}</p>", 410)
    code = escape(code)
    if row.user_id and request.user.is_authenticated and request.user.id == row.user_id:
        return _page(
            tr("Connected"),
            f"<h1>{_h('Connected')}</h1><p>{_h('The command line is now linked to your account. Close this page and run dockra deploy in your project.')}</p>",
        )
    if row.user_id:
        return _page("Dockra", f"<h1>Dockra</h1><p class='err'>{_h('This request has already been approved.')}</p>", 410)
    if request.user.is_authenticated:
        name = escape(request.user.get_full_name() or request.user.username)
        minutes = max(1, int((timezone.now() - row.created_at).total_seconds() // 60) or 1)
        body = (
            f"<h1>{_h('Connect the command line')}</h1><p>{_h('Link account {name} to a command-line session?', name=f'<b>{name}</b>')}</p>"
            f"<p>{_h('Request code: {code} (about {minutes} min ago)', code=f'<b dir=\'ltr\'>{escape(short_code(row.code))}</b>', minutes=str(minutes))}</p>"
            f"<p class='err'>{_h('Only approve if you just ran {command} yourself and this same code is printed in your terminal. If someone sent you this link, do not approve: they would get full access to your account.', command='<b>dockra login</b>')}</p>"
            f"<form method='post' action='/cli/approve'>{_csrf(request)}<input type='hidden' name='code' value='{code}'>"
            f"<button type='submit'>{_h('Connect')}</button></form>"
        )
        return _page(tr("Connect Dockra"), body)
    body = (
        f"<h1>{_h('Sign in to Dockra')}</h1><p>{_h('Once you sign in, only the connect button remains.')}</p>"
        f"<form method='post' action='/cli/login'>{_csrf(request)}<input type='hidden' name='code' value='{code}'>"
        f"<label>{_h('Username')}</label><input name='username' autocomplete='username' dir='ltr' required>"
        f"<label>{_h('Password')}</label><input name='password' type='password' autocomplete='current-password' dir='ltr' required>"
        f"<label>{_h('Two-factor code (if enabled)')}</label><input name='otp' inputmode='numeric' autocomplete='one-time-code' dir='ltr'>"
        f"<button type='submit'>{_h('Continue')}</button></form>"
    )
    return _page(tr("Sign in to Dockra"), body)


@require_POST
def cli_browser_login(request):
    code = (request.POST.get("code") or "").strip()
    if _fresh(code) is None:
        return _page("Dockra", f"<h1>Dockra</h1><p class='err'>{_h('The login code has expired.')}</p>", 410)
    username = (request.POST.get("username") or "").strip()
    keys = security.throttle_keys(request, username)
    wait = security.retry_after(keys)
    if wait:
        return _page(tr("Sign in to Dockra"), f"<h1>{_h('Sign in')}</h1><p class='err'>{_h('Too many failed attempts; try again in {wait} seconds.', wait=str(wait))}</p>", 429)
    user = authenticate(request, username=username, password=request.POST.get("password") or "")
    if user is None or not Membership.objects.filter(user=user).exists():
        security.record_failure(keys)
        return _page(tr("Sign in to Dockra"), f"<h1>{_h('Sign in')}</h1><p class='err'>{_h('Incorrect username or password.')}</p>", 400)
    two_factor = security.needs_otp(user)
    if two_factor is not None and not security.verify_totp(two_factor, request.POST.get("otp") or ""):
        security.record_failure(keys)
        return _page(tr("Sign in to Dockra"), f"<h1>{_h('Sign in')}</h1><p class='err'>{_h('A two-factor code is required or incorrect.')}</p>", 400)
    security.clear_failures(username)
    login(request, user)
    return redirect(f"/cli?code={quote(code)}")


@require_POST
def cli_approve(request):
    if not request.user.is_authenticated:
        return redirect("/cli")
    code = (request.POST.get("code") or "").strip()
    row = _fresh(code)
    if row is None:
        return _page("Dockra", f"<h1>Dockra</h1><p class='err'>{_h('The login code has expired.')}</p>", 410)
    if row.user_id:
        return _page("Dockra", f"<h1>Dockra</h1><p class='err'>{_h('This request has already been approved.')}</p>", 410)
    raw = "dockra_" + secrets.token_urlsafe(32)
    ApiToken.objects.create(
        user=request.user,
        name="cli",
        prefix=raw[:12],
        key_hash=hash_key(raw),
        expires_at=timezone.now() + timedelta(days=settings.DOCKRA_CLI_TOKEN_DAYS),
    )
    row.user = request.user
    row.token = raw
    row.save(update_fields=["user", "token"])
    return redirect(f"/cli?code={quote(code)}")


def _watch(code: str) -> CliWatch | None:
    row = CliWatch.objects.filter(code=code).select_related("user").first()
    if row is None:
        return None
    if timezone.now() - row.created_at > timedelta(minutes=30):
        row.delete()
        return None
    return row


def _phase(dep) -> tuple[str, str, str]:
    if dep is None:
        return "upload", tr("Uploading source…"), ""
    phase = dep.status
    if phase == "failed":
        return "failed", tr(dep.get_status_display()), dep.log or ""
    if phase not in ("queued", "building", "running"):
        phase = "running" if phase in ("superseded", "rolled_back") else "queued"
    return phase, tr(dep.get_status_display()), dep.log or ""


@require_GET
def cli_deploy_page(request):
    ticket = (request.GET.get("ticket") or "").strip()
    watch = _watch(ticket)
    title = escape(watch.app_slug) if watch else escape(tr("deployment"))
    status_url = json.dumps("/cli/deploy/status?ticket=" + quote(ticket, safe=""))
    body = f"""
<h1>{_h("Deploying {name}", name=title)}</h1>
<div class="track" id="steps">
  <div class="step on" data-id="upload"><i>1</i><div><b>{_h("Upload")}</b><em>{_h("Sending the project folder")}</em></div></div>
  <div class="step" data-id="queued"><i>2</i><div><b>{_h("Queued")}</b><em>{_h("Waiting for a builder")}</em></div></div>
  <div class="step" data-id="building"><i>3</i><div><b>{_h("Build")}</b><em>{_h("Building the Docker image")}</em></div></div>
  <div class="step" data-id="running"><i>4</i><div><b>{_h("Release")}</b><em>{_h("Starting the service")}</em></div></div>
</div>
<p id="label">{_h("Uploading source…")}</p>
<pre id="log" dir="ltr"></pre>
<script>
const order = ["upload", "queued", "building", "running"];
async function tick() {{
  const res = await fetch({status_url});
  const data = await res.json();
  const at = order.indexOf(data.phase);
  document.querySelectorAll(".step").forEach((item, index) => {{
    item.className = "step" + (index < at ? " ok" : index === at ? " on" : "");
  }});
  if (data.phase === "running") document.querySelector('[data-id="running"]').className = "step ok";
  if (data.phase === "failed") {{
    const current = document.querySelector(".step.on") || document.querySelector(".step");
    if (current) current.className = "step fail";
  }}
  const label = document.getElementById("label");
  label.textContent = data.label || "";
  label.className = data.phase === "failed" ? "err" : "";
  const log = document.getElementById("log");
  log.textContent = data.log || "";
  log.className = data.log ? "show" : "";
  if (data.phase !== "running" && data.phase !== "failed") setTimeout(tick, 2000);
}}
tick();
</script>"""
    return _page(tr("Deploying {name}", name=watch.app_slug if watch else tr("deployment")), body)


@require_GET
def cli_deploy_status(request):
    watch = _watch((request.GET.get("ticket") or "").strip())
    if watch is None:
        return JsonResponse({"phase": "failed", "label": tr("This page has expired. Run dockra deploy again."), "log": ""})
    membership = Membership.objects.filter(user=watch.user).first()
    app = None
    if membership is not None:
        app = App.objects.filter(organization=membership.organization, slug=watch.app_slug).first()
    if app is None:
        return JsonResponse({"phase": "upload", "label": tr("Uploading source…"), "log": ""})
    after = (watch.after or "").replace("Z", "+00:00")
    rows = app.deployments.all()
    if after:
        try:
            moment = datetime.fromisoformat(after)
            if timezone.is_naive(moment):
                moment = timezone.make_aware(moment, timezone.get_current_timezone())
            rows = rows.filter(created_at__gte=moment)
        except ValueError:
            pass
    phase, label, log = _phase(rows.first())
    return JsonResponse({"phase": phase, "label": label, "log": log})
