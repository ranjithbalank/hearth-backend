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
    if report == "aggregator":
        # Order-level records — one row per Zomato/Swiggy/website/QR order.
        qs = (Order.objects.filter(source_platform__in=["zomato", "swiggy", "website", "qr"])
              .prefetch_related("lines__menu_item").order_by("-created_at"))
        rows = [[o.created_at.strftime("%Y-%m-%d %H:%M"), o.source_platform,
                 o.external_ref or f"order:{o.id}",
                 sum(l.qty for l in o.lines.all()), str(o.totals()["total"]),
                 o.get_status_display()]
                for o in qs]
        return ("Aggregator Order Records",
                ["Date", "Platform", "Reference", "Items", "Total", "Status"], rows)
    restaurant = _restaurant_report(report)
    if restaurant:
        # §7 exports reuse the viewer payload: KPI rows then the series.
        rows = [[k["label"], k["value"]] for k in restaurant["kpis"]]
        rows += [[b["name"], b["value"]] for b in restaurant["bars"]]
        return (restaurant["title"], ["Metric / " + restaurant["series_label"], "Value"], rows)
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


def _item_sales():
    """Per-menu-item sold qty and revenue across settled/posted orders."""
    from apps.pos.models import OrderLine
    rows = {}
    lines = (OrderLine.objects
             .filter(order__status__in=[Order.SETTLED, Order.POSTED_TO_ROOM])
             .select_related("menu_item"))
    for l in lines:
        r = rows.setdefault(l.menu_item_id, {
            "item": l.menu_item, "qty": 0, "revenue": Decimal("0")})
        r["qty"] += l.qty
        r["revenue"] += l.unit_price * l.qty
    return rows


def _consumption_cost(days=None):
    """Total recipe-consumption cost, and per-day / per-ingredient splits (spec §7)."""
    from django.utils import timezone
    from datetime import timedelta
    from apps.inventory.models import StockMovement
    qs = StockMovement.objects.filter(kind=StockMovement.CONSUMPTION).select_related("ingredient")
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    total = Decimal("0")
    by_day, by_ingredient = {}, {}
    for m in qs:
        cost = -m.qty * (m.ingredient.unit_cost or Decimal("0"))
        total += cost
        day = m.created_at.date().isoformat()
        by_day[day] = by_day.get(day, Decimal("0")) + cost
        by_ingredient[m.ingredient.name] = by_ingredient.get(m.ingredient.name, Decimal("0")) + cost
    return total, by_day, by_ingredient


def _restaurant_report(report):
    """§7 restaurant analytics: returns the viewer payload, or None if not ours."""
    from apps.inventory.models import StockMovement
    from apps.recipes.models import Recipe

    if report == "recipe_consumption":
        sales = _item_sales()
        rows = []
        for r in Recipe.objects.select_related("menu_item").prefetch_related("lines__ingredient"):
            sold = sales.get(r.menu_item_id, {"qty": 0})["qty"]
            if not sold:
                continue
            rows.append({"name": r.menu_item.name,
                         "value": float(r.plate_cost * sold)})
        rows.sort(key=lambda x: -x["value"])
        return {
            "title": "Recipe-wise Consumption",
            "kpis": [
                {"label": "Recipes consumed", "value": len(rows)},
                {"label": "Ingredient cost of sales", "value": str(round(sum(Decimal(str(x["value"])) for x in rows), 2)), "money": True},
            ],
            "series_label": "Ingredient cost by recipe",
            "bars": rows[:12],
        }

    if report == "sales_vs_consumption":
        from datetime import datetime, timedelta
        from django.utils import timezone
        _, by_day, _ = _consumption_cost(days=14)
        sales_by_day = {}
        since = timezone.now() - timedelta(days=14)
        orders = Order.objects.filter(status__in=[Order.SETTLED, Order.POSTED_TO_ROOM],
                                      created_at__gte=since)
        for o in orders.prefetch_related("lines__menu_item"):
            day = o.created_at.date().isoformat()
            sales_by_day[day] = sales_by_day.get(day, Decimal("0")) + o.totals()["total"]
        total_sales = sum(sales_by_day.values(), start=Decimal("0"))
        total_cons = sum(by_day.values(), start=Decimal("0"))
        pct = round(float(total_cons / total_sales * 100), 1) if total_sales else 0
        days = sorted(set(by_day) | set(sales_by_day))  # ISO dates sort chronologically
        return {
            "title": "Daily Sales vs Consumption",
            "kpis": [
                {"label": "F&B sales (14d)", "value": str(total_sales), "money": True},
                {"label": "Consumption cost (14d)", "value": str(round(total_cons, 2)), "money": True},
                {"label": "Food cost", "value": f"{pct}%"},
            ],
            "series_label": "Consumption cost by day (₹)",
            "bars": [{"name": datetime.fromisoformat(d).strftime("%d %b"),
                      "value": float(by_day.get(d, 0))} for d in days],
        }

    if report == "purchase_vs_consumption":
        from apps.inventory.models import Ingredient
        purchased = consumed = Decimal("0")
        by_ing = {}
        for m in (StockMovement.objects
                  .filter(kind__in=[StockMovement.RECEIPT, StockMovement.CONSUMPTION])
                  .select_related("ingredient")):
            value = abs(m.qty) * (m.ingredient.unit_cost or Decimal("0"))
            if m.kind == StockMovement.RECEIPT:
                purchased += value
            else:
                consumed += value
                by_ing[m.ingredient.name] = by_ing.get(m.ingredient.name, Decimal("0")) + value
        bars = sorted(({"name": k, "value": float(v)} for k, v in by_ing.items()),
                      key=lambda x: -x["value"])[:12]
        return {
            "title": "Purchase vs Consumption",
            "kpis": [
                {"label": "Purchased value", "value": str(round(purchased, 2)), "money": True},
                {"label": "Consumed value", "value": str(round(consumed, 2)), "money": True},
            ],
            "series_label": "Consumption value by material (₹)",
            "bars": bars,
        }

    if report == "food_cost":
        total_cons, _, by_ingredient = _consumption_cost()
        fnb = Decimal(_fnb_kpis()["fnb_sales"])
        pct = round(float(total_cons / fnb * 100), 1) if fnb else 0
        bars = sorted(({"name": k, "value": float(v)} for k, v in by_ingredient.items()),
                      key=lambda x: -x["value"])[:12]
        return {
            "title": "Food Cost",
            "kpis": [
                {"label": "F&B revenue", "value": str(fnb), "money": True},
                {"label": "Ingredient cost", "value": str(round(total_cons, 2)), "money": True},
                {"label": "Food cost", "value": f"{pct}%"},
            ],
            "series_label": "Cost contribution by material (₹)",
            "bars": bars,
        }

    if report == "aggregator":
        labels = {"zomato": "Zomato", "swiggy": "Swiggy",
                  "website": "Website", "qr": "QR ordering"}
        qs = (Order.objects.filter(source_platform__in=labels)
              .prefetch_related("lines__menu_item"))
        received = 0
        settled_total = Decimal("0")
        by_platform = {}
        for o in qs:
            received += 1
            if o.status in (Order.SETTLED, Order.POSTED_TO_ROOM):
                t = o.totals()["total"]
                settled_total += t
                name = labels[o.source_platform]
                by_platform[name] = by_platform.get(name, Decimal("0")) + t
        settled_count = sum(1 for o in qs if o.status in (Order.SETTLED, Order.POSTED_TO_ROOM))
        avg = round(settled_total / settled_count, 2) if settled_count else Decimal("0")
        return {
            "title": "Zomato / Swiggy — Aggregator Orders",
            "kpis": [
                {"label": "Online sales (settled)", "value": str(settled_total), "money": True},
                {"label": "Orders received", "value": received},
                {"label": "Avg order value", "value": str(avg), "money": True},
            ],
            "series_label": "Cumulative sales by platform (₹)",
            "bars": sorted(({"name": k, "value": float(v)} for k, v in by_platform.items()),
                           key=lambda x: -x["value"]),
        }

    if report == "item_profitability":
        from apps.recipes.models import Recipe
        sales = _item_sales()
        recipes = {r.menu_item_id: r for r in
                   Recipe.objects.select_related("menu_item").prefetch_related("lines__ingredient")}
        rows, gross = [], Decimal("0")
        for mid, s in sales.items():
            cost = recipes[mid].plate_cost * s["qty"] if mid in recipes else Decimal("0")
            profit = s["revenue"] - cost
            gross += profit
            rows.append({"name": s["item"].name, "value": float(profit)})
        rows.sort(key=lambda x: -x["value"])
        return {
            "title": "Menu Item Profitability",
            "kpis": [
                {"label": "Gross profit (F&B)", "value": str(round(gross, 2)), "money": True},
                {"label": "Items sold", "value": len(rows)},
            ],
            "series_label": "Gross profit by item (₹, ex-recipe items at full margin)",
            "bars": rows[:12],
        }
    return None


class ReportView(ModuleAPIView):
    """In-app report viewer data: KPIs + a chartable series (FR-RPT-001/004)."""

    module = "reports"

    def get(self, request):
        report = request.query_params.get("report", "sales")
        restaurant = _restaurant_report(report)
        if restaurant:
            return Response(restaurant)
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
                "Discounts & Voids", "Zomato / Swiggy Aggregators"]},
            {"group": "Restaurant Inventory", "tiles": [
                "Recipe-wise Consumption", "Daily Sales vs Consumption",
                "Purchase vs Consumption", "Food Cost",
                "Menu Item Profitability"]},
            {"group": "Finance", "tiles": [
                "Tax / GST", "City Ledger / AR Aging"]},
            {"group": "Guests", "tiles": ["Guest & Loyalty"]},
        ]
        return Response(groups)
