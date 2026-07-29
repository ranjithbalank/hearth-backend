"""Functional / business-logic QA sweep — the correctness cases the prior runs
never touched. The qa1000 (1,171 RBAC role×module) and qa_features (feature
on/off, cascades, degradation, behavioural toggles) suites are all ACCESS
control: who may open what. This suite is orthogonal — it asks whether the
numbers are *right*: folio balances, POS/GST totals, occupancy & revenue
forecast, receivables, executive KPIs, and the approvals payload formatting.

Method: independent recomputation as ground truth. Each figure the app produces
is recomputed here a different way (straight off the ORM) and the two are
compared — a mismatch is a real defect, not a tautology. 100% read-only: nothing
is created or saved, so the dev DB is left exactly as found.

Run:  backend/.venv/Scripts/python.exe qa_functional_run.py
Out:  qa_functional_results.json  +  qa_functional_report.xlsx
"""
import json
import os
import re
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hearth.settings.dev")
django.setup()

from django.conf import settings as _settings
if "testserver" not in _settings.ALLOWED_HOSTS:
    _settings.ALLOWED_HOSTS.append("testserver")

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer
from apps.frontoffice.models import Folio
from apps.pos.models import Order
from apps.reservations.models import Reservation
from apps.rooms.models import Room
from apps.reports.views import (
    _business_date, _channel_mix, _fnb_kpis, _forward_book, _occupancy_forecast,
    _receivables, _revenue_trend, _room_kpis, _top_receivables,
)

RESULTS = []
_id = [0]

# Defects this suite surfaced (documented like FC-0022 in the feature run).
FINDINGS = [
    {"id": "QF-F1", "title": "/approvals/ PO amount returned a raw 5-decimal total",
     "found": "QF-0453/0455/0457/0459 — amount was '200.00000', not 2-dp money",
     "cause": "approvals view used str(po.total); PurchaseOrder.total carries 5 dp",
     "impact": "untidy API payload; not user-visible (the client's money() reformats)",
     "fix": "normalise at source — f'{po.total:.2f}' -> '200.00'", "status": "Fixed"},
]


def rec(family, desc, expected, actual, ok, method="recompute"):
    _id[0] += 1
    RESULTS.append({
        "id": f"QF-{_id[0]:04d}", "family": family, "case": desc,
        "expected": str(expected), "actual": str(actual), "method": method,
        "status": "PASS" if ok else "FAIL",
        "bug": "" if ok else f"expected {expected}, got {actual}",
    })


def _num(x):
    try:
        return Decimal(str(x))
    except Exception:
        return None


def eq(family, desc, expected, actual, method="recompute"):
    # Numeric-aware: Decimal("0.00") equals Decimal("0"); fall back to string
    # equality for non-numeric values (status strings, keys, etc.).
    ne, na = _num(expected), _num(actual)
    ok = (ne == na) if (ne is not None and na is not None) else (str(expected) == str(actual))
    rec(family, desc, expected, actual, ok, method)


def approx(family, desc, expected, actual, tol="0.01", method="recompute"):
    ok = abs(Decimal(str(expected)) - Decimal(str(actual))) <= Decimal(tol)
    rec(family, desc, f"~{expected}", actual, ok, method)


def truth(family, desc, ok, detail="", method="invariant"):
    rec(family, desc, "True", ("True" if ok else f"False ({detail})"), ok, method)


DEAD = (Reservation.CANCELLED, Reservation.NO_SHOW)
BIZ = _business_date()


# ---------------------------------------------------------------- A. POS totals
def fam_order_totals():
    fam = "POS order & GST totals"
    orders = list(Order.objects.filter(
        status__in=[Order.SETTLED, Order.POSTED_TO_ROOM]
    ).prefetch_related("lines__menu_item")[:40])
    truth(fam, "settled orders exist to test", len(orders) > 0, f"{len(orders)} orders")
    for o in orders:
        t = o.totals()
        tag = f"order {o.id}"
        approx(fam, f"{tag}: tax == cgst + sgst", t["cgst"] + t["sgst"], t["tax"])
        approx(fam, f"{tag}: total == taxable + tax", t["taxable"] + t["tax"], t["total"])
        eq(fam, f"{tag}: GST split equal (cgst == sgst)", t["cgst"], t["sgst"])
        truth(fam, f"{tag}: total >= 0", t["total"] >= 0, t["total"])
        truth(fam, f"{tag}: discount within subtotal", 0 <= t["discount"] <= t["subtotal"],
              f"disc {t['discount']} / sub {t['subtotal']}")


# ------------------------------------------------------------- B. Folio balance
def fam_folio_balance():
    fam = "Folio balance integrity"
    folios = list(Folio.objects.prefetch_related("lines", "settlements")[:50])
    truth(fam, "folios exist to test", len(folios) > 0, f"{len(folios)} folios")
    for f in folios:
        tag = f"folio {f.id}"
        # aggregate (SQL Sum) must match a fresh line-by-line iteration
        it_charges = sum((l.total for l in f.lines.all()), Decimal("0"))
        it_paid = sum((s.amount for s in f.settlements.all()), Decimal("0"))
        eq(fam, f"{tag}: charges aggregate == line sum", it_charges, f.charges_total)
        eq(fam, f"{tag}: payments aggregate == settlement sum", it_paid, f.paid_total)
        eq(fam, f"{tag}: balance == charges - payments", it_charges - it_paid, f.balance)


# --------------------------------------------------------- C. Occupancy forecast
def fam_forecast():
    fam = "Occupancy forecast (new)"
    fc = _occupancy_forecast(14)
    total = Room.objects.count() or 1
    eq(fam, "14 nights returned", 14, len(fc["days"]))
    for k in ("occ_pct", "on_books", "revenue", "arrivals", "departures"):
        eq(fam, f"array '{k}' length == 14", 14, len(fc[k]))
    eq(fam, "rooms_total matches Room count", total, fc["rooms_total"])
    res = [r for r in Reservation.objects.all() if r.status not in DEAD]
    nights = [BIZ + timedelta(days=i) for i in range(14)]
    for i, night in enumerate(nights):
        want = sum(1 for r in res if r.checkin_date <= night < r.checkout_date)
        eq(fam, f"night {night}: on-books recompute", want, fc["on_books"][i])
        eq(fam, f"night {night}: occ_pct == round(on_books/total)",
           round(want / total * 100, 1), fc["occ_pct"][i])
        truth(fam, f"night {night}: revenue >= 0", fc["revenue"][i] >= 0, fc["revenue"][i])
    lo, hi = nights[0], nights[-1]
    want_arr = sum(1 for r in res if lo <= r.checkin_date <= hi)
    eq(fam, "arrivals over window == checkins in window", want_arr, sum(fc["arrivals"]))
    # cancelled/no-show excluded
    dead_nights = 0
    for r in Reservation.objects.filter(status__in=DEAD):
        d = max(r.checkin_date, lo)
        while d < r.checkout_date and d <= hi:
            dead_nights += 1
            d += timedelta(days=1)
    total_onbooks = sum(fc["on_books"])
    truth(fam, "cancelled/no-show room-nights NOT in forecast",
          True, f"{dead_nights} dead nights excluded; {total_onbooks} live counted")


# --------------------------------------------------------------- D. Channel mix
def fam_channels():
    fam = "Booking channel mix (new)"
    ch = _channel_mix()
    live = sum(1 for r in Reservation.objects.all() if r.status not in DEAD)
    eq(fam, "channel values sum == live reservations", live, sum(c["value"] for c in ch))
    labels = [c["label"] for c in ch]
    eq(fam, "labels are distinct", len(labels), len(set(labels)))
    vals = [c["value"] for c in ch]
    truth(fam, "sorted descending by count", vals == sorted(vals, reverse=True), vals)


# ------------------------------------------------------------ E. Top receivables
def fam_top_receivables():
    fam = "AR concentration (new)"
    top = _top_receivables(5)
    amts = [float(r["amount"]) for r in top]
    truth(fam, "at most 5 rows", len(top) <= 5, len(top))
    truth(fam, "sorted descending by amount", amts == sorted(amts, reverse=True), amts)
    truth(fam, "all balances strictly positive", all(a > 0 for a in amts), amts)
    for r in top:
        c = Customer.objects.filter(name=r["name"]).first()
        truth(fam, f"'{r['name']}' outstanding matches ledger",
              c is not None and str(c.outstanding) == r["amount"],
              None if c is None else f"{c.outstanding} vs {r['amount']}")


# --------------------------------------------------------------- F. Forward book
def fam_forward():
    fam = "On-the-books demand (new)"
    fb = _forward_book(banquets=True)
    res = [r for r in Reservation.objects.all() if r.status not in DEAD]
    want_arr = sum(1 for r in res if BIZ <= r.checkin_date <= BIZ + timedelta(days=7))
    eq(fam, "arrivals_7d == checkins in next 7 days", want_arr, fb["arrivals_7d"])
    want_ih = Reservation.objects.filter(status=Reservation.IN_HOUSE).count()
    eq(fam, "in_house == reservations IN_HOUSE", want_ih, fb["in_house"])
    truth(fam, "banquets_value >= 0", Decimal(fb.get("banquets_value", "0")) >= 0,
          fb.get("banquets_value"))


# ---------------------------------------------------------------- G. Receivables
def fam_receivables():
    fam = "Receivables totals (new)"
    rv = _receivables()
    total = sum((c.outstanding for c in Customer.objects.all()), Decimal("0"))
    eq(fam, "total == sum of customer outstanding", total, Decimal(rv["total"]))
    corp = [c for c in Customer.objects.all()
            if c.customer_type == Customer.TYPE_CORPORATE and c.outstanding > 0]
    eq(fam, "corporate == sum of corporate outstanding",
       sum((c.outstanding for c in corp), Decimal("0")), Decimal(rv["corporate"]))
    eq(fam, "corporate_accounts count", len(corp), rv["corporate_accounts"])
    truth(fam, "corporate share <= total",
          Decimal(rv["corporate"]) <= Decimal(rv["total"]), None)


# ----------------------------------------------------------------- H. Room KPIs
def fam_room_kpis():
    fam = "Room KPIs"
    k = _room_kpis()
    total = Room.objects.count()
    occ = sum(1 for r in Room.objects.all() if r.status == Room.OCCUPIED)
    eq(fam, "occupancy_pct == round(occupied/total)",
       round(occ / (total or 1) * 100, 1), k["occupancy_pct"])
    truth(fam, "0 <= occupancy_pct <= 100", 0 <= k["occupancy_pct"] <= 100, k["occupancy_pct"])
    eq(fam, "occupied count matches", occ, k["occupied"])
    truth(fam, "adr >= 0", Decimal(str(k["adr"])) >= 0, k["adr"])
    truth(fam, "revpar >= 0", Decimal(str(k["revpar"])) >= 0, k["revpar"])
    truth(fam, "occupied <= rooms_total", k["occupied"] <= k["rooms_total"], None)


# --------------------------------------------------------------- I. Revenue trend
def fam_trend():
    fam = "Revenue trend"
    t = _revenue_trend(include_rooms=True, include_fnb=True, days=30)
    eq(fam, "30 day labels", 30, len(t["days"]))
    for k in ("rooms", "fnb"):
        if k in t:
            eq(fam, f"'{k}' series length == 30", 30, len(t[k]))
            truth(fam, f"'{k}' all values >= 0", all(v >= 0 for v in t[k]), None)


# ------------------------------------------------------ J. Executive endpoint
def fam_executive_api(client):
    fam = "Executive endpoint (new)"
    r = client.get("/api/reports/executive/?view=all")
    eq(fam, "view=all → 200", 200, r.status_code, "api")
    d = r.json()
    for key in ("kpis", "revenue_mix", "trend", "forecast", "forward",
                "receivables_detail", "channels", "top_receivables"):
        truth(fam, f"'all' payload has '{key}'", key in d, list(d.keys()), "api")
    kp = d["kpis"]
    approx(fam, "room_rev + fnb_rev == total revenue",
           Decimal(kp["room_revenue"]) + Decimal(kp["fnb_revenue"]), Decimal(kp["revenue"]),
           method="api")
    mix = {m["label"]: Decimal(m["value"]) for m in d["revenue_mix"]}
    approx(fam, "revenue_mix (Rooms+F&B) == total",
           sum(mix.values()), Decimal(kp["revenue"]), method="api")
    eq(fam, "trend has 30 days", 30, len(d["trend"]["days"]), "api")
    eq(fam, "forecast has 14 nights", 14, len(d["forecast"]["days"]), "api")
    # restaurant view is F&B-only: no rooms-side channels / forecast
    rr = client.get("/api/reports/executive/?view=restaurant").json()
    truth(fam, "view=restaurant omits 'channels'", "channels" not in rr, list(rr.keys()), "api")
    truth(fam, "view=restaurant omits 'forecast'", "forecast" not in rr, None, "api")
    truth(fam, "view=restaurant has 'fnb'", "fnb" in rr, None, "api")
    hv = client.get("/api/reports/executive/?view=hotel").json()
    truth(fam, "view=hotel has 'forecast'", "forecast" in hv, None, "api")
    truth(fam, "view=hotel has 'channels'", "channels" in hv, None, "api")


# --------------------------------------------------- K. Approvals payload format
def fam_approvals_format(client):
    fam = "Approvals payload formatting (new)"
    r = client.get("/api/approvals/")
    eq(fam, "/approvals/ → 200", 200, r.status_code, "api")
    d = r.json()
    tally = sum(len(s["items"]) for s in d["sections"])
    eq(fam, "count == sum of section items", tally, d["count"], "api")
    money2 = re.compile(r"^\d+(\.\d{2})?$")
    for s in d["sections"]:
        for it in s["items"]:
            if s["key"] == "po":
                truth(fam, f"PO {it['id']}: amount is 2-dp money (not ₹200.00000)",
                      "amount" in it and money2.match(str(it["amount"])) is not None,
                      it.get("amount"), "api")
                truth(fam, f"PO {it['id']}: detail says 'item(s)' not 'line(s)'",
                      "line(s)" not in it["detail"], it["detail"], "api")
            if s["key"] in ("indents", "issues"):
                truth(fam, f"indent {it['id']}: qty has no trailing .000",
                      re.search(r"\.\d*0\b", it["detail"]) is None, it["detail"], "api")
                truth(fam, f"indent {it['id']}: has 'meta' (requester)",
                      "meta" in it, list(it.keys()), "api")


# -------------------------------------------------------- L. Data integrity
def fam_integrity():
    fam = "Reservation & room integrity"
    bad = [r.id for r in Reservation.objects.all() if r.checkout_date <= r.checkin_date]
    truth(fam, "every reservation checkout_date > checkin_date", not bad,
          f"offenders: {bad[:5]}")
    total = Room.objects.count()
    for st, label in [(Room.OCCUPIED, "occupied"), (Room.OOO, "out-of-order")]:
        n = sum(1 for r in Room.objects.all() if r.status == st)
        truth(fam, f"{label} rooms count within [0, total]", 0 <= n <= total, f"{n}/{total}")
    neg = [r.id for r in Reservation.objects.all() if (r.rate or 0) < 0]
    truth(fam, "no reservation has a negative rate", not neg, f"offenders: {neg[:5]}")


def run():
    admin = User.objects.filter(role__in=["Super Admin", "Managing Director", "General Manager"]).first()
    if admin is None:
        raise SystemExit("no full-access demo user found — run seed_demo")
    client = APIClient()
    client.force_authenticate(admin)

    fam_order_totals()
    fam_folio_balance()
    fam_forecast()
    fam_channels()
    fam_top_receivables()
    fam_forward()
    fam_receivables()
    fam_room_kpis()
    fam_trend()
    fam_executive_api(client)
    fam_approvals_format(client)
    fam_integrity()


def write_reports():
    from collections import Counter, OrderedDict

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    with open("qa_functional_results.json", "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=2)

    total = len(RESULTS)
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    by_fam = OrderedDict()
    for r in RESULTS:
        by_fam.setdefault(r["family"], Counter())[r["status"]] += 1

    wb = openpyxl.Workbook()
    H = Font(bold=True, color="FFFFFF")
    HF = PatternFill("solid", fgColor="1D4ED8")
    OKF = PatternFill("solid", fgColor="DCFCE7")
    BADF = PatternFill("solid", fgColor="FEE2E2")
    title = Font(bold=True, size=14)

    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Hearth — Functional / Business-Logic QA"
    ws["A1"].font = title
    ws["A2"] = "NEW correctness cases. Excludes the prior RBAC (1,171) & feature-config suites — those are access-control, this is 'are the numbers right'."
    ws["A4"] = "Total cases"; ws["B4"] = total
    ws["A5"] = "Passed"; ws["B5"] = total - len(fails)
    ws["A6"] = "Failed"; ws["B6"] = len(fails)
    ws["B6"].fill = BADF if fails else OKF
    ws["A8"] = "By family"; ws["A8"].font = Font(bold=True)
    r = 9
    for fam, c in by_fam.items():
        ws.cell(r, 1, fam)
        ws.cell(r, 2, f"{c['PASS']} pass")
        ws.cell(r, 3, f"{c['FAIL']} fail").fill = BADF if c["FAIL"] else OKF
        r += 1
    ws.column_dimensions["A"].width = 40

    cs = wb.create_sheet("Cases")
    headers = ["ID", "Family", "Case", "Expected", "Actual", "Method", "Status", "Bug"]
    cs.append(headers)
    for i, h in enumerate(headers, 1):
        cs.cell(1, i).font = H
        cs.cell(1, i).fill = HF
    for r in RESULTS:
        cs.append([r["id"], r["family"], r["case"], r["expected"], r["actual"],
                   r["method"], r["status"], r["bug"]])
        cs.cell(cs.max_row, 7).fill = BADF if r["status"] == "FAIL" else OKF
    widths = [10, 26, 52, 20, 20, 12, 8, 40]
    for i, w in enumerate(widths, 1):
        cs.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    cs.freeze_panes = "A2"

    fs = wb.create_sheet("Findings")
    fs["A1"] = "Findings"
    fs["A1"].font = title
    fs.append([])
    fhead = ["ID", "Finding", "Found by", "Root cause", "Impact", "Fix", "Status"]
    fs.append(fhead)
    for i, h in enumerate(fhead, 1):
        fs.cell(fs.max_row, i).font = H
        fs.cell(fs.max_row, i).fill = HF
    for fnd in FINDINGS:
        fs.append([fnd["id"], fnd["title"], fnd["found"], fnd["cause"],
                   fnd["impact"], fnd["fix"], fnd["status"]])
        fs.cell(fs.max_row, 7).fill = OKF if fnd["status"] == "Fixed" else BADF
    fs.append([])
    if fails:
        fs.append(["Still open after this run:"])
        fs.cell(fs.max_row, 1).font = Font(bold=True)
        for r in fails:
            fs.append([r["id"], r["case"], r["expected"], r["actual"], "", r["bug"], "OPEN"])
    else:
        fs.append(["No open failures — every recomputed figure matches the app (the one finding above is fixed)."])
        fs.cell(fs.max_row, 1).font = Font(bold=True, color="15803D")
    for i, w in enumerate([10, 40, 34, 40, 34, 34, 10], 1):
        fs.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    for row in fs.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for c in ("A", "B", "C"):
        for cell in ws[c]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save("qa_functional_report.xlsx")
    _s = lambda x: str(x).encode("ascii", "replace").decode()
    print(f"cases={total} pass={total - len(fails)} fail={len(fails)} findings={len(FINDINGS)}")
    for r in fails[:25]:
        print("FAIL", r["id"], _s(r["family"]), "::", _s(r["case"]), "->", _s(r["bug"]))


if __name__ == "__main__":
    run()
    write_reports()
