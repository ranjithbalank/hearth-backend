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
    available = sum(1 for r in rooms if r.status in Room.SELLABLE)
    dirty = sum(1 for r in rooms if r.status in (Room.VACANT_DIRTY, Room.CLEANING))
    ooo = sum(1 for r in rooms if r.status == Room.OOO)
    room_lines = FolioLine.objects.filter(kind=FolioLine.KIND_ROOM)
    room_revenue = sum((l.taxable for l in room_lines), start=Decimal("0"))
    rooms_sold = room_lines.count() or 1
    occupancy = round(occupied / total * 100, 1)
    adr = round(float(room_revenue) / rooms_sold, 2)
    revpar = round(float(room_revenue) / total, 2)
    return {
        "rooms_total": len(rooms),
        "occupied": occupied,
        "available": available,
        "dirty": dirty,
        "ooo": ooo,
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


def _receivables():
    """Accounts receivable: outstanding owed to us, and the corporate (BTC) share."""
    customers = list(Customer.objects.all())
    total = sum((c.outstanding for c in customers), start=Decimal("0"))
    corporate = [c for c in customers if c.customer_type == Customer.TYPE_CORPORATE and c.outstanding > 0]
    corp_total = sum((c.outstanding for c in corporate), start=Decimal("0"))
    return {
        "total": str(total),
        "corporate": str(corp_total),
        "corporate_accounts": len(corporate),
    }


class DashboardView(ModuleAPIView):
    module = "dashboard"

    def get(self, request):
        return Response({"rooms": _room_kpis(), "fnb": _fnb_kpis(), "receivables": _receivables()})


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
    if report == "accounting":
        # ERP-importable daybook: revenue, output tax and purchases (FR-ACC-005).
        from apps.procurement.models import PurchaseOrder
        r, f = _room_kpis(), _fnb_kpis()
        tax_total = sum((l.cgst + l.sgst for l in FolioLine.objects.exclude(kind=FolioLine.KIND_TAX)),
                        start=Decimal("0"))
        purchases = sum((po.total for po in PurchaseOrder.objects.filter(
            status=PurchaseOrder.RECEIVED)), start=Decimal("0"))
        rows = [
            ["Revenue", "Rooms", r["room_revenue"]],
            ["Revenue", "F&B", f["fnb_sales"]],
            ["Tax", "Output GST", str(tax_total)],
            ["Purchases", "Goods received", str(purchases)],
        ]
        return ("Accounting Export", ["Account", "Head", "Amount"], rows)
    if report == "source":
        from apps.reservations.models import Reservation
        counts = {}
        for r in Reservation.objects.all():
            counts[r.get_source_display()] = counts.get(r.get_source_display(), 0) + 1
        return ("Bookings by Source", ["Source", "Reservations"], [[k, v] for k, v in counts.items()])
    if report == "guests":
        # Statutory guest report (BRD FR-PMS-012): in-house/checked guests with KYC.
        from apps.frontoffice.models import Folio
        rows = []
        for fo in Folio.objects.select_related("room").exclude(id_number=""):
            rows.append([fo.guest_name, fo.room.number if fo.room else "—",
                         fo.id_type, fo.id_number, fo.opened_at.strftime("%Y-%m-%d")])
        return ("Guest Report", ["Guest", "Room", "ID type", "ID number", "Check-in"], rows)
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


class DayEndView(ModuleAPIView):
    """Day-end (Z) settlement: tender-wise collection totals (BRD FR-PAY-007)."""

    module = "accounting"

    def get(self, request):
        from collections import defaultdict

        from apps.frontoffice.models import Settlement
        by = defaultdict(lambda: {"amount": Decimal("0"), "count": 0, "tip": Decimal("0")})
        for s in Settlement.objects.all():
            b = by[s.tender]
            b["amount"] += s.amount
            b["count"] += 1
            b["tip"] += s.tip
        tenders = [
            {"tender": k, "amount": str(v["amount"]), "count": v["count"], "tip": str(v["tip"])}
            for k, v in sorted(by.items())
        ]
        total = sum((v["amount"] for v in by.values()), start=Decimal("0"))
        tips = sum((v["tip"] for v in by.values()), start=Decimal("0"))
        return Response({"tenders": tenders, "total": str(total), "tips": str(tips)})


class ReportView(ModuleAPIView):
    """In-app report viewer data: KPIs + a chartable series (FR-RPT-001/004)."""

    module = "reports"

    def get(self, request):
        report = request.query_params.get("report", "sales")
        if report == "sales":
            r, f = _room_kpis(), _fnb_kpis()
            return Response({
                "title": "Sales Summary",
                "kpis": [
                    {"label": "Occupancy", "value": f"{r['occupancy_pct']}%"},
                    {"label": "ADR", "value": r["adr"], "money": True},
                    {"label": "RevPAR", "value": r["revpar"], "money": True},
                    {"label": "F&B sales", "value": f["fnb_sales"], "money": True},
                ],
                "series_label": "F&B sales by channel",
                "bars": [{"name": k.title(), "value": float(v)} for k, v in f["by_mode"].items()],
            })
        if report == "tax":
            from collections import defaultdict
            agg = defaultdict(Decimal)
            for line in FolioLine.objects.exclude(kind=FolioLine.KIND_TAX):
                agg[str(line.gst_rate)] += line.cgst + line.sgst
            return Response({
                "title": "GST by Slab",
                "kpis": [{"label": "Total output tax", "value": str(sum(agg.values())), "money": True}],
                "series_label": "Output tax by GST rate",
                "bars": [{"name": f"{k}%", "value": float(v)} for k, v in sorted(agg.items())],
            })
        if report == "source":
            from apps.reservations.models import Reservation
            counts = {}
            for r in Reservation.objects.all():
                counts[r.get_source_display()] = counts.get(r.get_source_display(), 0) + 1
            return Response({
                "title": "Bookings by Source",
                "kpis": [{"label": "Total reservations", "value": sum(counts.values())}],
                "series_label": "Reservations by channel",
                "bars": [{"name": k, "value": v} for k, v in counts.items()],
            })
        # occupancy: rooms by status
        status_counts = {}
        for room in Room.objects.all():
            status_counts[room.get_status_display()] = status_counts.get(room.get_status_display(), 0) + 1
        return Response({
            "title": "Room Status",
            "kpis": [{"label": "Total rooms", "value": sum(status_counts.values())}],
            "series_label": "Rooms by status",
            "bars": [{"name": k, "value": v} for k, v in status_counts.items()],
        })


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
