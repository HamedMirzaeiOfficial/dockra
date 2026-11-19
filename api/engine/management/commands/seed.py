import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import Membership, Organization

User = get_user_model()


class Command(BaseCommand):
    help = "Create the organization and the admin user. Does not create sample apps or databases."

    def handle(self, *args, **options):
        org_name = os.environ.get("DOCKRA_ORG_NAME", "Default organization")
        org, _ = Organization.objects.get_or_create(slug="dockra", defaults={"name": org_name})
        password = os.environ.get("DOCKRA_ADMIN_PASSWORD", "dockra")
        user, created = User.objects.get_or_create(
            username="admin",
            defaults={"first_name": "Admin", "last_name": "", "is_staff": True, "is_superuser": True},
        )
        if created:
            user.set_password(password)
            user.save()
            if password == "dockra":
                self.stderr.write("warning: the admin password is the default (dockra); set DOCKRA_ADMIN_PASSWORD or change it after signing in")
        Membership.objects.get_or_create(
            user=user, organization=org, defaults={"role": Membership.Role.OWNER}
        )
        self.stdout.write(self.style.SUCCESS("ready: admin"))
