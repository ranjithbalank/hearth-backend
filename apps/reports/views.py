from decimal import Decimal

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import ModulePermission
from apps.crm.models import Customer
from apps.frontoffice.models import FolioLine
from apps.pos.models import Order
from apps.rooms.models import Room


def _room_kpis():
    rooms = list(Room.objects.all())
    total = len(rooms) or 1
    occupied = sum(1 for r in rooms if r.status == Room.OCCUPIED)
    room_lines = FolioLine.objects.filter(kind=FolioLine.KIND_ROOM)
    room_revenue = sum((l.taxable for l in room_lines), start=Decimal("0"))
    rooms_sold = room_lines.count() or 1
    occupancy = round(occupied / total * 100, 1)
    adr = round(float(room_revenue) / rooms_sold, 2)
    revpar = round(float(room_revenue) / total, 2)
    return {
        "rooms_total": total,
        "occupied": occupied,
        "occupancy_pct": occupancy,
        "adr": adr,
        "revpar": revpar,
        "room_revenue": str(room_revenue),
    }


def _fnb_kpis():
    orders = Order.objects.filter(
        status__in=[Order.SETTLED, Order.POSTED_TO_ROOM]
    ).prefetch_related("lines__menu_item")
    total = Decimal("0")
    by_mode = {Order.DINEIN: Decimal("0"), Order.TAKEAWAY: Decimal("0"),
               Order.DELIVERY: Decimal("0")}
    count = 0
    for o in orders:
        t = o.totals()["total"]
        total += t
        by_mode[o.mode] = by_mode.get(o.mode, Decimal("0")) + t
        count += 1
    return {
        "fnb_sales": str(total),
        "order_count": count,
        "by_mode": {k: str(v) for k, v in by_mode.items()},
    }


class ModuleAPIView(APIView):
    permission_classes = [IsAuthenticated, ModulePermission]
    module = None


class DashboardView(ModuleAPIView):
    module = "dashboard"

    def get(self, request):
        return Response({"rooms": _room_kpis(), "fnb": _fnb_kpis()})


class ExecutiveView(ModuleAPIView):
    module = "execdashboard"

    def get(self, request):
        rooms = _room_kpis()
        fnb = _fnb_kpis()
        room_rev = Decimal(rooms["room_revenue"])
        fnb_rev = Decimal(fnb["fnb_sales"])
        receivables = sum(
            (c.outstanding for c in Customer.objects.all()), start=Decimal("0")
        )
        total_rev = room_rev + fnb_rev
        return Response({
            "kpis": {
                "revenue": str(total_rev),
                "occupancy_pct": rooms["occupancy_pct"],
                "room_revenue": str(room_rev),
                "fnb_revenue": str(fnb_rev),
                "receivables": str(receivables),
            },
            "revenue_mix": [
                {"label": "Rooms", "value": str(room_rev)},
                {"label": "F&B", "value": str(fnb_rev)},
            ],
        })


class SalesSummaryView(ModuleAPIView):
    module = "reports"

    def get(self, request):
        return Response({"rooms": _room_kpis(), "fnb": _fnb_kpis()})


class CatalogueView(ModuleAPIView):
    module = "reports"

    def get(self, request):
        groups = [
            {"group": "Rooms", "tiles": [
                "Occupancy & RevPAR", "Arrivals & Departures",
                "Night Audit / Daily Revenue", "No-show & Cancellation"]},
            {"group": "F&B", "tiles": [
                "F&B Sales Summary", "Item & Category Performance",
                "Discounts & Voids"]},
            {"group": "Finance", "tiles": [
                "Tax / GST", "City Ledger / AR Aging"]},
            {"group": "Guests", "tiles": ["Guest & Loyalty"]},
        ]
        return Response(groups)
