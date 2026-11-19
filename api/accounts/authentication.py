import hashlib

from django.db import models

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.models import ApiToken, SessionVersion


def session_version(user) -> int:
    return SessionVersion.objects.filter(user=user).values_list("version", flat=True).first() or 0


def issue_access(user) -> str:
    """Panel JWT bound to the current session version; logging out or changing the password invalidates all older versions."""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    refresh["sv"] = session_version(user)
    return refresh


class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if int(validated_token.get("sv", 0)) != session_version(user):
            raise AuthenticationFailed("Session expired; please sign in again")
        return user


def revoke_sessions(user) -> None:
    row, _ = SessionVersion.objects.get_or_create(user=user)
    SessionVersion.objects.filter(pk=row.pk).update(version=models.F("version") + 1)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class DockraAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None
        raw = header.split(" ", 1)[1].strip()
        if not raw:
            return None
        if raw.count(".") == 2:
            return VersionedJWTAuthentication().authenticate(request)
        token = ApiToken.objects.filter(key_hash=hash_key(raw)).select_related("user").first()
        if token is None:
            raise AuthenticationFailed("Invalid token")
        if not token.user.is_active:
            raise AuthenticationFailed("Account is disabled")
        if token.expires_at and token.expires_at <= timezone.now():
            raise AuthenticationFailed("Token expired; run dockra login again")
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return (token.user, token)

    def authenticate_header(self, request):
        return "Bearer"
