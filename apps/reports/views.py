import csv
import io
from decimal import Decimal

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import currency_symbol
from apps.accounts.permissions import ModulePermission
from apps.crm.models import Customer
from apps.frontoffice.models import FolioLine
from apps.pos.models import Order
from apps.rooms.models import Room


def _parse_range(request):
    """Optional ?from=YYYY-MM-DD&to=YYYY-MM-DD window shared by every report.
    A malformed date is treated as unset rather than erroring the report."""
    from datetime import date

    def parse(name):
        raw = request.query_params.get(name, "")
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None
    return parse("from"), parse("to")


def _between(qs, field, f, t, date_field=False):
    """Filter a queryset's datetime (or date) field to the [f, t] day window."""
    suffix = "" if date_field else "__date"
    if f:
        qs = qs.filter(**{f"{field}{suffix}__gte": f})
    if t:
        qs = qs.filter(**{f"{field}{suffix}__lte": t})
    return qs


def _room_kpis(f=None, t=None):
    # Room-status counts are a live snapshot; only the revenue side is
    # windowed by the date range.
    rooms = list(Room.objects.all())
    total = len(rooms) or 1
    occupied = sum(1 for r in rooms if r.status == Room.OCCUPIED)
    available = sum(1 for r in rooms if r.status in Room.SELLABLE)
    dirty = sum(1 for r in rooms if r.status in (Room.VACANT_DIRTY, Room.CLEANING))
    ooo = sum(1 for r in rooms if r.status == Room.OOO)
    room_lines = _between(FolioLine.objects.filter(kind=FolioLine.KIND_ROOM),
                          "created_at", f, t)
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


def _fnb_kpis(f=None, t=None):
    orders = _between(Order.objects.filter(
        status__in=[Order.SETTLED, Order.POSTED_TO_ROOM]
    ), "created_at", f, t).prefetch_related("lines__menu_item")
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
    """Rooms + F&B side by side for cross-cutting roles (Admin, Finance, and
    Super Admin/MD/GM if they land here) — but Hotel Manager only runs the
    hotel side and Restaurant Manager only runs the restaurant side, so each
    gets just their own numbers, not the other side's."""

    module = "dashboard"

    def get(self, request):
        from apps.accounts.constants import ROLE_HOTEL_MGR, ROLE_REST_MGR
        role = getattr(request.user, "role", "")
        body = {}
        if role != ROLE_REST_MGR:
            body["rooms"] = _room_kpis()
            body["receivables"] = _receivables()
        if role != ROLE_HOTEL_MGR:
            body["fnb"] = _fnb_kpis()
        body["view"] = ("hotel" if role == ROLE_HOTEL_MGR
                        else "restaurant" if role == ROLE_REST_MGR else "combined")
        return Response(body)


class ExecutiveView(ModuleAPIView):
    """Group performance — combined by default, but MD/GM/CEO/Super Admin can
    also drill into just the hotel side or just the restaurant side via ?view=."""

    module = "execdashboard"

    def get(self, request):
        view = request.query_params.get("view", "all")
        if view not in ("all", "hotel", "restaurant"):
            view = "all"
        rooms = _room_kpis()
        fnb = _fnb_kpis()
        receivables = _receivables()
        room_rev = Decimal(rooms["room_revenue"])
        fnb_rev = Decimal(fnb["fnb_sales"])

        body = {"view": view}
        if view in ("all", "hotel"):
            body["rooms"] = rooms
        if view in ("all", "restaurant"):
            body["fnb"] = fnb
        if view == "all":
            total_rev = room_rev + fnb_rev
            body["kpis"] = {
                "revenue": str(total_rev),
                "occupancy_pct": rooms["occupancy_pct"],
                "room_revenue": str(room_rev),
                "fnb_revenue": str(fnb_rev),
                "receivables": receivables["total"],
            }
            body["revenue_mix"] = [
                {"label": "Rooms", "value": str(room_rev)},
                {"label": "F&B", "value": str(fnb_rev)},
            ]
        elif view == "hotel":
            body["kpis"] = {
                "revenue": rooms["room_revenue"],
                "occupancy_pct": rooms["occupancy_pct"],
                "room_revenue": rooms["room_revenue"],
                "receivables": receivables["total"],
            }
        else:  # restaurant
            body["kpis"] = {
                "revenue": fnb["fnb_sales"],
                "fnb_revenue": fnb["fnb_sales"],
                "order_count": fnb["order_count"],
            }
        return Response(body)


def _scoped_sales_kpis(role, f=None, t=None):
    """Sales Summary content, scoped like the Dashboard: Restaurant Manager
    sees F&B only, Hotel Manager sees rooms only, everyone else authorized
    for 'sales' (full access, Finance, CEO) sees both. Returns (rooms, fnb),
    either of which is None when that side is hidden from this role."""
    from apps.accounts.constants import ROLE_HOTEL_MGR, ROLE_REST_MGR
    rooms = _room_kpis(f, t) if role != ROLE_REST_MGR else None
    fnb = _fnb_kpis(f, t) if role != ROLE_HOTEL_MGR else None
    return rooms, fnb


class SalesSummaryView(ModuleAPIView):
    module = "reports"

    def get(self, request):
        role = getattr(request.user, "role", "")
        rooms, fnb = _scoped_sales_kpis(role)
        body = {}
        if rooms is not None:
            body["rooms"] = rooms
        if fnb is not None:
            body["fnb"] = fnb
        return Response(body)


def _business_date():
    """The property's operational date (advanced by night audit), not the wall
    clock — reports about 'today' mean the open business day."""
    from django.utils import timezone
    from apps.accounts.views import get_property
    return get_property().business_date or timezone.localdate()


def _operational_report(report, f=None, t=None):
    """Front-office / floor operational reports (arrivals, night audit,
    no-show & cancellation, discounts & voids): viewer payload, or None."""
    from datetime import timedelta

    if report == "arrivals":
        from apps.reservations.models import Reservation
        qs = list(Reservation.objects.select_related("room", "room_type"))
        dead = (Reservation.CANCELLED, Reservation.NO_SHOW)
        # Default: the open business date. With a range: that whole window.
        lo = f or (t or _business_date())
        hi = t or (f or _business_date())
        if lo > hi:
            lo, hi = hi, lo
        arriving = [r for r in qs if lo <= r.checkin_date <= hi and r.status not in dead]
        departing = [r for r in qs if lo <= r.checkout_date <= hi
                     and r.status in (Reservation.IN_HOUSE, Reservation.CHECKED_OUT)]
        in_house = sum(1 for r in qs if r.status == Reservation.IN_HOUSE)
        # Arrivals per day — over the range, or the coming week by default.
        start, days = (lo, min((hi - lo).days + 1, 31)) if (f or t) else (lo, 7)
        bars = []
        for i in range(days):
            day = start + timedelta(days=i)
            bars.append({"name": day.strftime("%d %b"),
                         "value": sum(1 for r in qs
                                      if r.checkin_date == day and r.status not in dead)})
        rows = [["Arrival", r.guest_name, r.room.number if r.room else r.room_type.code,
                 r.get_source_display(), r.get_status_display(), str(r.checkin_date)]
                for r in arriving]
        rows += [["Departure", r.guest_name, r.room.number if r.room else r.room_type.code,
                  r.get_source_display(), r.get_status_display(), str(r.checkout_date)]
                 for r in departing]
        when = (lo.strftime("%d %b") if lo == hi
                else f"{lo.strftime('%d %b')} – {hi.strftime('%d %b')}")
        return {
            "title": "Arrivals & Departures",
            "kpis": [
                {"label": f"Arrivals ({when})", "value": len(arriving)},
                {"label": "Departures", "value": len(departing)},
                {"label": "In-house now", "value": in_house},
            ],
            "series_label": "Arrivals by day" if (f or t) else "Expected arrivals — next 7 days",
            "bars": bars,
            "records": {"columns": ["Movement", "Guest", "Room", "Source", "Status", "Date"],
                        "rows": rows},
        }

    if report == "night_audit":
        from apps.frontoffice.models import NightAuditRun
        runs = list(_between(NightAuditRun.objects.all(), "business_date", f, t,
                             date_field=True)[:60])  # newest first
        revenue = sum((r.room_revenue for r in runs), start=Decimal("0"))
        tax = sum((r.tax_posted for r in runs), start=Decimal("0"))
        last = runs[0] if runs else None
        return {
            "title": "Night Audit / Daily Revenue",
            "kpis": [
                {"label": "Last audit", "value":
                    last.business_date.strftime("%d %b %Y") if last else "never"},
                {"label": "Room nights posted", "value": sum(r.rooms_posted for r in runs)},
                {"label": "Room revenue posted", "value": str(revenue), "money": True},
                {"label": "Tax posted", "value": str(tax), "money": True},
            ],
            "series_label": "Room revenue by business date",
            "bars": [{"name": r.business_date.strftime("%d %b"), "value": float(r.room_revenue)}
                     for r in reversed(runs[:14])],
            "records": {"columns": ["Business date", "Rooms posted", "Room revenue",
                                    "Tax posted", "Status"],
                        "rows": [[str(r.business_date), r.rooms_posted, str(r.room_revenue),
                                  str(r.tax_posted), "done" if r.completed else "pending"]
                                 for r in runs]},
        }

    if report == "noshow":
        from apps.reservations.models import Reservation
        lost = list(_between(Reservation.objects.filter(
            status__in=[Reservation.CANCELLED, Reservation.NO_SHOW]),
            "checkin_date", f, t, date_field=True).select_related("room_type"))
        noshows = [r for r in lost if r.status == Reservation.NO_SHOW]
        value_lost = sum((r.rate * r.nights for r in lost), start=Decimal("0"))
        by_source = {}
        for r in lost:
            src = r.get_source_display()
            by_source[src] = by_source.get(src, Decimal("0")) + r.rate * r.nights
        return {
            "title": "No-show & Cancellation",
            "kpis": [
                {"label": "Cancellations", "value": len(lost) - len(noshows)},
                {"label": "No-shows", "value": len(noshows)},
                {"label": "Room nights lost", "value": sum(r.nights for r in lost)},
                {"label": "Value lost", "value": str(value_lost), "money": True},
            ],
            "series_label": "Value lost by booking source",
            "bars": [{"name": k, "value": float(v)}
                     for k, v in sorted(by_source.items(), key=lambda x: -x[1])],
            "records": {"columns": ["Guest", "Type", "Check-in", "Nights", "Rate",
                                    "Source", "Status"],
                        "rows": [[r.guest_name, r.room_type.code, str(r.checkin_date),
                                  r.nights, str(r.rate), r.get_source_display(),
                                  r.get_status_display()] for r in lost]},
        }

    if report == "discounts":
        from apps.accounts.models import AuditLog
        orders = (_between(Order.objects.exclude(status=Order.OPEN), "created_at", f, t)
                  .select_related("coupon").prefetch_related("lines__menu_item"))
        buckets = {"Manual": Decimal("0"), "Coupon": Decimal("0"),
                   "Loyalty": Decimal("0"), "Voids": Decimal("0")}
        rows, disc_bills, void_bills = [], 0, 0
        disc_total = Decimal("0")
        for o in orders:
            # Voided bills are closed with the audit trail in discount_reason
            # (see OrderViewSet.void) — their full value came off the books.
            if o.discount_reason.startswith("VOID by"):
                voided = o.totals()["total"]
                void_bills += 1
                buckets["Voids"] += voided
                rows.append([o.bill_no or f"#{o.id}", o.get_mode_display(), "Void",
                             str(voided), o.discount_reason])
                continue
            tot = o.totals()
            if tot["discount"] <= 0:
                continue
            disc_bills += 1
            disc_total += tot["discount"]
            sub = tot["subtotal"]
            if o.discount_kind == Order.DISC_PERCENT:
                buckets["Manual"] += sub * o.discount_value / Decimal("100")
            elif o.discount_kind == Order.DISC_FIXED:
                buckets["Manual"] += min(o.discount_value, sub)
            if o.coupon:
                buckets["Coupon"] += o.coupon.reduction(sub)
            if o.loyalty_redeemed:
                buckets["Loyalty"] += Decimal(o.loyalty_redeemed)
            reason = o.discount_reason or (o.coupon.code if o.coupon else "") or "—"
            rows.append([o.bill_no or f"#{o.id}", o.get_mode_display(), "Discount",
                         str(tot["discount"]), reason])
        item_voids = _between(AuditLog.objects.filter(action="item_void"),
                              "created_at", f, t).count()
        return {
            "title": "Discounts & Voids",
            "kpis": [
                {"label": "Discounted bills", "value": disc_bills},
                {"label": "Discounts given", "value": str(disc_total), "money": True},
                {"label": "Voided bills", "value": void_bills},
                {"label": "Item voids (fired lines reduced)", "value": item_voids},
            ],
            "series_label": "Reduction by type",
            "bars": [{"name": k, "value": float(round(v, 2))}
                     for k, v in buckets.items() if v > 0],
            "records": {"columns": ["Bill", "Mode", "Type", "Amount", "Reason"],
                        "rows": rows},
        }

    return None


def _report_rows(report, role, f=None, t=None):
    """Return (title, header, rows) for an exportable report."""
    if report == "sales":
        rooms, fnb = _scoped_sales_kpis(role, f, t)
        rows = []
        if rooms is not None:
            rows += [
                ["Occupancy %", rooms["occupancy_pct"]],
                ["ADR", rooms["adr"]],
                ["RevPAR", rooms["revpar"]],
                ["Room revenue", rooms["room_revenue"]],
            ]
        if fnb is not None:
            rows += [
                ["F&B sales", fnb["fnb_sales"]],
                ["F&B orders", fnb["order_count"]],
            ]
        return ("Sales Summary", ["Metric", "Value"], rows)
    if report == "tax":
        from collections import defaultdict
        agg = defaultdict(lambda: [Decimal("0")] * 4)
        for line in _between(FolioLine.objects.exclude(kind=FolioLine.KIND_TAX),
                             "created_at", f, t):
            a = agg[str(line.gst_rate)]
            a[0] += line.taxable; a[1] += line.cgst; a[2] += line.sgst; a[3] += line.total
        rows = [[rate, v[0], v[1], v[2], v[3]] for rate, v in sorted(agg.items())]
        return ("GST Summary", ["Rate %", "Taxable", "CGST", "SGST", "Total"], rows)
    if report == "accounting":
        # ERP-importable daybook: revenue, output tax and purchases (FR-ACC-005).
        from apps.procurement.models import PurchaseOrder
        r, fnb = _room_kpis(f, t), _fnb_kpis(f, t)
        tax_total = sum((l.cgst + l.sgst for l in _between(
            FolioLine.objects.exclude(kind=FolioLine.KIND_TAX), "created_at", f, t)),
            start=Decimal("0"))
        purchases = sum((po.total for po in _between(PurchaseOrder.objects.filter(
            status=PurchaseOrder.RECEIVED), "created_at", f, t)), start=Decimal("0"))
        rows = [
            ["Revenue", "Rooms", r["room_revenue"]],
            ["Revenue", "F&B", fnb["fnb_sales"]],
            ["Tax", "Output GST", str(tax_total)],
            ["Purchases", "Goods received", str(purchases)],
        ]
        return ("Accounting Export", ["Account", "Head", "Amount"], rows)
    if report == "source":
        from apps.reservations.models import Reservation
        counts = {}
        for r in _between(Reservation.objects.all(), "checkin_date", f, t, date_field=True):
            counts[r.get_source_display()] = counts.get(r.get_source_display(), 0) + 1
        return ("Bookings by Source", ["Source", "Reservations"], [[k, v] for k, v in counts.items()])
    if report == "guests":
        # Statutory guest report (BRD FR-PMS-012): in-house/checked guests with KYC.
        from apps.frontoffice.models import Folio
        rows = []
        for fo in _between(Folio.objects.select_related("room").exclude(id_number=""),
                           "opened_at", f, t):
            rows.append([fo.guest_name, fo.room.number if fo.room else "—",
                         fo.id_type, fo.id_number, fo.opened_at.strftime("%Y-%m-%d")])
        return ("Guest Report", ["Guest", "Room", "ID type", "ID number", "Check-in"], rows)
    if report == "aggregator":
        # Reuse the viewer's records (already carry gross/commission/net).
        data = _restaurant_report(report, f, t)
        return ("Aggregator Order Records (Gross / Commission / Net)",
                data["records"]["columns"], data["records"]["rows"])
    operational = _operational_report(report, f, t)
    if operational:
        # Operational exports are the record-level rows (one per reservation /
        # audit run / bill) — the useful thing to open in a spreadsheet.
        return (operational["title"], operational["records"]["columns"],
                operational["records"]["rows"])
    restaurant = _restaurant_report(report, f, t)
    if restaurant:
        # §7 exports reuse the viewer payload: KPI rows then the series.
        rows = [[k["label"], k["value"]] for k in restaurant["kpis"]]
        rows += [[b["name"], b["value"]] for b in restaurant["bars"]]
        return (restaurant["title"], ["Metric / " + restaurant["series_label"], "Value"], rows)
    # occupancy / rooms
    rows = [[r.number, r.room_type.code, r.get_status_display()]
            for r in Room.objects.select_related("room_type")]
    return ("Room Status", ["Room", "Type", "Status"], rows)


class ReportExportView(ModuleAPIView):
    module = "reports"

    def get(self, request):
        from apps.accounts.constants import role_can_view_report
        report = request.query_params.get("report", "sales")
        role = getattr(request.user, "role", "")
        if not role_can_view_report(role, report):
            return Response({"detail": f"not authorized for the '{report}' report"}, status=403)
        # NB: avoid the query param name 'format' — DRF reserves it as a renderer override.
        fmt = request.query_params.get("fmt", "xlsx")
        f, t = _parse_range(request)
        title, header, rows = _report_rows(report, role, f, t)

        if fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(header)
            for row in rows:
                w.writerow(row)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{report}.csv"'
            return resp

        import re
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        # openpyxl rejects \ / * ? : [ ] in sheet names ("Night Audit / Daily Revenue").
        ws.title = re.sub(r"[\\/*?:\[\]]", "-", title)[:31]
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


def _item_sales(f=None, t=None):
    """Per-menu-item sold qty and revenue across settled/posted orders."""
    from apps.pos.models import OrderLine
    rows = {}
    lines = _between(OrderLine.objects
                     .filter(order__status__in=[Order.SETTLED, Order.POSTED_TO_ROOM]),
                     "order__created_at", f, t).select_related("menu_item")
    for l in lines:
        r = rows.setdefault(l.menu_item_id, {
            "item": l.menu_item, "qty": 0, "revenue": Decimal("0")})
        r["qty"] += l.qty
        r["revenue"] += l.unit_price * l.qty
    return rows


def _consumption_cost(days=None, f=None, t=None):
    """Total recipe-consumption cost, and per-day / per-ingredient splits (spec §7).
    An explicit from/to window replaces the rolling `days` default."""
    from django.utils import timezone
    from datetime import timedelta
    from apps.inventory.models import StockMovement
    qs = StockMovement.objects.filter(kind=StockMovement.CONSUMPTION).select_related("ingredient")
    if f or t:
        qs = _between(qs, "created_at", f, t)
    elif days:
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


def _restaurant_report(report, f=None, t=None):
    """§7 restaurant analytics: returns the viewer payload, or None if not ours."""
    from apps.inventory.models import StockMovement
    from apps.recipes.models import Recipe

    if report == "recipe_consumption":
        sales = _item_sales(f, t)
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
        ranged = bool(f or t)
        _, by_day, _ = _consumption_cost(days=None if ranged else 14, f=f, t=t)
        sales_by_day = {}
        orders = Order.objects.filter(status__in=[Order.SETTLED, Order.POSTED_TO_ROOM])
        if ranged:
            orders = _between(orders, "created_at", f, t)
        else:
            orders = orders.filter(created_at__gte=timezone.now() - timedelta(days=14))
        for o in orders.prefetch_related("lines__menu_item"):
            day = o.created_at.date().isoformat()
            sales_by_day[day] = sales_by_day.get(day, Decimal("0")) + o.totals()["total"]
        total_sales = sum(sales_by_day.values(), start=Decimal("0"))
        total_cons = sum(by_day.values(), start=Decimal("0"))
        pct = round(float(total_cons / total_sales * 100), 1) if total_sales else 0
        days = sorted(set(by_day) | set(sales_by_day))  # ISO dates sort chronologically
        win = "" if ranged else " (14d)"
        return {
            "title": "Daily Sales vs Consumption",
            "kpis": [
                {"label": f"F&B sales{win}", "value": str(total_sales), "money": True},
                {"label": f"Consumption cost{win}", "value": str(round(total_cons, 2)), "money": True},
                {"label": "Food cost", "value": f"{pct}%"},
            ],
            "series_label": f"Consumption cost by day ({currency_symbol()})",
            "bars": [{"name": datetime.fromisoformat(d).strftime("%d %b"),
                      "value": float(by_day.get(d, 0))} for d in days],
        }

    if report == "purchase_vs_consumption":
        from apps.inventory.models import Ingredient
        purchased = consumed = Decimal("0")
        by_ing = {}
        for m in _between(StockMovement.objects
                          .filter(kind__in=[StockMovement.RECEIPT, StockMovement.CONSUMPTION]),
                          "created_at", f, t).select_related("ingredient"):
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
            "series_label": f"Consumption value by material ({currency_symbol()})",
            "bars": bars,
        }

    if report == "food_cost":
        total_cons, _, by_ingredient = _consumption_cost(f=f, t=t)
        fnb = Decimal(_fnb_kpis(f, t)["fnb_sales"])
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
            "series_label": f"Cost contribution by material ({currency_symbol()})",
            "bars": bars,
        }

    if report == "aggregator":
        from apps.accounts.views import get_property
        labels = {"zomato": "Zomato", "swiggy": "Swiggy",
                  "website": "Website", "qr": "QR ordering"}
        prop = get_property()
        commission_pct = {
            "zomato": prop.zomato_commission_pct or Decimal("0"),
            "swiggy": prop.swiggy_commission_pct or Decimal("0"),
            "website": Decimal("0"), "qr": Decimal("0"),
        }
        qs = _between(Order.objects.filter(source_platform__in=labels),
                      "created_at", f, t).prefetch_related("lines__menu_item")
        received = 0
        settled_total = Decimal("0")
        net_total = Decimal("0")
        by_platform = {}
        net_by_platform = {}
        for o in qs:
            received += 1
            if o.status in (Order.SETTLED, Order.POSTED_TO_ROOM):
                t = o.totals()["total"]
                pct = commission_pct.get(o.source_platform, Decimal("0"))
                net = t * (Decimal("1") - pct / Decimal("100"))
                settled_total += t
                net_total += net
                name = labels[o.source_platform]
                by_platform[name] = by_platform.get(name, Decimal("0")) + t
                net_by_platform[name] = net_by_platform.get(name, Decimal("0")) + net
        settled_count = sum(1 for o in qs if o.status in (Order.SETTLED, Order.POSTED_TO_ROOM))
        avg = round(settled_total / settled_count, 2) if settled_count else Decimal("0")
        commission_taken = settled_total - net_total
        return {
            "title": "Zomato / Swiggy — Aggregator Orders",
            "kpis": [
                {"label": "Gross sales (settled)", "value": str(settled_total), "money": True},
                {"label": "Commission taken", "value": str(round(commission_taken, 2)), "money": True},
                {"label": "Net realization", "value": str(round(net_total, 2)), "money": True},
                {"label": "Orders received", "value": received},
                {"label": "Avg order value", "value": str(avg), "money": True},
            ],
            "series_label": "Net realization by platform, after commission (₹)",
            "bars": sorted(({"name": k, "value": float(v)} for k, v in net_by_platform.items()),
                           key=lambda x: -x["value"]),
            # Order-level records render as a table in the Reports screen.
            "records": {
                "columns": ["Date", "Platform", "Reference", "Items", "Gross", "Commission %", "Net", "Status"],
                "rows": [[o.created_at.strftime("%d %b %H:%M"), labels[o.source_platform],
                          o.external_ref or f"order:{o.id}",
                          sum(l.qty for l in o.lines.all()), str(o.totals()["total"]),
                          str(commission_pct.get(o.source_platform, Decimal("0"))),
                          str(round(o.totals()["total"] * (Decimal("1") - commission_pct.get(o.source_platform, Decimal("0")) / Decimal("100")), 2)),
                          o.get_status_display()]
                         for o in qs.order_by("-created_at")[:100]],
            },
        }

    if report == "item_profitability":
        from apps.recipes.models import Recipe
        sales = _item_sales(f, t)
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
        from apps.accounts.constants import role_can_view_report
        report = request.query_params.get("report", "sales")
        role = getattr(request.user, "role", "")
        if not role_can_view_report(role, report):
            return Response({"detail": f"not authorized for the '{report}' report"}, status=403)
        f, t = _parse_range(request)
        restaurant = _restaurant_report(report, f, t)
        if restaurant:
            return Response(restaurant)
        operational = _operational_report(report, f, t)
        if operational:
            return Response(operational)
        if report == "sales":
            rooms, fnb = _scoped_sales_kpis(role, f, t)
            kpis = []
            if rooms is not None:
                kpis += [
                    {"label": "Occupancy", "value": f"{rooms['occupancy_pct']}%"},
                    {"label": "ADR", "value": rooms["adr"], "money": True},
                    {"label": "RevPAR", "value": rooms["revpar"], "money": True},
                ]
            if fnb is not None:
                kpis.append({"label": "F&B sales", "value": fnb["fnb_sales"], "money": True})
            if fnb is not None:
                series_label = "F&B sales by channel"
                bars = [{"name": k.title(), "value": float(v)} for k, v in fnb["by_mode"].items()]
            else:
                series_label = "Room revenue"
                bars = [{"name": "Room revenue", "value": float(rooms["room_revenue"])}]
            return Response({
                "title": "Sales Summary",
                "kpis": kpis,
                "series_label": series_label,
                "bars": bars,
            })
        if report == "tax":
            from collections import defaultdict
            agg = defaultdict(Decimal)
            for line in _between(FolioLine.objects.exclude(kind=FolioLine.KIND_TAX),
                                 "created_at", f, t):
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
            for r in _between(Reservation.objects.all(), "checkin_date", f, t, date_field=True):
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
        from apps.accounts.constants import (
            ROLE_ALLOW,
            RESTAURANT_ANALYTICS_REPORTS,
            role_can_view_report,
        )
        role = getattr(request.user, "role", "")
        full = ROLE_ALLOW.get(role) == "*"

        all_reports = [
            "sales", "tax", "source", "occupancy", "accounting", "guests",
            "arrivals", "night_audit", "noshow", "discounts",
            "aggregator", "recipe_consumption", "sales_vs_consumption",
            "purchase_vs_consumption", "food_cost", "item_profitability",
        ]
        allowed_reports = all_reports if full else [
            r for r in all_reports if role_can_view_report(role, r)]
        allowed = set(allowed_reports)

        # Each group is gated on a representative report key from that
        # category — same slices as ROLE_REPORT_ACCESS, so nobody sees a
        # tile for a report they'd be 403'd on if they actually tried it.
        groups = [
            {"group": "Rooms", "gate": {"source", "occupancy", "arrivals",
                                        "night_audit", "noshow"}, "tiles": [
                "Occupancy & RevPAR", "Arrivals & Departures",
                "Night Audit / Daily Revenue", "No-show & Cancellation"]},
            {"group": "F&B", "gate": {"sales", "aggregator", "discounts"}, "tiles": [
                "F&B Sales Summary", "Item & Category Performance",
                "Discounts & Voids", "Zomato / Swiggy Aggregators"]},
            {"group": "Restaurant Inventory", "gate": RESTAURANT_ANALYTICS_REPORTS, "tiles": [
                "Recipe-wise Consumption", "Daily Sales vs Consumption",
                "Purchase vs Consumption", "Food Cost",
                "Menu Item Profitability"]},
            {"group": "Finance", "gate": {"tax", "accounting"}, "tiles": [
                "Tax / GST", "City Ledger / AR Aging"]},
            {"group": "Guests", "gate": {"guests"}, "tiles": ["Guest & Loyalty"]},
        ]
        visible = [
            {"group": g["group"], "tiles": g["tiles"]}
            for g in groups if full or allowed & g["gate"]
        ]
        return Response({"groups": visible, "allowed_reports": allowed_reports})
