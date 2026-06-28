from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import entitlement_allows, role_can_access
from apps.accounts.permissions import active_entitlements


def _build_alerts():
    """Derive operational alerts from current state (BRD 5.24 / FR-NOT-003).

    Each alert carries the module it deep-links to, so the caller can RBAC-filter.
    """
    alerts = []

    from apps.inventory.models import Ingredient
    low = [i for i in Ingredient.objects.all() if i.below_par]
    for i in low:
        alerts.append({
            "severity": "warning", "module": "inventory",
            "title": f"Low stock: {i.name}",
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

    from apps.banquets.models import Event
    tentative = Event.objects.filter(status=Event.TENTATIVE).count()
    if tentative:
        alerts.append({
            "severity": "info", "module": "banquets",
            "title": f"{tentative} tentative event(s)",
            "detail": "Function-space holds awaiting confirmation",
        })

    from apps.accounts.models import AuditLog
    sensitive = AuditLog.objects.filter(
        action__in=["folio_settle", "pos_settle", "dpdp_erase", "entitlement_update"]
    ).count()
    if sensitive:
        alerts.append({
            "severity": "info", "module": "reports",
            "title": f"{sensitive} sensitive action(s) logged",
            "detail": "Review the audit trail for settlements and admin changes",
        })

    return alerts


class NotificationView(APIView):
    """Alert center, scoped to what the signed-in user may actually see."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ent = active_entitlements()
        role = request.user.role
        visible = [
            a for a in _build_alerts()
            if role_can_access(role, a["module"]) and entitlement_allows(ent, a["module"])
        ]
        order = {"critical": 0, "warning": 1, "info": 2}
        visible.sort(key=lambda a: order.get(a["severity"], 3))
        return Response({"count": len(visible), "alerts": visible})
