import secrets
from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import hash_key, issue_access, revoke_sessions
from accounts import security
from accounts.models import ApiToken, Membership, TwoFactor
from accounts.present import user_payload
from config.i18n import tr

User = get_user_model()


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        keys = security.throttle_keys(request, username)
        wait = security.retry_after(keys)
        if wait:
            return Response(
                {"detail": tr("Too many failed attempts; try again in {wait} seconds", wait=wait), "retry_after": wait},
                status=429,
            )
        user = authenticate(request, username=username, password=request.data.get("password") or "")
        if user is None:
            security.record_failure(keys)
            return Response({"detail": tr("Incorrect username or password")}, status=400)
        two_factor = security.needs_otp(user)
        if two_factor is not None:
            otp = str(request.data.get("otp") or "")
            if not otp:
                return Response({"detail": tr("Enter your two-factor code"), "otp_required": True}, status=400)
            if not security.verify_totp(two_factor, otp):
                security.record_failure(keys)
                return Response({"detail": tr("Incorrect verification code"), "otp_required": True}, status=400)
        security.clear_failures(username)
        membership = Membership.objects.filter(user=user).select_related("organization").first()
        if membership is None:
            return Response({"detail": tr("This user is not a member of any organization")}, status=403)
        refresh = issue_access(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": user_payload(user, membership),
            }
        )


class MeView(APIView):
    def get(self, request):
        return Response(user_payload(request.user, request.membership))


class TokenListCreate(APIView):
    # Everyone (viewers included) manages their own tokens; a token carries the same access as the role.
    write_role = "viewer"

    def get(self, request):
        rows = ApiToken.objects.filter(user=request.user).order_by("-created_at")
        return Response(
            [
                {
                    "id": item.id,
                    "name": item.name,
                    "prefix": item.prefix,
                    "created_at": item.created_at.isoformat(),
                    "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
                    "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                }
                for item in rows
            ]
        )

    def post(self, request):
        days = request.data.get("expires_days", 90)
        expires_at = None
        if days not in (None, "", 0, "0"):
            try:
                days = int(days)
            except (TypeError, ValueError):
                return Response({"detail": tr("Invalid expiry")}, status=400)
            if not 1 <= days <= 3650:
                return Response({"detail": tr("Expiry must be between 1 and 3650 days")}, status=400)
            expires_at = timezone.now() + timedelta(days=days)
        raw = "dockra_" + secrets.token_urlsafe(32)
        token = ApiToken.objects.create(
            user=request.user,
            name=(request.data.get("name") or "cli").strip()[:80] or "cli",
            prefix=raw[:12],
            key_hash=hash_key(raw),
            expires_at=expires_at,
        )
        return Response(
            {
                "id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "token": raw,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
            status=201,
        )


class TokenDelete(APIView):
    write_role = "viewer"

    def delete(self, request, pk):
        ApiToken.objects.filter(user=request.user, pk=pk).delete()
        return Response(status=204)


def member_payload(item: Membership, request) -> dict:
    return {
        "id": item.id,
        "username": item.user.username,
        "name": item.user.get_full_name() or item.user.username,
        "role": item.role,
        "role_label": tr(item.get_role_display()),
        "is_active": item.user.is_active,
        "is_self": item.user_id == request.user.id,
    }


class MemberListCreate(APIView):
    write_role = "admin"

    def get(self, request):
        rows = (
            Membership.objects.filter(organization=request.membership.organization)
            .select_related("user")
            .order_by("id")
        )
        return Response([member_payload(item, request) for item in rows])

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        role = request.data.get("role") or Membership.Role.DEVELOPER
        if role not in ASSIGNABLE_ROLES:
            return Response({"detail": tr("Invalid role")}, status=400)
        if not username:
            return Response({"detail": tr("Username is required")}, status=400)
        try:
            validate_password(password)
        except ValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=400)
        if User.objects.filter(username=username).exists():
            return Response({"detail": tr("This username already exists")}, status=400)
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=(request.data.get("first_name") or "").strip(),
            last_name=(request.data.get("last_name") or "").strip(),
        )
        membership = Membership.objects.create(
            user=user, organization=request.membership.organization, role=role
        )
        _audit(request, "member added", user.username, role)
        return Response(member_payload(membership, request), status=201)


ASSIGNABLE_ROLES = (Membership.Role.ADMIN, Membership.Role.DEVELOPER, Membership.Role.VIEWER)


def _audit(request, action: str, target: str, detail: str = "") -> None:
    from audit.models import AuditLog

    AuditLog.objects.create(
        organization=request.membership.organization, actor=request.user, action=action, target=target, detail=detail
    )


class MemberDetail(APIView):
    """Change role, enable/disable and remove members. The owner and the caller cannot be changed here."""

    write_role = "admin"

    def _target(self, request, pk):
        member = (
            Membership.objects.filter(organization=request.membership.organization, pk=pk).select_related("user").first()
        )
        if member is None:
            return None, Response({"detail": tr("Member not found")}, status=404)
        if member.user_id == request.user.id:
            return None, Response({"detail": tr("Change your own account from personal settings")}, status=400)
        if member.role == Membership.Role.OWNER:
            return None, Response({"detail": tr("The organization owner cannot be changed")}, status=403)
        if member.role == Membership.Role.ADMIN and request.membership.role != Membership.Role.OWNER:
            return None, Response({"detail": tr("Only the owner can change admins")}, status=403)
        return member, None

    def patch(self, request, pk):
        member, error = self._target(request, pk)
        if error:
            return error
        if "role" in request.data:
            role = request.data.get("role")
            if role not in ASSIGNABLE_ROLES:
                return Response({"detail": tr("Invalid role")}, status=400)
            if role == Membership.Role.ADMIN and request.membership.role != Membership.Role.OWNER:
                return Response({"detail": tr("Only the owner can grant the admin role")}, status=403)
            member.role = role
            member.save(update_fields=["role"])
            _audit(request, "role changed", member.user.username, role)
        if "is_active" in request.data:
            active = bool(request.data.get("is_active"))
            member.user.is_active = active
            member.user.save(update_fields=["is_active"])
            if not active:
                revoke_sessions(member.user)
            _audit(request, "member enabled" if active else "member disabled", member.user.username)
        return Response(member_payload(member, request))

    def delete(self, request, pk):
        member, error = self._target(request, pk)
        if error:
            return error
        user = member.user
        member.delete()
        _audit(request, "member removed", user.username)
        if not user.memberships.exists():
            user.delete()
        else:
            revoke_sessions(user)
        return Response(status=204)


class MemberPassword(APIView):
    """Admin password reset; every session and CLI token of that user is revoked."""

    write_role = "admin"

    def post(self, request, pk):
        member, error = MemberDetail()._target(request, pk)
        if error:
            return error
        password = request.data.get("password") or ""
        try:
            validate_password(password, member.user)
        except ValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=400)
        member.user.set_password(password)
        member.user.save(update_fields=["password"])
        revoke_sessions(member.user)
        ApiToken.objects.filter(user=member.user).delete()
        _audit(request, "password reset", member.user.username)
        return Response(status=204)


class LogoutView(APIView):
    """Revoke every panel session of this user; API and CLI tokens stay valid."""

    write_role = "viewer"

    def post(self, request):
        revoke_sessions(request.user)
        return Response(status=204)


class PasswordChange(APIView):
    write_role = "viewer"

    def post(self, request):
        if not request.user.check_password(request.data.get("old_password") or ""):
            return Response({"detail": tr("Current password is incorrect")}, status=400)
        new = request.data.get("new_password") or ""
        try:
            validate_password(new, request.user)
        except ValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=400)
        request.user.set_password(new)
        request.user.save(update_fields=["password"])
        revoke_sessions(request.user)
        # The fresh token is issued after the cutoff and stays valid; all other sessions are revoked.
        return Response({"access": str(issue_access(request.user).access_token)})


class TwoFactorStatus(APIView):
    def get(self, request):
        row = TwoFactor.objects.filter(user=request.user).first()
        return Response({"enabled": bool(row and row.enabled)})


class TwoFactorSetup(APIView):
    write_role = "viewer"

    def post(self, request):
        row = TwoFactor.objects.filter(user=request.user).first()
        if row and row.enabled:
            return Response({"detail": tr("Two-factor authentication is already enabled; disable it first")}, status=400)
        secret = security.new_secret()
        TwoFactor.objects.update_or_create(
            user=request.user, defaults={"secret": secret, "enabled": False, "recovery_hashes": [], "last_step": 0}
        )
        return Response({"secret": secret, "otpauth_url": security.otpauth_url(secret, request.user.username)})


class TwoFactorConfirm(APIView):
    write_role = "viewer"

    def post(self, request):
        row = TwoFactor.objects.filter(user=request.user, enabled=False).first()
        if row is None:
            return Response({"detail": tr("Start the setup first")}, status=400)
        codes = security.confirm(row, str(request.data.get("code") or ""))
        if codes is None:
            return Response({"detail": tr("Incorrect code; check the clock on your phone")}, status=400)
        return Response({"enabled": True, "recovery_codes": codes})


class TwoFactorDisable(APIView):
    write_role = "viewer"

    def post(self, request):
        row = TwoFactor.objects.filter(user=request.user, enabled=True).first()
        if row is None:
            return Response({"enabled": False})
        if not request.user.check_password(request.data.get("password") or ""):
            return Response({"detail": tr("Incorrect password")}, status=400)
        if not security.verify_totp(row, str(request.data.get("code") or "")):
            return Response({"detail": tr("Incorrect verification code")}, status=400)
        row.delete()
        return Response({"enabled": False})
