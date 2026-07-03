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

    def create(self, request):
        """A department raises an indent: {department, lines: [{ingredient, qty}]}."""
        from decimal import Decimal, InvalidOperation

        from apps.inventory.models import Ingredient

        from .models import MaterialRequestLine

        department = (request.data.get("department") or "").strip()
        if not department:
            return Response({"detail": "department is required"}, status=400)
        wanted = request.data.get("lines") or []
        if not wanted:
            return Response({"detail": "at least one material line is required"}, status=400)
        parsed = []
        for w in wanted:
            ing = Ingredient.objects.filter(pk=w.get("ingredient")).first()
            if not ing:
                return Response({"detail": "unknown raw material on a line"}, status=400)
            try:
                qty = Decimal(str(w.get("qty", 0)))
            except InvalidOperation:
                return Response({"detail": "invalid quantity"}, status=400)
            if qty <= 0:
                return Response({"detail": "quantities must be positive"}, status=400)
            parsed.append((ing, qty))
        with transaction.atomic():
            r = MaterialRequest.objects.create(department=department,
                                               requested_by=request.user.username)
            for ing, qty in parsed:
                MaterialRequestLine.objects.create(request=r, ingredient=ing, qty=qty)
        log_action(request.user, "indent_requested", entity="MaterialRequest", entity_id=r.id,
                   after={"department": department, "lines": len(parsed)})
        return Response(_dict(r), status=201)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        """Requested → Approved → Issued. Issuing deducts stock (FR-STR-002).

        Segregation of duties: you can't approve your own indent — a second
        pair of eyes (store keeper / manager) moves it forward.
        """
        r = MaterialRequest.objects.prefetch_related("lines__ingredient").filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=404)
        if r.status == MaterialRequest.REQUESTED:
            from apps.accounts.constants import INDENT_APPROVER_ROLES
            if getattr(request.user, "role", "") not in INDENT_APPROVER_ROLES:
                return Response(
                    {"detail": "indent approval needs the store keeper or the restaurant manager"},
                    status=403)
            if r.requested_by and r.requested_by == request.user.username:
                return Response(
                    {"detail": "you raised this request — approval needs the store keeper or a manager"},
                    status=403)
            # Issuing more than the store holds fails later; flag it early.
            short = [l.ingredient.name for l in r.lines.all()
                     if l.qty > (l.ingredient.current_stock or 0)]
            if short:
                return Response(
                    {"detail": f"not enough stock to approve: {', '.join(short)} — raise a purchase order first"},
                    status=400)
            r.status = MaterialRequest.APPROVED
            r.save(update_fields=["status"])
            log_action(request.user, "indent_approved", entity="MaterialRequest", entity_id=r.id)
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
