from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog


class AuditList(APIView):
    def get(self, request):
        rows = (
            AuditLog.objects.filter(organization=request.membership.organization)
            .select_related("actor")
            .order_by("-created_at")[:100]
        )
        return Response(
            [
                {
                    "id": item.id,
                    "action": item.action,
                    "target": item.target,
                    "detail": item.detail,
                    "actor": (item.actor.get_full_name() or item.actor.username) if item.actor_id else "",
                    "created_at": item.created_at.isoformat(),
                }
                for item in rows
            ]
        )
