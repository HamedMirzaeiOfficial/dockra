from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organization
from config.crypto import EncryptedTextField


class App(models.Model):
    class Platform(models.TextChoices):
        DJANGO = "django", "Python / Django"
        NODE = "node", "Node.js"
        NEXTJS = "nextjs", "Next.js"
        STATIC = "static", "Static site"
        DOCKER = "docker", "Docker"

    class Region(models.TextChoices):
        LOCAL = "local", "This server"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DEPLOYING = "deploying", "Deploying"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="apps")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=40, unique=True)
    platform = models.CharField(max_length=20, choices=Platform.choices)
    region = models.CharField(max_length=20, choices=Region.choices, default=Region.LOCAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STOPPED)
    instances = models.PositiveIntegerField(default=1)
    port = models.PositiveIntegerField(default=8000)
    git_url = models.CharField(max_length=300, blank=True)
    git_branch = models.CharField(max_length=120, blank=True)
    webhook_secret = EncryptedTextField(blank=True)
    health_path = models.CharField(max_length=200, blank=True)
    runtime = models.CharField(max_length=40, blank=True)
    cpu = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.50"))
    memory_mb = models.PositiveIntegerField(default=512)
    current_image = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_apps"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.slug


class Deployment(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        BUILDING = "building", "Building"
        RUNNING = "running", "Live"
        FAILED = "failed", "Failed"
        SUPERSEDED = "superseded", "Succeeded"
        ROLLED_BACK = "rolled_back", "Rolled back"
        CANCELED = "canceled", "Canceled"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="deployments")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    message = models.CharField(max_length=240, blank=True)
    git_url = models.CharField(max_length=300, blank=True)
    source_path = models.CharField(max_length=400, blank=True)
    tree = models.CharField(max_length=64, blank=True)
    image = models.CharField(max_length=200, blank=True)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="deployments"
    )

    class Meta:
        unique_together = [("app", "number")]
        ordering = ["-number"]

    def __str__(self):
        return f"{self.app.slug}:v{self.number}"


class EnvVar(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="env_vars")
    key = models.CharField(max_length=120)
    value = EncryptedTextField(blank=True)
    is_secret = models.BooleanField(default=False)

    class Meta:
        unique_together = [("app", "key")]
        ordering = ["key"]


class Domain(models.Model):
    class Ssl(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        ERROR = "error", "Error"

    class Kind(models.TextChoices):
        SSLIP = "sslip", "Default"
        CUSTOM = "custom", "Custom"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="domains")
    hostname = models.CharField(max_length=200, unique=True)
    ssl_status = models.CharField(max_length=20, choices=Ssl.choices, default=Ssl.PENDING)
    is_primary = models.BooleanField(default=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.CUSTOM)
    redirect_https = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-is_primary", "hostname"]


class Redirect(models.Model):
    """Redirect served by Traefik in front of the app; the request never reaches the app."""

    class Match(models.TextChoices):
        PREFIX = "prefix", "This path and below"
        EXACT = "exact", "Exact path only"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="redirects")
    # Empty = every domain of the app
    source_host = models.CharField(max_length=200, blank=True)
    source_path = models.CharField(max_length=200, default="/")
    match = models.CharField(max_length=10, choices=Match.choices, default=Match.PREFIX)
    # Full URL (https://example.com/x) or a path on the same domain (/x)
    target = models.CharField(max_length=500)
    # Append the rest of the path and the query string to the target
    keep_path = models.BooleanField(default=True)
    permanent = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["source_host", "source_path", "id"]


class Disk(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="disks")
    name = models.CharField(max_length=40)
    mount_to = models.CharField(max_length=200)
    size_gb = models.PositiveIntegerField(default=1)
    # Automatic backups: every N hours (0 = off) and how many to keep. Empty = server default.
    backup_hours = models.PositiveIntegerField(null=True, blank=True)
    backup_keep = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("app", "name")]


class DiskBackup(models.Model):
    """tar.gz snapshot of a disk. Backups outlive the disk so a mistaken deletion can be undone."""

    class Status(models.TextChoices):
        RUNNING = "running", "In progress"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Automatic"
        SAFETY = "safety", "Before restore"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="disk_backups")
    disk = models.ForeignKey(Disk, null=True, blank=True, on_delete=models.SET_NULL, related_name="backups")
    disk_name = models.CharField(max_length=40)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    size_bytes = models.BigIntegerField(default=0)
    path = models.CharField(max_length=400, blank=True)
    remote_key = models.CharField(max_length=400, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CronJob(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="cron_jobs")
    schedule = models.CharField(max_length=80)
    command = models.CharField(max_length=400)
    enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True)
    last_output = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
