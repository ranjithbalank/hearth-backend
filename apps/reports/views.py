import csv
import io
from decimal import Decimal

from django.http import HttpResponse
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


def _report_rows(report):
    """Return (title, header, rows) for an exportable report."""
    if report == "sales":
        r, f = _room_kpis(), _fnb_kpis()
        return ("Sales Summary", ["Metric", "Value"], [
            ["Occupancy %", r["occupancy_pct"]],
            ["ADR", r["adr"]],
            ["RevPAR", r["revpar"]],
            ["Room revenue", r["room_revenue"]],
            ["F&B sales", f["fnb_sales"]],
            ["F&B orders", f["order_count"]],
        ])
    if report == "tax":
        from collections import defaultdict
        agg = defaultdict(lambda: [Decimal("0")] * 4)
        for line in FolioLine.objects.exclude(kind=FolioLine.KIND_TAX):
            a = agg[str(line.gst_rate)]
            a[0] += line.taxable; a[1] += line.cgst; a[2] += line.sgst; a[3] += line.total
        rows = [[rate, v[0], v[1], v[2], v[3]] for rate, v in sorted(agg.items())]
        return ("GST Summary", ["Rate %", "Taxable", "CGST", "SGST", "Total"], rows)
    # occupancy / rooms
    rows = [[r.number, r.room_type_code, r.get_status_display()]
            for r in Room.objects.select_related("room_type")]
    return ("Room Status", ["Room", "Type", "Status"], rows)


class ReportExportView(ModuleAPIView):
    module = "reports"

    def get(self, request):
        report = request.query_params.get("report", "sales")
        # NB: avoid the query param name 'format' — DRF reserves it as a renderer override.
        fmt = request.query_params.get("fmt", "xlsx")
        title, header, rows = _report_rows(report)

        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(header)
            for row in rows:
                w.writerow(row)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{report}.csv"'
            return resp

        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append(header)
        for row in rows:
            ws.append([str(c) for c in row])
        out = io.BytesIO()
        wb.save(out)
        resp = HttpResponse(
            out.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{report}.xlsx"'
        return resp


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
