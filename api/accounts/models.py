from django.conf import settings
from django.db import models

from config.crypto import EncryptedTextField


class Organization(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        DEVELOPER = "developer", "Developer"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        unique_together = [("user", "organization")]

    def __str__(self):
        return f"{self.user_id}:{self.role}"


class CliWatch(models.Model):
    """Short-lived ticket for watching deployment progress in a browser without a separate session."""

    code = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cli_watches")
    app_slug = models.CharField(max_length=40)
    after = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.app_slug


class CliLogin(models.Model):
    """One-time request for linking the command line through a browser sign-in."""

    code = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="cli_logins"
    )
    token = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code


class ApiToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=80)
    prefix = models.CharField(max_length=16)
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.prefix


class TwoFactor(models.Model):
    """User TOTP. Recovery codes are stored only as hashes."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_factor")
    secret = EncryptedTextField()
    enabled = models.BooleanField(default=False)
    recovery_hashes = models.JSONField(default=list, blank=True)
    last_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    """Failed sign-in attempt, used to throttle password guessing."""

    key = models.CharField(max_length=200, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class SessionVersion(models.Model):
    """Panel session version for the user. A JWT whose "sv" claim is lower is rejected (logout, password change, deactivation)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="session_version")
    version = models.PositiveIntegerField(default=0)
