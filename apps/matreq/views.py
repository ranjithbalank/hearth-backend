from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.inventory.models import apply_movement

from .models import MaterialRequest


def _dict(r):
    return {
        "id": r.id, "department": r.department, "requested_by": r.requested_by,
        "status": r.status, "created_at": r.created_at,
        "lines": [{"ingredient": l.ingredient.name, "qty": str(l.qty)} for l in r.lines.all()],
    }


class MaterialRequestViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "matreq"

    def list(self, request):
        qs = MaterialRequest.objects.prefetch_related("lines__ingredient")
        return Response([_dict(r) for r in qs])

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        """Requested → Approved → Issued. Issuing deducts stock (FR-STR-002)."""
        r = MaterialRequest.objects.prefetch_related("lines__ingredient").filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=404)
        if r.status == MaterialRequest.REQUESTED:
            r.status = MaterialRequest.APPROVED
            r.save(update_fields=["status"])
        elif r.status == MaterialRequest.APPROVED:
            with transaction.atomic():
                for line in r.lines.all():
                    # Department issue is a stock transfer, not recipe consumption (spec §4).
                    apply_movement(line.ingredient, "transfer", -line.qty,
                                   reason=f"Issued to {r.department}", source=f"indent:{r.id}",
                                   user=request.user)
                r.status = MaterialRequest.ISSUED
                r.save(update_fields=["status"])
            log_action(request.user, "indent_issued", entity="MaterialRequest", entity_id=r.id)
        return Response(_dict(r))
