import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand

from engine.terminal import serve


class Command(BaseCommand):
    help = "WebSocket service for the interactive terminal and database tunnels"

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=settings.DOCKRA_TERMINAL_PORT)

    def handle(self, *args, **options):
        self.stdout.write(f"terminal service on {options['host']}:{options['port']}")
        asyncio.run(serve(options["host"], options["port"]))
