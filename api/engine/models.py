from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organization


class Job(models.Model):
    class Kind(models.TextChoices):
        DEPLOY = "deploy", "Deploy"
        RESTART = "restart", "Restart"
        STOP = "stop", "Stop"
        START = "start", "Start"
        ROLLBACK = "rollback", "Rollback"
        BACKUP = "backup", "Backup"
        PROVISION_DB = "provision_db", "Provision database"
        RESTORE = "restore", "Restore backup"
        SYNC = "sync", "Apply settings"
        APPLY_RESOURCES = "apply_resources", "Apply resources"
        REMOVE_APP = "remove_app", "Remove app"
        REMOVE_DISK = "remove_disk", "Remove disk"
        REMOVE_DB = "remove_db", "Remove database"
        DB_STOP = "db_stop", "Stop database"
        DB_START = "db_start", "Start database"
        DB_RESIZE = "db_resize", "Resize database"
        EXEC = "exec", "Run command"
        LOGS = "logs", "Read logs"
        VOLUME = "volume", "Prepare disk"
        DISK_BACKUP = "disk_backup", "Disk backup"
        DISK_RESTORE = "disk_restore", "Disk restore"
        DB_ADMIN = "db_admin", "Open database admin"
        DB_ADMIN_STOP = "db_admin_stop", "Close database admin"
        IMPORT_DB = "import_db", "Import database"
        DISK_IMPORT = "disk_import", "Import disk files"

    class Queue(models.TextChoices):
        BUILD = "build", "Build"
        OPS = "ops", "Operations"
        # Short jobs the API waits on; the terminal service picks them up so the API itself has no Docker access.
        RPC = "rpc", "Immediate"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(Organization, null=True, on_delete=models.CASCADE, related_name="jobs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    queue = models.CharField(max_length=10, choices=Queue.choices, default=Queue.OPS)
    # Target app or database; two jobs on the same target never run concurrently.
    target = models.CharField(max_length=60, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    # The user asked to cancel; a queued job ends immediately and a running build is stopped.
    cancel_requested = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["status", "id"]), models.Index(fields=["queue", "status", "id"])]


BUILD_KINDS = {Job.Kind.DEPLOY, Job.Kind.ROLLBACK}
RPC_KINDS = {Job.Kind.EXEC, Job.Kind.LOGS, Job.Kind.VOLUME}


class MetricSample(models.Model):
    """Per-minute usage sample of an app or a database (exactly one of the two keys is set)."""

    app = models.ForeignKey("apps_core.App", null=True, blank=True, on_delete=models.CASCADE, related_name="metric_samples")
    database = models.ForeignKey(
        "databases.Database", null=True, blank=True, on_delete=models.CASCADE, related_name="metric_samples"
    )
    at = models.DateTimeField(default=timezone.now, db_index=True)
    cpu = models.PositiveSmallIntegerField(default=0)
    ram = models.PositiveSmallIntegerField(default=0)
    network_mbps = models.FloatField(default=0)
    # Request statistics for this minute, from Traefik
    requests = models.PositiveIntegerField(default=0)
    errors_5xx = models.PositiveIntegerField(default=0)
    avg_ms = models.FloatField(null=True, blank=True)
    p95_ms = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["at"]
        indexes = [models.Index(fields=["app", "at"]), models.Index(fields=["database", "at"])]


class Alert(models.Model):
    class Kind(models.TextChoices):
        CRASH_LOOP = "crash_loop", "Crash loop"
        CPU = "cpu", "High CPU"
        MEMORY = "memory", "High memory"
        DISK = "disk", "App disk nearly full"
        HOST_DISK = "host_disk", "Host disk"
        HOST_MEMORY = "host_memory", "Host memory"
        HOST_CPU = "host_cpu", "Host CPU pressure"
        SERVICE = "service", "Platform service down"
        ERRORS = "errors", "High 5xx rate"
        DOWN = "down", "Unreachable"

    class Status(models.TextChoices):
        FIRING = "firing", "Firing"
        RESOLVED = "resolved", "Resolved"

    app = models.ForeignKey("apps_core.App", null=True, blank=True, on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    subject = models.CharField(max_length=120, blank=True)
    message = models.CharField(max_length=400)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FIRING)
    started_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["status", "kind"])]


class ServiceBeat(models.Model):
    """Heartbeat of worker, builder and terminal; if one goes silent the others raise an alert."""

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=20)
    queues = models.CharField(max_length=60, blank=True)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["kind", "name"]
