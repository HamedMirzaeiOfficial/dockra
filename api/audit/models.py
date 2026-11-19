from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organization


class AuditLog(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=120)
    detail = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
