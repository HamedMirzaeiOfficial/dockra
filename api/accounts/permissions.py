from rest_framework.permissions import BasePermission

from accounts.models import Membership

ROLE_RANK = {"viewer": 1, "developer": 2, "admin": 3, "owner": 4}


class IsMember(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        rows = Membership.objects.filter(user=user).select_related("organization").order_by("id")
        wanted = request.META.get("HTTP_X_ORGANIZATION", "").strip()
        if wanted:
            rows = rows.filter(organization__slug=wanted)
        membership = rows.first()
        if membership is None:
            return False
        request.membership = membership
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        needed = getattr(view, "write_role", "developer")
        return ROLE_RANK[membership.role] >= ROLE_RANK[needed]
