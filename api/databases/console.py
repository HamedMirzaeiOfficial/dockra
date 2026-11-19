"""Open the web admin UI of a database (pgAdmin, phpMyAdmin, ...); details in engine/dbadmin.py."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseRedirect
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import session_version
from accounts.models import Membership
from accounts.permissions import ROLE_RANK
from databases.models import Database
from engine import dbadmin
from engine.actions import enqueue
from engine.models import Job

ADMIN_KINDS = (Job.Kind.DB_ADMIN, Job.Kind.DB_ADMIN_STOP)


def _idle() -> timedelta:
    return timedelta(minutes=settings.DOCKRA_DBADMIN_IDLE_MINUTES)


def _last_job(database: Database) -> Job | None:
    return Job.objects.filter(target=f"db:{database.id}", kind__in=ADMIN_KINDS).order_by("-id").first()


def _state(database: Database, request) -> dict:
    job = _last_job(database)
    data = {
        "tool": dbadmin.tool_name(database),
        "status": "stopped",
        "detail": "",
        "url": "",
        "idle_minutes": settings.DOCKRA_DBADMIN_IDLE_MINUTES,
    }
    if job is None or job.kind == Job.Kind.DB_ADMIN_STOP:
        return data
    if job.status in (Job.Status.QUEUED, Job.Status.RUNNING):
        data["status"] = "starting"
    elif job.status == Job.Status.FAILED:
        lines = (job.log or "").strip().splitlines()
        data["status"] = "failed"
        data["detail"] = lines[-1] if lines else "Could not open"
    elif database.admin_until and database.admin_until > timezone.now():
        data["status"] = "running"
        data["base"] = job.payload.get("base", "")
        ticket = dbadmin.make_ticket(request.user, database, session_version(request.user))
        data["url"] = dbadmin.open_url(data["base"], ticket)
    return data


def _reply(database: Database, request, code: int = 200):
    data = _state(database, request)
    data.pop("base", None)
    return Response(data, status=code)


class DatabaseAdmin(APIView):
    """GET status (and a one-minute sign-in URL if open), POST to open, DELETE to close."""

    def _database(self, request, pk) -> Database | None:
        if request.membership.role == Membership.Role.VIEWER:
            return None
        return get_object_or_404(Database, organization=request.membership.organization, pk=pk)

    def get(self, request, pk):
        database = self._database(request, pk)
        if database is None:
            return Response({"detail": "Viewers have no access to database data"}, status=403)
        return _reply(database, request)

    def post(self, request, pk):
        database = self._database(request, pk)
        if database is None:
            return Response({"detail": "Viewers have no access to database data"}, status=403)
        if database.status != Database.Status.RUNNING:
            return Response({"detail": "The database is not running"}, status=400)
        base = dbadmin.public_base(request)
        state = _state(database, request)
        Database.objects.filter(pk=database.pk).update(admin_until=timezone.now() + _idle())
        database.refresh_from_db()
        if state["status"] == "starting" or (state["status"] == "running" and state.get("base") == base):
            return _reply(database, request)
        enqueue(Job.Kind.DB_ADMIN, {"database_id": database.id, "base": base}, request.user, database.organization)
        return _reply(database, request, code=202)

    def delete(self, request, pk):
        database = self._database(request, pk)
        if database is None:
            return Response({"detail": "Viewers have no access to database data"}, status=403)
        Database.objects.filter(pk=database.pk).update(admin_until=None)
        enqueue(Job.Kind.DB_ADMIN_STOP, {"database_id": database.id}, request.user, database.organization)
        return Response(status=204)


def _page(status: int, text: str) -> HttpResponse:
    body = (
        '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Dockra</title>'
        '<body style="font-family:system-ui,sans-serif;background:#0b0b0d;color:#e4e4e7;display:grid;'
        f'place-items:center;min-height:90vh;margin:0;padding:16px"><p>{text}</p></body></html>'
    )
    return HttpResponse(body, status=status, content_type="text/html; charset=utf-8")


def _allowed(data: dict | None, database_id: int) -> Database | None:
    if not data or data.get("d") != database_id:
        return None
    user = get_user_model().objects.filter(pk=data.get("u"), is_active=True).first()
    if user is None or session_version(user) != data.get("sv"):
        return None
    database = Database.objects.filter(pk=database_id).first()
    if database is None:
        return None
    role = Membership.objects.filter(user=user, organization_id=database.organization_id).values_list("role", flat=True).first()
    if role is None or ROLE_RANK[role] < ROLE_RANK["developer"]:
        return None
    return database


def dbadmin_open(request):
    """Exchange the panel's one-minute ticket for a cookie on this path and redirect to the tool."""
    data = dbadmin.read_ticket(request.GET.get("t", ""))
    database = _allowed(data, (data or {}).get("d"))
    if database is None:
        return _page(403, "The link has expired; click Open again in the panel.")
    path = f"{dbadmin.prefix(database)}/"
    response = HttpResponseRedirect(path)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response.set_cookie(
        dbadmin.cookie_name(database.id),
        dbadmin.make_session(data),
        max_age=dbadmin.SESSION_SECONDS,
        path=path,
        secure=request.is_secure(),
        httponly=True,
        samesite="Lax",
    )
    return response


def dbadmin_auth(request, pk: int):
    """Traefik forwardAuth for every request to /dbadmin/<name>/."""
    raw = request.COOKIES.get(dbadmin.cookie_name(pk), "")
    key = f"{pk}:{raw}"
    if raw and dbadmin.cached_ok(key):
        return HttpResponse(status=200)
    database = _allowed(dbadmin.read_session(raw), pk)
    if database is None:
        return _page(401, "Access denied or the session ended; click Open again in the panel.")
    dbadmin.remember_ok(key)
    # Every use pushes the automatic close forward (at most one write per minute).
    now = timezone.now()
    Database.objects.filter(pk=pk, admin_until__lt=now + _idle() - timedelta(minutes=1)).update(admin_until=now + _idle())
    return HttpResponse(status=200)
