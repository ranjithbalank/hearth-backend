from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import (
    ModuleViewSetMixin,
    resolve_active_branch,
    shared_or_visible,
    visible_branch_ids,
)
from apps.inventory.models import apply_movement

from .models import MaterialRequest


def _dict(r):
    from apps.accounts.constants import indent_approvers_for
    return {
        "id": r.id, "department": r.department, "requested_by": r.requested_by,
        "status": r.status, "created_at": r.created_at,
        "location": r.location_id, "location_name": r.location.name if r.location_id else None,
        "lines": [{"ingredient": l.ingredient.name, "qty": str(l.qty)} for l in r.lines.all()],
        # Who approves THIS department's indent — shown on the card so the
        # requester knows whose desk it's waiting on.
        "approver_roles": sorted(indent_approvers_for(r.department)),
    }


class MaterialRequestViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "matreq"

    def list(self, request):
        """Segregated by design: nobody sees every department's indents dumped
        together. ?view=mine → what I raised (to track its status).
        ?view=queue (default) → what's actually mine to act on right now
        (my department's pending approvals + anything approved and awaiting
        issue, since issuing is a store-wide job).

        Super Admin / MD / GM are the exception: they're universal approvers
        and issuers everywhere already, so instead of just their actionable
        subset they get the full oversight view — every department, every
        status (including already-issued history), across the whole property.
        CEO gets that same full oversight view for visibility only — CEO
        never appears in indent_approvers_for()/INDENT_ISSUER_ROLES, so the
        advance() action below still rejects any approve/issue attempt from CEO.
        """
        from apps.accounts.constants import (
            INDENT_ISSUER_ROLES,
            INDENT_OVERSIGHT_ROLES,
            indent_approvers_for,
        )
        role = getattr(request.user, "role", "")
        username = request.user.username
        qs = MaterialRequest.objects.prefetch_related("lines__ingredient")
        # Same "mine + not-yet-branch-tagged" rule as POS orders — an
        # indent raised before this feature existed doesn't vanish for
        # anyone, but a new one at another branch stays out of view.
        qs = shared_or_visible(qs, request)
        if request.query_params.get("view") == "mine":
            qs = qs.filter(requested_by=username)
        elif role in INDENT_OVERSIGHT_ROLES:
            pass   # full oversight — every department, every status
        else:
            actionable_ids = [
                r.id for r in qs
                if (r.status == MaterialRequest.REQUESTED and role in indent_approvers_for(r.department))
                or (r.status == MaterialRequest.APPROVED and role in INDENT_ISSUER_ROLES)
            ]
            qs = qs.filter(id__in=actionable_ids)
        return Response([_dict(r) for r in qs])

    @action(detail=False, methods=["get"])
    def materials(self, request):
        """Read-only material picklist for the request form. Every role that
        can raise an indent needs this — but most of them (Housekeeping,
        Front Office, Cashier…) don't have the full 'inventory' module, so
        this can't just proxy to /inventory/. No cost, no CRUD — just enough
        to pick an item and see what's in stock."""
        from apps.inventory.models import Ingredient
        qs = shared_or_visible(Ingredient.objects.all(), request).order_by("name")
        return Response([
            {"id": i.id, "name": i.name, "unit": i.unit, "current_stock": str(i.current_stock)}
            for i in qs
        ])

    def create(self, request):
        """A department raises an indent: {department, lines: [{ingredient, qty}]}."""
        from decimal import Decimal, InvalidOperation

        from apps.inventory.models import Ingredient

        from .models import MaterialRequestLine

        from apps.accounts.constants import indent_approvers_for, role_can_request_department

        department = (request.data.get("department") or "").strip()
        if not department:
            return Response({"detail": "department is required"}, status=400)
        role = getattr(request.user, "role", "")
        if not role_can_request_department(role, department):
            approvers = ", ".join(sorted(indent_approvers_for(department) - {role}))
            return Response(
                {"detail": f"you approve {department} indents yourself — ask someone on the floor "
                           f"to raise this one (or pick a different department); it'll still land "
                           f"in your own approval queue" + (f", alongside {approvers}" if approvers else "")},
                status=400)
        wanted = request.data.get("lines") or []
        if not wanted:
            return Response({"detail": "at least one material line is required"}, status=400)
        ingredient_qs = shared_or_visible(Ingredient.objects.all(), request)
        parsed = []
        for w in wanted:
            ing = ingredient_qs.filter(pk=w.get("ingredient")).first()
            if not ing:
                return Response({"detail": "unknown raw material on a line"}, status=400)
            try:
                qty = Decimal(str(w.get("qty", 0)))
            except InvalidOperation:
                return Response({"detail": "invalid quantity"}, status=400)
            if qty <= 0:
                return Response({"detail": "quantities must be positive"}, status=400)
            parsed.append((ing, qty))
        # Same fallback as POS orders: the till's active branch if sent,
        # else — since an indent has no table to infer from — the
        # requester's own branch when they're only ever assigned to one.
        request_location = resolve_active_branch(request)
        if request_location is None:
            visible = visible_branch_ids(request)
            if isinstance(visible, set) and len(visible) == 1:
                request_location = next(iter(visible))
        with transaction.atomic():
            r = MaterialRequest.objects.create(department=department,
                                               requested_by=request.user.username,
                                               location_id=request_location)
            for ing, qty in parsed:
                MaterialRequestLine.objects.create(request=r, ingredient=ing, qty=qty)
        log_action(request.user, "indent_requested", entity="MaterialRequest", entity_id=r.id,
                   after={"department": department, "lines": len(parsed)})
        return Response(_dict(r), status=201)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        """Requested → Approved → Issued (FR-STR-002).

        Every department has its own approver — the head who actually knows
        whether the indent makes sense (Kitchen/Bar → Restaurant Manager,
        Housekeeping/Banquets/Front Office → Front Office, Maintenance →
        Housekeeping — see DEPARTMENT_APPROVERS), plus GM/MD/Super Admin as a
        universal override. You can never approve your own request. Issuing
        the approved stock is always the Store Keeper's job (or a manager's)
        — a physical handover, regardless of which department it's for.
        """
        from apps.accounts.constants import indent_approvers_for, INDENT_ISSUER_ROLES
        r = MaterialRequest.objects.prefetch_related("lines__ingredient").filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=404)
        role = getattr(request.user, "role", "")
        if r.status == MaterialRequest.REQUESTED:
            approvers = indent_approvers_for(r.department)
            if role not in approvers:
                return Response(
                    {"detail": f"a {' or '.join(sorted(approvers))} must approve {r.department} indents"},
                    status=403)
            if r.requested_by and r.requested_by == request.user.username:
                return Response(
                    {"detail": "you raised this request — someone else must approve it"},
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
            if role not in INDENT_ISSUER_ROLES:
                return Response(
                    {"detail": "only the store keeper (or a manager) can issue approved stock"},
                    status=403)
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
