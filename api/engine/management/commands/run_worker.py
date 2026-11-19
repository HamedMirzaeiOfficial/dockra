import signal
import socket
import time

from django.core.management.base import BaseCommand

from engine.models import Job
from engine.runner import CronRunner, Maintenance, beat, forget, process_next_job, watch_services


class Command(BaseCommand):
    help = "Process the job queue, cron and periodic tasks"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--queues",
            default="build,ops,rpc",
            help="Queues: build (build and rollback), ops (everything else), rpc (immediate jobs; picked up by the terminal service in production)",
        )
        parser.add_argument("--no-cron", action="store_true", help="Do not run cron and periodic tasks")

    def handle(self, *args, **options):
        from engine.volumes import remount_all

        queues = [item.strip() for item in options["queues"].split(",") if item.strip()]
        unknown = set(queues) - set(Job.Queue.values)
        if unknown:
            raise SystemExit(f"Unknown queue: {', '.join(sorted(unknown))}")
        periodic = not options["no_cron"] and Job.Queue.OPS in queues
        try:
            remount_all()
        except Exception as exc:
            self.stderr.write(f"volume remount: {exc}")
        if periodic:
            from engine.drivers import get_driver

            try:
                get_driver().heal_volumes(self.stderr.write)
            except Exception as exc:
                self.stderr.write(f"volume heal: {exc}")
            try:
                get_driver().sync_resources(self.stderr.write)
            except Exception as exc:
                self.stderr.write(f"resource sync: {exc}")
        self.stdout.write(f"worker started: queues={','.join(queues)} periodic={periodic}")
        crons = CronRunner() if periodic else None
        maintenance = Maintenance() if periodic else None
        kind = "builder" if queues == [Job.Queue.BUILD] else "worker"
        name = f"{kind}@{socket.gethostname()}"

        def leave(*_):
            # A normal shutdown (e.g. scaling builders down) does not raise a "service down" alert.
            forget(name)
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, leave)
        last_beat = 0.0
        while True:
            worked = process_next_job(queues)
            if time.monotonic() - last_beat > 15:
                last_beat = time.monotonic()
                try:
                    beat(name, kind, ",".join(queues))
                    if periodic:
                        watch_services()
                except Exception as exc:
                    self.stderr.write(f"beat: {exc}")
            if crons:
                crons.tick()
                maintenance.tick(self.stderr.write)
            if options["once"]:
                return
            time.sleep(0.4 if worked else 1)
