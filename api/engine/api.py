"""Status of long-running jobs (imports and restores) for the CLI and panel, so they can stream step logs until done."""

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.models import Job
from config.i18n import tr

WATCHABLE = (Job.Kind.IMPORT_DB, Job.Kind.RESTORE, Job.Kind.DISK_IMPORT, Job.Kind.DISK_RESTORE, Job.Kind.BACKUP)


def job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "kind_label": tr(job.get_kind_display()),
        "status": job.status,
        "status_label": tr(job.get_status_display()),
        "log": job.log,
        "result": job.result or {},
    }


class JobStatus(APIView):
    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk, kind__in=WATCHABLE, organization=request.membership.organization)
        return Response(job_payload(job))
