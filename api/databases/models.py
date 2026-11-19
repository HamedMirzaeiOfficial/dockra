from decimal import Decimal

from django.db import models
from django.utils import timezone

from accounts.models import Organization
from apps_core.models import App
from config.crypto import EncryptedTextField


class Database(models.Model):
    class Engine(models.TextChoices):
        POSTGRES = "postgres", "PostgreSQL"
        MYSQL = "mysql", "MySQL"
        MARIADB = "mariadb", "MariaDB"
        MONGODB = "mongodb", "MongoDB"
        REDIS = "redis", "Redis"

    class Status(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        RUNNING = "running", "Running"
        FAILED = "failed", "Failed"
        STOPPED = "stopped", "Stopped"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="databases")
    name = models.SlugField(max_length=40, unique=True)
    engine = models.CharField(max_length=20, choices=Engine.choices)
    version = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROVISIONING)
    region = models.CharField(max_length=20, default="local")
    memory_mb = models.PositiveIntegerField(default=1024)
    cpu = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.50"))
    disk_gb = models.PositiveIntegerField(default=10)
    host = models.CharField(max_length=200, blank=True)
    port = models.PositiveIntegerField(default=5432)
    username = models.CharField(max_length=80, blank=True)
    password = EncryptedTextField(blank=True)
    db_name = models.CharField(max_length=80, blank=True)
    container_name = models.CharField(max_length=80, blank=True)
    # The admin UI (pgAdmin, ...) stays open until this time; every request to it extends it.
    admin_until = models.DateTimeField(null=True, blank=True)
    # Automatic backups: every N hours (0 = off) and how many to keep. Empty = server default.
    backup_hours = models.PositiveIntegerField(null=True, blank=True)
    backup_keep = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DatabaseLink(models.Model):
    database = models.ForeignKey(Database, on_delete=models.CASCADE, related_name="links")
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="database_links")
    env_key = models.CharField(max_length=80)

    class Meta:
        unique_together = [("database", "app")]


class Backup(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "In progress"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Automatic"

    database = models.ForeignKey(Database, on_delete=models.CASCADE, related_name="backups")
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
