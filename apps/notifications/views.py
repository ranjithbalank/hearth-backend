from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import entitlement_allows, role_can_access
from apps.accounts.permissions import ModulePermission, active_entitlements


def _build_alerts():
    """Derive operational alerts from current state (BRD 5.24 / FR-NOT-003).

    Each alert carries the module it deep-links to, so the caller can RBAC-filter.
    """
    alerts = []

    from apps.inventory.models import Ingredient
    low = [i for i in Ingredient.objects.all() if i.below_par]
    for i in low:
        # At (or below) zero the kitchen can't plate the dish at all —
        # that's an outage, not a reorder reminder.
        out = i.current_stock <= 0
        alerts.append({
            "severity": "critical" if out else "warning", "module": "inventory",
            "title": f"{'Out of stock' if out else 'Low stock'}: {i.name}",
            "detail": f"{i.current_stock} {i.unit} left (reorder at {i.reorder_level})",
        })

    from apps.rooms.models import Room
    ooo = Room.objects.filter(status=Room.OOO)
    for r in ooo:
        alerts.append({
            "severity": "warning", "module": "engineering",
            "title": f"Room {r.number} out of order",
            "detail": r.ooo_reason or "Maintenance required",
        })

    # Housekeeping: rooms a guest has just vacated need servicing. This fires
    # automatically on check-out (which sets the room to vacant/dirty).
    dirty = list(Room.objects.filter(status=Room.VACANT_DIRTY)
                 .order_by("number").values_list("number", flat=True))
    if dirty:
        alerts.append({
            "severity": "warning", "module": "housekeeping",
            "title": f"{len(dirty)} room(s) awaiting cleaning",
            "detail": "Vacated — ready to service: " + ", ".join(dirty),
        })
    # Front-desk cleaning requests (incl. occupied make-up-room) — urgent.
    requested = list(Room.objects.filter(cleaning_requested=True)
                     .order_by("number").values_list("number", flat=True))
    if requested:
        alerts.append({
            "severity": "warning", "module": "housekeeping",
            "title": f"{len(requested)} cleaning request(s) from front desk",
            "detail": "Guest-requested service: room " + ", ".join(requested),
        })
    cleaning = Room.objects.filter(status=Room.CLEANING).count()
    if cleaning:
        alerts.append({
            "severity": "info", "module": "housekeeping",
            "title": f"{cleaning} room(s) being cleaned",
            "detail": "Cleaning in progress — inspect when done",
        })

    # Front desk: rooms cleaned & inspected are ready to assign to arrivals.
    ready = list(Room.objects.filter(status__in=list(Room.SELLABLE))
                 .order_by("number").values_list("number", flat=True))
    if ready:
        alerts.append({
            "severity": "info", "module": "livegrid",
            "title": f"{len(ready)} room(s) ready to sell",
            "detail": "Cleaned & inspected: " + ", ".join(ready),
        })

    # Front desk: room-service food the kitchen has marked ready — send it up.
    # Clears automatically when the kitchen bumps the ticket to served.
    from apps.pos.models import Kot
    rs_ready = (Kot.objects.filter(status="ready", order__source_platform="roomservice")
                .select_related("order"))
    for k in rs_ready:
        alerts.append({
            "severity": "warning", "module": "frontdesk",
            "title": f"{k.order.captain or 'Room service'} — food ready",
            "detail": f"{k.number} is ready in the kitchen — send it up to the guest",
        })

    # A bar tab's side dish still cooks in the shared kitchen — bar staff
    # need telling when it's ready so they walk over and collect it (the
    # kitchen has no way to know it reached the bar customer, so the bar
    # confirms pickup itself via the same "serve" action captains use).
    from apps.pos.models import Kot, Order as PosOrder
    bar_ready = (Kot.objects.filter(status=Kot.READY, order__department=PosOrder.BAR)
                 .select_related("order", "order__bar_table")
                 .prefetch_related("lines__menu_item"))
    for k in bar_ready:
        kitchen_lines = [l for l in k.lines.all() if l.menu_item.station == "kitchen"]
        if not kitchen_lines:
            continue
        where = f"Bar: {k.order.bar_table.name}" if k.order.bar_table else "Bar takeaway"
        items_desc = ", ".join(f"{l.qty}× {l.display_name}" for l in kitchen_lines)
        alerts.append({
            "severity": "warning", "module": "barpos",
            "title": f"{where} — side dish ready",
            "detail": f"{items_desc} ready in the kitchen — collect from the pass",
        })

    from apps.housekeeping.models import WorkOrder
    open_wo = WorkOrder.objects.exclude(status=WorkOrder.DONE).count()
    if open_wo:
        alerts.append({
            "severity": "info", "module": "engineering",
            "title": f"{open_wo} open work order(s)",
            "detail": "Engineering tasks awaiting completion",
        })

    from apps.channel import services as ch
    breaches = ch.parity_breaches()
    if breaches:
        alerts.append({
            "severity": "critical", "module": "channel",
            "title": "Rate parity breach",
            "detail": f"Room types out of parity: {', '.join(breaches)}",
        })

    from apps.procurement.models import PurchaseOrder
    pending = PurchaseOrder.objects.filter(status=PurchaseOrder.PENDING).count()
    if pending:
        alerts.append({
            "severity": "info", "module": "procurement",
            "title": f"{pending} purchase order(s) pending approval",
            "detail": "Awaiting manager approval",
        })

    # Chef proposes a new dish → Restaurant Manager/GM/MD/Super Admin sign off
    # before it's orderable. "roles" narrows past the module gate: Chef also
    # has the "recipes" module, but can't approve their own dish, so they
    # don't need this alert.
    from apps.accounts.constants import MENU_APPROVER_ROLES
    from apps.pos.models import MenuItem
    pending_dishes = MenuItem.objects.filter(approval_status=MenuItem.PENDING)
    if pending_dishes.exists():
        names = list(pending_dishes.values_list("name", flat=True))
        alerts.append({
            "severity": "warning", "module": "recipes",
            "title": f"{len(names)} dish(es) awaiting approval",
            "detail": "Proposed: " + ", ".join(names),
            "roles": sorted(MENU_APPROVER_ROLES),
        })

    # Chef requests chicken → Restaurant Manager approves → the Store Keeper
    # needs to know it's ready to hand over. "roles" narrows this past the
    # module gate: every role can raise a matreq now, but only the actual
    # issuers (Store Keeper + managers) should be pinged to go issue it —
    # the chef who requested it doesn't need this alert.
    from apps.accounts.constants import INDENT_ISSUER_ROLES
    from apps.matreq.models import MaterialRequest
    approved = MaterialRequest.objects.filter(status=MaterialRequest.APPROVED)
    if approved.exists():
        depts = sorted(set(approved.values_list("department", flat=True)))
        alerts.append({
            "severity": "warning", "module": "matreq",
            "title": f"{approved.count()} material request(s) approved — ready to issue",
            "detail": "Departments: " + ", ".join(depts),
            "roles": sorted(INDENT_ISSUER_ROLES),
        })

    from apps.banquets.models import Event
    tentative = Event.objects.filter(status=Event.TENTATIVE).count()
    if tentative:
        alerts.append({
            "severity": "info", "module": "banquets",
            "title": f"{tentative} tentative event(s)",
            "detail": "Function-space holds awaiting confirmation",
        })

    # A rolling 24h window — an all-time count only ever grows, which turns
    # the alert (and the bell badge) into permanent noise nobody can clear.
    from datetime import timedelta
    from django.utils import timezone
    from apps.accounts.models import AuditLog
    sensitive = AuditLog.objects.filter(
        action__in=["folio_settle", "pos_settle", "dpdp_erase", "entitlement_update"],
        created_at__gte=timezone.now() - timedelta(hours=24),
    ).count()
    if sensitive:
        # Gated on "settings" — the audit trail lives at Settings > Audit Log,
        # so only roles that can actually open it get told to review it.
        alerts.append({
            "severity": "info", "module": "settings",
            "title": f"{sensitive} sensitive action(s) in the last 24h",
            "detail": "Review the audit trail for settlements and admin changes",
        })

    return alerts


class ApprovalInboxView(APIView):
    """One inbox for everything awaiting the signed-in user's sign-off:
    pending POs, indents (approve + issue stages), Chef-proposed dishes,
    and leave requests at whichever level this role decides. Sections only
    appear when the role can act AND something is actually waiting — the
    same segregation-of-duty rules as the underlying endpoints (never your
    own request)."""

    permission_classes = [IsAuthenticated, ModulePermission]
    module = "approvals"

    def get(self, request):
        from apps.accounts.constants import (
            INDENT_ISSUER_ROLES,
            LEAVE_FINAL_APPROVERS,
            MENU_APPROVER_ROLES,
            PO_APPROVER_ROLES,
            indent_approvers_for,
            leave_approvers_for,
        )
        from apps.accounts.permissions import shared_or_visible
        role = getattr(request.user, "role", "")
        username = request.user.username
        sections = []

        if role in PO_APPROVER_ROLES:
            from apps.procurement.models import PurchaseOrder
            items = [{
                "id": po.id,
                "title": f"{po.po_no or f'PO #{po.id}'} — {po.supplier.name}",
                "detail": f"{po.lines.count()} line(s) · ₹{po.total}",
            } for po in (PurchaseOrder.objects.filter(status=PurchaseOrder.PENDING)
                         .select_related("supplier").prefetch_related("lines"))
                if po.requested_by != username]
            if items:
                sections.append({"key": "po", "title": "Purchase orders",
                                 "route": "/procurement", "items": items})

        from apps.matreq.models import MaterialRequest
        indents = list(shared_or_visible(
            MaterialRequest.objects.prefetch_related("lines__ingredient"), request)
            .filter(status__in=[MaterialRequest.REQUESTED, MaterialRequest.APPROVED]))

        def indent_item(r):
            lines = ", ".join(f"{l.qty} {l.ingredient.unit} {l.ingredient.name}"
                              for l in r.lines.all()[:4])
            return {"id": r.id, "title": f"Indent #{r.id} — {r.department}",
                    "detail": f"{lines} · by {r.requested_by or '—'}"}

        to_approve = [indent_item(r) for r in indents
                      if r.status == MaterialRequest.REQUESTED
                      and role in indent_approvers_for(r.department)
                      and r.requested_by != username]
        if to_approve:
            sections.append({"key": "indents", "title": "Material requests",
                             "route": "/material-requests", "items": to_approve})
        if role in INDENT_ISSUER_ROLES:
            to_issue = [indent_item(r) for r in indents
                        if r.status == MaterialRequest.APPROVED]
            if to_issue:
                sections.append({"key": "issues", "title": "Approved indents — ready to issue",
                                 "route": "/material-requests", "items": to_issue})

        if role in MENU_APPROVER_ROLES:
            from apps.pos.models import MenuItem
            items = [{
                "id": m.id, "title": m.name,
                "detail": f"₹{m.price} · {m.category.name if m.category_id else '—'}",
            } for m in (MenuItem.objects.filter(approval_status=MenuItem.PENDING)
                        .select_related("category"))]
            if items:
                sections.append({"key": "dishes", "title": "New dishes",
                                 "route": "/recipes?tab=pending", "items": items})

        from apps.hr.models import LeaveRequest
        own = getattr(request.user, "employee_record", None)
        items = []
        for r in (LeaveRequest.objects.select_related("employee", "leave_type")
                  .filter(status__in=[LeaveRequest.PENDING, LeaveRequest.MANAGER_APPROVED])):
            if r.requested_by == username or (own and own.id == r.employee_id):
                continue  # never your own request
            if r.status == LeaveRequest.PENDING and role in leave_approvers_for(r.employee.department):
                stage = "manager sign-off"
            elif r.status == LeaveRequest.MANAGER_APPROVED and role in LEAVE_FINAL_APPROVERS:
                stage = "final sign-off"
            else:
                continue
            items.append({
                "id": r.id,
                "title": f"{r.employee.name} — {r.leave_type.name}",
                "detail": (f"{r.start_date.strftime('%d %b')} → {r.end_date.strftime('%d %b')}"
                           f" · {r.days} day(s) · {stage}"),
            })
        if items:
            sections.append({"key": "leave", "title": "Leave requests",
                             "route": "/leave", "items": items})

        return Response({"count": sum(len(s["items"]) for s in sections),
                         "sections": sections})


class NotificationView(APIView):
    """Alert center, scoped to what the signed-in user may actually see."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ent = active_entitlements()
        role = request.user.role
        visible = [
            a for a in _build_alerts()
            if role_can_access(role, a["module"]) and entitlement_allows(ent, a["module"])
            # Some alerts narrow further than the module gate — e.g. every
            # role can open Material Requests now, but "ready to issue" is
            # only useful to whoever actually does the issuing.
            and ("roles" not in a or role in a["roles"])
        ]
        order = {"critical": 0, "warning": 1, "info": 2}
        visible.sort(key=lambda a: order.get(a["severity"], 3))
        return Response({"count": len(visible), "alerts": visible})
