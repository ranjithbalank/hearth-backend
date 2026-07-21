"""1000+ case deep QA sweep, re-run against the current build.

In-process via DRF's APIClient + force_authenticate (no live server, no auth
throttle, no test-data collisions from repeated runs) — ground truth for RBAC
comes straight from apps.accounts.rbac.can_access() (the same DB-aware check
the API itself enforces), not a hardcoded expectation table, exactly like the
original docs/QA_1000_Complex_Cases.xlsx run.

Endpoint list was discovered by introspecting the live URLconf for every
module-gated GET-list view (see qa1000_discover.py) — each (role, endpoint)
pair appears exactly once (the original run had ~165 accidental duplicate
rows; this run de-duplicates by construction).
"""
import json
import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hearth.settings.dev")
django.setup()

from django.conf import settings as _settings
if "testserver" not in _settings.ALLOWED_HOSTS:
    _settings.ALLOWED_HOSTS.append("testserver")

from rest_framework.test import APIClient

from apps.accounts.constants import (
    ROLE_ALLOW,
    ROLE_CHOICES,
    ROLE_REPORT_ACCESS,
    ROLE_TENDERS,
    role_can_tender,
    role_can_view_report,
)
from apps.accounts.models import Property, User
from apps.accounts.rbac import PROTECTED, can_access

RESULTS = []
_next_id = [1]


def rec(family, desc, expected, actual, status):
    RESULTS.append({
        "id": f"CX-{_next_id[0]:04d}",
        "family": family,
        "desc": desc,
        "expected": str(expected),
        "actual": str(actual),
        "status": status,
    })
    _next_id[0] += 1


def check(family, desc, expected, actual):
    status = "PASS" if str(expected) == str(actual) else "FAIL"
    rec(family, desc, expected, actual, status)


def check_ge(family, desc, expected_label, actual, ok):
    rec(family, desc, expected_label, actual, "PASS" if ok else "FAIL")


ALL_ROLES = [r[0] for r in ROLE_CHOICES]

USERNAME_FOR_ROLE = {
    "Super Admin": "superadmin", "Admin": "admin", "Managing Director": "md",
    "CEO": "ceo", "General Manager": "gm", "Finance": "finance",
    "Restaurant Manager": "restmanager", "Hotel Manager": "hotelmanager",
    "Front Office": "frontoffice", "F&B Cashier": "cashier", "Captain": "captain",
    "Housekeeping": "housekeeping", "Chef / Kitchen": "chef", "Store Keeper": "store",
    "Bar Captain": "barcaptain", "Bar Cashier": "barcashier", "HR Manager": "hr",
}

USER_FOR_ROLE = {}
for role, uname in USERNAME_FOR_ROLE.items():
    u = User.objects.filter(username=uname).first()
    if u is None or u.role != role:
        raise SystemExit(f"expected demo user '{uname}' with role '{role}', found {u}")
    USER_FOR_ROLE[role] = u

CLIENT_FOR_ROLE = {}
for role, u in USER_FOR_ROLE.items():
    c = APIClient()
    c.force_authenticate(u)
    CLIENT_FOR_ROLE[role] = c

anon = APIClient()

# ---------------------------------------------------------------------------
# Endpoint catalogue (path, module | None, modules-list | None, read_open)
# read_open=True means GET is open to any authenticated user regardless of
# module (apps.masters.views.MasterViewSet and its subclasses; see that
# file's own docstring) or even unauthenticated (PropertyView GET).
# ---------------------------------------------------------------------------
ENDPOINTS = [
    ("/api/banquets/", "banquets", None, False),
    ("/api/bar/tables/", "barpos", None, False),
    ("/api/booking/", "booking", None, False),
    ("/api/channel/", "channel", None, False),
    ("/api/crm/feedback/", "crm", None, False),
    ("/api/crm/loyalty-rewards/", "settings", None, True),
    ("/api/crm/loyalty-tiers/", "settings", None, True),
    ("/api/customers/", None, ["crm", "customers"], False),
    ("/api/folios/", "folio", None, False),
    ("/api/goods-receipts/", "procurement", None, False),
    ("/api/group-blocks/", "reservations", None, False),
    ("/api/gst-master/", "gstmaster", None, False),
    ("/api/housekeeping/", "housekeeping", None, False),
    ("/api/housekeeping/checklist-items/", "settings", None, True),
    ("/api/housekeeping/linen-items/", "settings", None, True),
    ("/api/housekeeping/tasks/", "housekeeping", None, False),
    ("/api/hr-advances/", "hr", None, False),
    ("/api/hr/", None, ["hr", "employees"], False),
    ("/api/inventory-categories/", "inventory", None, False),
    ("/api/inventory-uoms/", "inventory", None, False),
    ("/api/inventory/", "inventory", None, False),
    ("/api/kds/", "kds", None, False),
    ("/api/leave/", "leave", None, False),
    ("/api/masters/departments/", "settings", None, True),
    ("/api/masters/designations/", "settings", None, True),
    ("/api/masters/kitchen-stations/", "settings", None, True),
    ("/api/masters/payment-methods/", "settings", None, True),
    ("/api/material-requests/", "matreq", None, False),
    ("/api/night-audit/", "accounting", None, False),
    ("/api/pos/categories/", None, ["pos", "barpos"], False),
    ("/api/pos/menu-items/", None, ["pos", "barpos", "menumaster"], False),
    ("/api/pos/orders/", None, ["pos", "barpos"], False),
    ("/api/pos/reconciliation/", "pos", None, False),
    ("/api/pos/table-reservations/", "pos", None, False),
    ("/api/pos/tables/", "pos", None, False),
    ("/api/pos/till/", "pos", None, False),
    ("/api/purchase-orders/", None, ["procurement", "pomanage"], False),
    ("/api/rate-plans/", "roommaster", None, False),
    ("/api/recipes/", "recipes", None, False),
    ("/api/reservations/", "reservations", None, False),
    ("/api/revenue/", "revenue", None, False),
    ("/api/room-types/", "roommaster", None, False),
    ("/api/rooms/", "livegrid", None, False),
    ("/api/suppliers/", "suppliers", None, False),
    ("/api/tax/", "tax", None, False),
    ("/api/vendors/", "vendors", None, False),
    ("/api/work-orders/", "engineering", None, False),
    ("/api/approvals/", "approvals", None, False),
    ("/api/auth/branch-access/", "settings", None, False),
    ("/api/auth/branches/", "branchmaster", None, False),
    ("/api/auth/users/", "settings", None, False),
    ("/api/auth/audit/", "settings", None, False),
    ("/api/auth/property/", "settings", None, True),  # GET is AllowAny — see PropertyView.get_permissions
    ("/api/auth/roles/matrix/", "roles", None, False),
    ("/api/messages/", "notifications", None, False),
    ("/api/reports/catalogue/", "reports", None, False),
    ("/api/reports/dashboard/", "dashboard", None, False),
    ("/api/reports/dayend/", "accounting", None, False),
    ("/api/reports/executive/", "execdashboard", None, False),
    ("/api/reports/export/", "reports", None, False),
    ("/api/reports/revenue-trend/", "dashboard", None, False),
    ("/api/reports/sales-summary/", "reports", None, False),
    ("/api/reports/view/", "reports", None, False),
]

assert len({e[0] for e in ENDPOINTS}) == len(ENDPOINTS), "duplicate endpoint in catalogue"

# ---------------------------------------------------------------------------
# Family 1 — RBAC-read: every (role, endpoint) pair, exactly once.
# ---------------------------------------------------------------------------
for role in ALL_ROLES:
    client = CLIENT_FOR_ROLE[role]
    for path, module, modules, read_open in ENDPOINTS:
        if read_open:
            expected = 200
        elif modules:
            expected = 200 if any(can_access(role, m) for m in modules) else 403
        else:
            expected = 200 if can_access(role, module) else 403
        # CounterOnlyMixin (apps/pos/views.py): till/reconciliation are cash
        # controls reserved for the counter — Captain is tableside-only, even
        # though Captain otherwise has the "pos" module for orders/tables.
        if role == "Captain" and path in ("/api/pos/till/", "/api/pos/reconciliation/"):
            expected = 403
        resp = client.get(path)
        actual = resp.status_code
        # A 200-expecting case that comes back 404/500 is a real defect, not a
        # RBAC miss — only collapse 401/403 together (both mean "blocked").
        ok = (actual == expected) or (expected == 403 and actual in (401, 403))
        actual_label = actual if ok else f"{actual} {str(resp.data)[:200] if hasattr(resp, 'data') else ''}"
        rec("RBAC-read", f"{role} GET {path}",
            f"{expected} (authorised)" if expected == 200 else "403 (denied)",
            actual_label, "PASS" if ok else "FAIL")

print(f"RBAC-read done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 2 — RBAC-write: config-write denial. For each write target, every
# role that lacks the owning module must be denied (403), whatever the
# payload — proves the write path is gated at least as strictly as reads.
# ---------------------------------------------------------------------------
WRITE_TARGETS = [
    ("POST", "/api/masters/departments/", "settings", {"name": "QA Dept X"}),
    ("POST", "/api/masters/payment-methods/", "settings", {"name": "QA Tender X"}),
    ("POST", "/api/gst-master/", "gstmaster", {"name": "QA Slab X", "cgst_rate": 2.5, "sgst_rate": 2.5}),
    ("PATCH", "/api/auth/property/", "settings", {"name": "Hacked Property"}),
    ("POST", "/api/auth/roles/matrix/", "roles", {"role": "Housekeeping", "module": "accounting", "allowed": True}),
    ("POST", "/api/auth/branches/", "branchmaster", {"name": "QA Branch X", "code": "QAX"}),
    ("POST", "/api/tax/", "tax", {"name": "QA Tax X", "rate": 5}),
    ("POST", "/api/inventory-categories/", "inventory", {"name": "QA Cat X"}),
]
for method, path, module, payload in WRITE_TARGETS:
    for role in ALL_ROLES:
        if can_access(role, module):
            continue  # only denial is asserted here — legitimate writes are covered by Lifecycle/Masters
        client = CLIENT_FOR_ROLE[role]
        resp = client.patch(path, payload, format="json") if method == "PATCH" else client.post(path, payload, format="json")
        ok = resp.status_code in (401, 403)
        rec("RBAC-write", f"{role} {method} {path} denied", "403", resp.status_code if ok else f"{resp.status_code} {str(resp.data)[:150]}",
            "PASS" if ok else "FAIL")

print(f"RBAC-write done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 3 — Identity: real login (password, not force_authenticate) +
# /auth/me role claim + wrong-password rejection, per role. Throttle
# patched above so 51 rapid logins in-process don't trip the 10/min guard
# that real end users would still be subject to.
# ---------------------------------------------------------------------------
from rest_framework.throttling import ScopedRateThrottle
ScopedRateThrottle.THROTTLE_RATES = {**ScopedRateThrottle.THROTTLE_RATES, "auth": "10000/min", "sensitive": "10000/min"}

for role, uname in USERNAME_FOR_ROLE.items():
    c = APIClient()
    r = c.post("/api/auth/token/", {"username": uname, "password": "hearth123"}, format="json")
    login_ok = r.status_code == 200 and "access" in r.data
    rec("Identity", f"{role} token login", "200 + access token", r.status_code if login_ok else f"{r.status_code} {str(r.data)[:120]}",
        "PASS" if login_ok else "FAIL")

    if login_ok:
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        me = c.get("/api/auth/me/")
        me_role = me.data.get("role") if me.status_code == 200 else None
        check("Identity", f"{role} /me returns correct role", role, me_role)
    else:
        rec("Identity", f"{role} /me returns correct role", role, "N/A (login failed)", "FAIL")

    c2 = APIClient()
    bad = c2.post("/api/auth/token/", {"username": uname, "password": "wrong-password-x"}, format="json")
    ok = bad.status_code == 401
    rec("Identity", f"{role} wrong password rejected", "401", bad.status_code, "PASS" if ok else "FAIL")

anon_me = APIClient().get("/api/auth/me/")
rec("Identity", "Unauthenticated /me rejected", "401", anon_me.status_code, "PASS" if anon_me.status_code == 401 else "FAIL")

print(f"Identity done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 4 — Reports: role-scoped report access, ground truth from
# role_can_view_report() (apps/accounts/constants.py ROLE_REPORT_ACCESS).
# ---------------------------------------------------------------------------
ALL_REPORTS = ["sales", "tax", "source", "occupancy", "accounting", "guests",
               "arrivals", "night_audit", "noshow", "discounts", "aggregator",
               "recipe_consumption", "sales_vs_consumption", "purchase_vs_consumption",
               "food_cost", "item_profitability"]
REPORT_ROLES = ["Super Admin", "Managing Director", "General Manager", "CEO",
                "Finance", "Restaurant Manager", "Hotel Manager"]
for role in REPORT_ROLES:
    client = CLIENT_FOR_ROLE[role]
    for report in ALL_REPORTS:
        expected_allowed = role_can_view_report(role, report)
        resp = client.get(f"/api/reports/view/?report={report}")
        if expected_allowed:
            ok = resp.status_code == 200
        else:
            ok = resp.status_code == 403
        rec("Reports", f"{role} report:{report}", "200" if expected_allowed else "403",
            resp.status_code if ok else f"{resp.status_code} {str(resp.data)[:120]}", "PASS" if ok else "FAIL")

print(f"Reports done: {len(RESULTS)} cases so far")

gm = CLIENT_FOR_ROLE["General Manager"]
captain = CLIENT_FOR_ROLE["Captain"]
cashier = CLIENT_FOR_ROLE["F&B Cashier"]
admin = CLIENT_FOR_ROLE["Admin"]

# ---------------------------------------------------------------------------
# Family 5 — Masters: seeded data, CRUD, builtin-tender guards, in-use delete
# block, cross-role write denial. Ported from qa_rerun.py TC-009..018 (already
# proven against this build) onto the in-process client.
# ---------------------------------------------------------------------------
r = gm.get("/api/masters/departments/")
names = {d["name"] for d in r.data}
ok = r.status_code == 200 and {"Kitchen", "Housekeeping"} <= names
rec("Masters", "Department master seeded", "Kitchen & Housekeeping present", sorted(names), "PASS" if ok else "FAIL")

r = gm.post("/api/masters/departments/", {"name": f"QA Dept Y {int(time.time())}"}, format="json")
dept_id = r.data.get("id") if r.status_code == 201 else None
check("Masters", "Create department", 201, r.status_code)

r = gm.post("/api/masters/departments/", {"name": "Kitchen"}, format="json")
check("Masters", "Duplicate department name rejected", 400, r.status_code)

r = gm.patch(f"/api/masters/departments/{dept_id}/", {"active": False}, format="json")
check("Masters", "Deactivate department", False, r.data.get("active") if r.status_code == 200 else r.status_code)

kitchen_id = next(d["id"] for d in gm.get("/api/masters/departments/").data if d["name"] == "Kitchen")
r = gm.delete(f"/api/masters/departments/{kitchen_id}/")
rec("Masters", "Delete in-use department blocked", "400", r.status_code, "PASS" if r.status_code == 400 else "FAIL")

r = gm.delete(f"/api/masters/departments/{dept_id}/")
check("Masters", "Delete unused department", 204, r.status_code)

pm = gm.get("/api/masters/payment-methods/").data
builtins = [p for p in pm if p["name"] in ("Cash", "UPI", "Gateway")]
if builtins:
    bt_id = builtins[0]["id"]
    r = gm.patch(f"/api/masters/payment-methods/{bt_id}/", {"name": "Renamed"}, format="json")
    check("Masters", "Builtin tender rename blocked", 400, r.status_code)
    r = gm.delete(f"/api/masters/payment-methods/{bt_id}/")
    check("Masters", "Builtin tender delete blocked", 400, r.status_code)

r = captain.post("/api/masters/departments/", {"name": "QA Dept Captain"}, format="json")
check("Masters", "Captain cannot write masters", 403, r.status_code)

# ---------------------------------------------------------------------------
# Family 6 — Currency: round-trip across every supported currency code.
# ---------------------------------------------------------------------------
CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SAR", "LKR", "NPR", "BDT", "SGD", "MYR", "THB"]
orig_currency = gm.get("/api/auth/property/").data["currency"]
for cur in CURRENCIES:
    r = gm.patch("/api/auth/property/", {"currency": cur}, format="json")
    check("Currency", f"Set property currency {cur}", cur, r.data.get("currency") if r.status_code == 200 else r.status_code)
gm.patch("/api/auth/property/", {"currency": orig_currency}, format="json")

print(f"Masters/Currency done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 7 — Entitlement: the CX-RBAC-02 fix + edition gating behaviour.
# ---------------------------------------------------------------------------
r = CLIENT_FOR_ROLE["Housekeeping"].patch("/api/auth/entitlements/", {"banquets": False}, format="json")
check("Entitlement", "Non-settings role denied entitlement write (CX-RBAC-02 regression)", 403, r.status_code)

before_banquets = gm.get("/api/auth/property/").data["entitlement"]["banquets"]
gm.patch("/api/auth/entitlements/", {"banquets": False}, format="json")
r = gm.get("/api/banquets/")
check("Entitlement", "Banquets blocked when entitlement off (even for GM)", 403, r.status_code)
gm.patch("/api/auth/entitlements/", {"banquets": True}, format="json")
r = gm.get("/api/banquets/")
check("Entitlement", "Banquets restored when entitlement on", 200, r.status_code)
# restore original state exactly, in case it had been off for a real reason
gm.patch("/api/auth/entitlements/", {"banquets": before_banquets}, format="json")

print(f"Entitlement done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 8 — Audit: audit-trail presence + immutability.
# ---------------------------------------------------------------------------
r = gm.post("/api/masters/departments/", {"name": f"QA Spa {int(time.time())}"}, format="json")
spa_id = r.data.get("id")
ra = gm.get("/api/auth/audit/")
rows = ra.data if isinstance(ra.data, list) else ra.data.get("results", [])
found = any(f"QA Spa" in json.dumps(row.get("after", {})) for row in rows[:50])
check("Audit", "Master changes land in audit trail", True, ra.status_code == 200 and found)
if spa_id:
    gm.delete(f"/api/masters/departments/{spa_id}/")

r = CLIENT_FOR_ROLE["Captain"].get("/api/auth/audit/")
check("Audit", "Audit hidden from floor roles", 403, r.status_code)

r = gm.delete("/api/auth/audit/1/")
ok = r.status_code in (404, 405)
rec("Audit", "Audit entries cannot be deleted via API", "404/405", r.status_code, "PASS" if ok else "FAIL")

r = gm.post("/api/auth/entitlements/", {"banquets": True}, format="json")
ok = r.status_code == 200  # entitlement_update logs an audit row (see EntitlementView.patch)
ra2 = gm.get("/api/auth/audit/")
rows2 = ra2.data if isinstance(ra2.data, list) else ra2.data.get("results", [])
logged = any(row.get("action") == "entitlement_update" for row in rows2[:20])
check("Audit", "Entitlement changes are audited", True, logged)

print(f"Audit done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 9 — Tender: ROLE_TENDERS ground truth, role x tender allow/deny via
# the live POS discount-cap-free settle path (dine-in table, single item).
# ---------------------------------------------------------------------------
from apps.accounts.constants import ROLE_TENDERS
from apps.masters.models import PaymentMethod
from apps.pos.models import BarTable, MenuItem, Table

table = Table.objects.first()
bar_table = BarTable.objects.first()
menu_item = MenuItem.objects.filter(available=True).first()
tenders_to_check = ["Cash", "UPI", "Gateway"]
TENDER_ROLES = ["F&B Cashier", "Captain", "Bar Captain", "Bar Cashier", "Restaurant Manager"]
BAR_ROLES = {"Bar Captain", "Bar Cashier"}
for role in TENDER_ROLES:
    client = CLIENT_FOR_ROLE[role]
    for tender in tenders_to_check:
        allow = ROLE_TENDERS.get(role)
        pm_row = PaymentMethod.objects.filter(name=tender).first()
        if allow == "*":
            expected_ok = True
        elif allow is None:
            expected_ok = False
        else:
            expected_ok = pm_row.captain_allowed if pm_row else (tender in allow)
        if role in BAR_ROLES:
            payload = {"mode": "dinein", "bar_table": bar_table.id}
        else:
            payload = {"mode": "dinein", "table": table.id}
        o = client.post("/api/pos/orders/", payload, format="json")
        if o.status_code != 201:
            rec("Tender", f"{role} settle via {tender}", "order open", f"could not open order: {o.status_code} {str(o.data)[:100]}", "FAIL")
            continue
        oid = o.data["id"]
        client.post(f"/api/pos/orders/{oid}/add_item/", {"menu_item": menu_item.id, "qty": 1}, format="json")
        client.post(f"/api/pos/orders/{oid}/fire_kot/", format="json")
        settle_payload = {"tender": tender}
        if tender == "Gateway":
            settle_payload["token"] = "qa-mock-token"
        s = client.post(f"/api/pos/orders/{oid}/settle/", settle_payload, format="json")
        ok = (s.status_code == 200) if expected_ok else (s.status_code == 403)
        rec("Tender", f"{role} settle via {tender}", "200 (allowed)" if expected_ok else "403 (denied)",
            s.status_code if ok else f"{s.status_code} {str(s.data)[:100]}", "PASS" if ok else "FAIL")

print(f"Tender done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 10 — Numbering + State + Money + KYC: a real reservation -> KYC
# check-in -> checkout flow (ported from qa_rerun.py TC-028..045, proven
# against this build), used as the backbone for invoice numbering, illegal
# state transitions, and folio/KYC evidence checks.
# ---------------------------------------------------------------------------
import base64

TINY_PNG = "data:image/png;base64," + base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
                  "de0000000a49444154789c6360000002000155040d0a0000000049454e44ae426082")
).decode()

frontoffice = CLIENT_FOR_ROLE["Front Office"]

room_type_opts = gm.get("/api/reservations/room_types/").data
best_rt = max(room_type_opts, key=lambda o: o.get("available", 0))
rts = gm.get("/api/room-types/").data
room_type_id = next(rt["id"] for rt in rts if rt["code"] == best_rt["code"])
rate_plans = gm.get("/api/rate-plans/").data
rate_plan_id = next((rp["id"] for rp in rate_plans if rp.get("room_type_code") == best_rt["code"]),
                     rate_plans[0]["id"] if rate_plans else None)

r = gm.post("/api/reservations/", {
    "guest_name": "QA Suite Guest", "room_type": room_type_id, "rate_plan": rate_plan_id,
    "checkin_date": time.strftime("%Y-%m-%d"),
    "checkout_date": time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400)),
    "source": "direct", "rate": "3000",
}, format="json")
resv_id = r.data.get("id") if r.status_code == 201 else None
check("Numbering/State", "Create reservation for QA1000 flow", 201, r.status_code)

if resv_id:
    r = frontoffice.post("/api/checkin/", {"reservation": resv_id, "guest_type": "individual", "mobile": "9000000031"}, format="json")
    check("Validation", "Check-in without ID blocked", 400, r.status_code)

    r = frontoffice.post("/api/checkin/", {"reservation": resv_id, "guest_type": "individual",
                                            "id_type": "Aadhaar", "id_number": "111122223333"}, format="json")
    check("Validation", "Check-in without mobile blocked", 400, r.status_code)

    rooms = gm.get(f"/api/reservations/{resv_id}/room_options/").data
    rec("State", "Room options available for arrival", ">=1 sellable room", f"{len(rooms)} rooms",
        "PASS" if len(rooms) >= 1 else "FAIL")

    r = frontoffice.post("/api/checkin/", {
        "reservation": resv_id, "room": rooms[0]["id"], "guest_type": "individual",
        "id_type": "Aadhaar", "id_number": "111122223333", "mobile": "9000000031",
        "id_scan": "not-an-image", "signature": TINY_PNG,
    }, format="json")
    check("Validation", "Non-image ID scan rejected", 400, r.status_code)

    r = frontoffice.post("/api/checkin/", {
        "reservation": resv_id, "room": rooms[0]["id"], "guest_type": "individual",
        "id_type": "Aadhaar", "id_number": "111122223333", "mobile": "9000000031",
        "id_scan": TINY_PNG, "signature": TINY_PNG,
    }, format="json")
    folio_id = r.data.get("id") if r.status_code == 201 else None
    check("Lifecycle", "Check-in with KYC + scan + signature opens folio", 201, r.status_code)

    if folio_id:
        r = gm.get(f"/api/folios/{folio_id}/")
        d = r.data
        has_flags = bool(d.get("has_id_scan")) and bool(d.get("has_signature"))
        blob_excluded = "id_scan" not in d and "signature" not in d
        ok = r.status_code == 200 and has_flags and blob_excluded
        rec("KYC", "Folio exposes id/signature as flags, never blobs", "flags true, blob absent",
            f"flags={has_flags} blob_excluded={blob_excluded}", "PASS" if ok else "FAIL")

        r = gm.get(f"/api/folios/{folio_id}/registration/")
        ra = gm.get("/api/auth/audit/")
        rows = ra.data if isinstance(ra.data, list) else ra.data.get("results", [])
        # entity_id is stored as a string on AuditLog (see apps/accounts/models.py) —
        # compare as strings, not against the serializer's int id.
        logged = any(row.get("entity") == "Folio" and str(row.get("entity_id")) == str(folio_id)
                     and row.get("action") == "registration_viewed" for row in rows[:20])
        check("KYC", "Registration-evidence view is itself audited", True, r.status_code == 200 and logged)

        r = frontoffice.post("/api/checkin/", {
            "reservation": resv_id, "room": rooms[0]["id"], "guest_type": "individual",
            "id_type": "Aadhaar", "id_number": "111122223333", "mobile": "9000000031",
            "id_scan": TINY_PNG, "signature": TINY_PNG,
        }, format="json")
        check("State", "Double check-in on same reservation blocked", 400, r.status_code)

        r = frontoffice.post(f"/api/folios/{folio_id}/checkout/", {"tender": "UPI"}, format="json")
        invoice_no = r.data.get("invoice_no") if r.status_code == 200 else None
        check("Lifecycle", "Checkout settles and issues GST invoice", True, r.status_code == 200 and bool(invoice_no))

        import re as _re
        ok = bool(invoice_no) and bool(_re.match(r"^[A-Z]+-\d{6}-\d{5}$", invoice_no))
        rec("Numbering", "Invoice number format PREFIX-YYYYMM-NNNNN", "pattern match", invoice_no, "PASS" if ok else "FAIL")

        # check_out() (apps/frontoffice/services.py:232) is idempotent on an
        # already-settled folio: balance is already 0, so it's a no-op that
        # re-returns the same invoice/settlement rather than erroring or
        # double-charging. Assert that safety property, not a 4xx.
        r2 = frontoffice.post(f"/api/folios/{folio_id}/checkout/", {"tender": "UPI"}, format="json")
        no_dup_charge = (r2.status_code == 200 and r2.data.get("invoice_no") == invoice_no
                          and len(r2.data.get("settlements", [])) == 1)
        rec("State", "Re-checkout on an already-settled folio is a safe no-op (no double charge)",
            "200, same invoice, 1 settlement", f"{r2.status_code}, invoice={r2.data.get('invoice_no')}, "
            f"settlements={len(r2.data.get('settlements', []))}", "PASS" if no_dup_charge else "FAIL")

        room_after = gm.get(f"/api/rooms/{rooms[0]['id']}/").data
        check("Lifecycle", "Room released to housekeeping cycle after checkout", "vacant_dirty", room_after.get("status"))

print(f"Numbering/State/KYC done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 11 — Lifecycle + Numbering + State: HR (employee/leave), Banquets
# (BEO numbering), Housekeeping/Engineering (work-order), MatReq + Procurement
# (PO/GRN numbering + approval-chain segregation of duties). Ported from
# qa_rerun.py TC-081..099 (proven against this build).
# ---------------------------------------------------------------------------
hr = CLIENT_FOR_ROLE["HR Manager"]
chef = CLIENT_FOR_ROLE["Chef / Kitchen"]
store = CLIENT_FOR_ROLE["Store Keeper"]
finance = CLIENT_FOR_ROLE["Finance"]
restmgr = CLIENT_FOR_ROLE["Restaurant Manager"]
housekeeping = CLIENT_FOR_ROLE["Housekeeping"]

# --- HR: employee lifecycle ---
depts = hr.get("/api/masters/departments/").data
desigs = hr.get("/api/masters/designations/").data
dept_name = next((d["name"] for d in depts if d.get("active", True)), "Kitchen")
desig_name = next((d["name"] for d in desigs if d.get("active", True)), None)

r = hr.post("/api/hr/", {"name": "QA Employee Astronaut", "department": dept_name, "role": "Astronaut"}, format="json")
check("Validation", "Employee create validates designation master", 400, r.status_code)

r = hr.post("/api/hr/", {"name": "QA Employee Two", "department": dept_name, "role": desig_name}, format="json")
emp_id = r.data.get("id") if r.status_code == 201 else None
check("Lifecycle", "Create employee (masters-validated)", 201, r.status_code)
if emp_id:
    r1 = hr.post(f"/api/hr/{emp_id}/set_status/", {"status": "Inactive"}, format="json")
    r2 = hr.post(f"/api/hr/{emp_id}/set_status/", {"status": "Active"}, format="json")
    ok = r1.status_code == 200 and r2.status_code == 200
    rec("Lifecycle", "Employee status toggle (Inactive then Active)", "200 then 200",
        f"{r1.status_code}/{r2.status_code}", "PASS" if ok else "FAIL")

# --- HR: leave lifecycle (two-level approval; GM is universal at both levels) ---
r = hr.post("/api/leave/save_type/", {"name": f"QA Leave Type {int(time.time())}", "annual_quota": 12}, format="json")
leave_type_id = r.data.get("id")
r2 = hr.get("/api/leave/types/")
check("Lifecycle", "Leave type configurable", True, r.status_code == 200 and r2.status_code == 200)

if emp_id and leave_type_id:
    today = time.strftime("%Y-%m-%d")
    r = hr.post("/api/leave/", {"employee": emp_id, "leave_type": leave_type_id,
                                 "start_date": today, "end_date": today, "reason": "QA"}, format="json")
    lr_id = r.data.get("id")
    r1 = gm.post(f"/api/leave/{lr_id}/decide/", {"decision": "approve"}, format="json") if lr_id else None
    r2 = gm.post(f"/api/leave/{lr_id}/decide/", {"decision": "approve"}, format="json") if lr_id else None
    ok = (r.status_code == 201 and r2 is not None and r2.status_code == 200 and r2.data.get("status") == "approved")
    rec("Lifecycle", "Leave request + two-level manager approval", "created then approved",
        f"create={r.status_code} final={r2.data.get('status') if r2 is not None else None}", "PASS" if ok else "FAIL")

# --- Banquets: BEO numbering ---
event_date = time.strftime("%Y-%m-%d", time.localtime(time.time() + 7 * 86400))
spaces = gm.get(f"/api/banquets/availability/?date={event_date}").data
space = next((s for s in spaces if s.get("available")), spaces[0] if spaces else None)
if space:
    r = gm.post("/api/banquets/", {"space": space["id"], "title": "QA Wedding", "host": "QA Host",
                                    "contact": "9000000096", "event_date": event_date,
                                    "start_time": "18:00", "end_time": "23:00", "covers": 100}, format="json")
    event_id = r.data.get("id") if r.status_code == 201 else None
    check("Lifecycle", "Book function-space event", 201, r.status_code)
    if event_id:
        r = gm.post(f"/api/banquets/{event_id}/confirm/", format="json")
        beo_no = r.data.get("beo_no") if r.status_code == 200 else None
        r2 = gm.post(f"/api/banquets/{event_id}/bill/", format="json") if beo_no else None
        ok = bool(beo_no) and r2 is not None and r2.status_code == 200
        rec("Lifecycle", "Confirm + bill event", "confirmed then billed",
            f"beo={beo_no} billed={r2.status_code if r2 else None}", "PASS" if ok else "FAIL")
        import re as _re2
        ok2 = bool(beo_no) and bool(_re2.match(r"^[A-Z]+-\d{6}-\d{5}$", beo_no))
        rec("Numbering", "BEO number format PREFIX-YYYYMM-NNNNN", "pattern match", beo_no, "PASS" if ok2 else "FAIL")

# --- Housekeeping / Engineering: room-cycle + work-order lifecycle ---
rooms_hk = gm.get("/api/housekeeping/").data
transitionable = {"vacant_dirty", "cleaning", "vacant_clean"}
room_hk = next((rm for rm in rooms_hk if rm.get("status") in transitionable), None)
if room_hk:
    r = gm.patch(f"/api/housekeeping/{room_hk['id']}/advance/", format="json")
    check("Lifecycle", "Room cleaning cycle advances one step", 200, r.status_code)

if rooms_hk:
    r = housekeeping.post("/api/work-orders/", {"room": rooms_hk[0]["id"], "title": "QA AC not cooling", "detail": "test"}, format="json")
    wo_id = r.data.get("id") if r.status_code == 201 else None
    r2 = gm.patch(f"/api/work-orders/{wo_id}/advance/", format="json") if wo_id else None
    ok = r.status_code == 201 and r2 is not None and r2.status_code == 200
    rec("Lifecycle", "Maintenance work-order lifecycle (create then advance)", "201 then 200",
        f"{r.status_code}/{r2.status_code if r2 else None}", "PASS" if ok else "FAIL")

# --- MatReq: segregation of duties (requester != approver != issuer) ---
mats = store.get("/api/inventory/").data
if mats:
    r = chef.post("/api/material-requests/", {"department": "Kitchen", "lines": [{"ingredient": mats[0]["id"], "qty": 1}]}, format="json")
    indent_id = r.data.get("id") if r.status_code == 201 else None
    check("Lifecycle", "Chef raises Kitchen material-request indent", 201, r.status_code)
    if indent_id:
        r = chef.post(f"/api/material-requests/{indent_id}/advance/", format="json")
        check("State", "Requester cannot approve own indent (self-approve blocked)", 403, r.status_code)
        r = store.post(f"/api/material-requests/{indent_id}/advance/", format="json")
        check("State", "Store Keeper cannot approve indents (issues only)", 403, r.status_code)
        r = restmgr.post(f"/api/material-requests/{indent_id}/advance/", format="json")
        check("Lifecycle", "Restaurant Manager approves Kitchen indent", "approved", r.data.get("status") if r.status_code == 200 else r.status_code)

# --- Procurement: PO/GRN numbering + approve-before-receive state guard ---
suppliers = store.get("/api/suppliers/").data
supplier = suppliers[0] if suppliers else None
if not supplier:
    r = store.post("/api/suppliers/", {"name": f"QA Fresh Farms {int(time.time())}", "contact": "9000000090"}, format="json")
    supplier = r.data if r.status_code == 201 else None

if supplier and mats:
    po_numbers = []
    po_ids = []
    for _ in range(2):
        r = store.post("/api/purchase-orders/", {"supplier": supplier["id"],
                        "lines": [{"ingredient": mats[0]["id"], "qty": 10, "rate": 20}]}, format="json")
        if r.status_code == 201:
            po_numbers.append(r.data.get("po_no"))
            po_ids.append(r.data.get("id"))
    ok = len(po_numbers) == 2 and all(po_numbers) and po_numbers[1] != po_numbers[0]
    rec("Numbering", "Consecutive POs get unique document numbers", "unique, sequential", po_numbers, "PASS" if ok else "FAIL")

    if po_ids:
        r = store.post(f"/api/purchase-orders/{po_ids[0]}/receive/", format="json")
        check("State", "Receiving a PO before finance approval is blocked", True, r.status_code >= 400)

        r = finance.post(f"/api/purchase-orders/{po_ids[0]}/approve/", format="json")
        check("Lifecycle", "Finance approves PO", 200, r.status_code)

        mats_before = {m["id"]: float(m["current_stock"]) for m in store.get("/api/inventory/").data}
        r = store.post(f"/api/purchase-orders/{po_ids[0]}/receive/", format="json")
        grns = store.get("/api/goods-receipts/").data
        grn_no = grns[0]["grn_no"] if grns else None
        check("Lifecycle", "GRN receipt adds stock + issues GRN number", True, r.status_code == 200 and bool(grn_no))

r = store.post("/api/suppliers/", {"name": supplier["name"] if supplier else "QA Fresh Farms", "contact": "9000000090"}, format="json")
check("Validation", "Duplicate supplier name rejected", 400, r.status_code)

print(f"Lifecycle/Numbering/State (HR/Banquets/HK/MatReq/Procurement) done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 12 — Money: GST line-total consistency + discount cap enforcement +
# void-after-KOT authorization guard.
# ---------------------------------------------------------------------------
o = cashier.post("/api/pos/orders/", {"mode": "takeaway"}, format="json")
oid = o.data["id"]
cashier.post(f"/api/pos/orders/{oid}/add_item/", {"menu_item": menu_item.id, "qty": 1}, format="json")
order_detail = cashier.get(f"/api/pos/orders/{oid}/").data
line = order_detail["lines"][0]
taxable, cgst, sgst, total = (float(line.get(k, 0)) for k in ("taxable", "cgst", "sgst", "total"))
ok = abs((taxable + cgst + sgst) - total) < 0.01
rec("Money", "F&B line total = taxable + CGST + SGST", "consistent", f"{taxable}+{cgst}+{sgst}={taxable+cgst+sgst} vs total={total}",
    "PASS" if ok else "FAIL")

o2 = cashier.post("/api/pos/orders/", {"mode": "takeaway"}, format="json")
oid2 = o2.data["id"]
cashier.post(f"/api/pos/orders/{oid2}/add_item/", {"menu_item": menu_item.id, "qty": 1}, format="json")
r = cashier.post(f"/api/pos/orders/{oid2}/apply_discount/", {"kind": "percent", "value": 50, "reason": "QA test"}, format="json")
check("Money", "Discount above cashier's cap blocked", 403, r.status_code)

o3 = cashier.post("/api/pos/orders/", {"mode": "dinein", "table": table.id}, format="json")
oid3 = o3.data["id"]
cashier.post(f"/api/pos/orders/{oid3}/add_item/", {"menu_item": menu_item.id, "qty": 1}, format="json")
cashier.post(f"/api/pos/orders/{oid3}/fire_kot/", format="json")
o3f = cashier.get(f"/api/pos/orders/{oid3}/").data
line3 = o3f["lines"][0].get("line", o3f["lines"][0].get("id"))
r = cashier.post(f"/api/pos/orders/{oid3}/set_qty/", {"line": line3, "qty": 0}, format="json")
check("Money", "Void after KOT needs reason/authorisation override", 403, r.status_code)

print(f"Money done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 13 — DPDP: guest data export + right-to-erasure anonymisation.
# ---------------------------------------------------------------------------
r = gm.post("/api/customers/", {"name": "QA DPDP Customer", "mobile": f"90000{int(time.time()) % 100000}"}, format="json")
cust_id = r.data.get("id") if r.status_code == 201 else None
check("DPDP", "Create guest record for export/erase check", 201, r.status_code)

if cust_id:
    r = CLIENT_FOR_ROLE["Housekeeping"].get(f"/api/customers/{cust_id}/export/")
    check("DPDP", "Guest-data export blocked for a role without crm/customers", 403, r.status_code)

    rex = gm.get(f"/api/customers/{cust_id}/export/")
    check("DPDP", "Guest-data export", 200, rex.status_code)

    rer = gm.post(f"/api/customers/{cust_id}/erase/", format="json")
    anonymised = rer.status_code == 200 and "Erased" in (rer.data.get("customer", {}).get("name") or "")
    check("DPDP", "Right-to-erasure anonymises guest record", True, anonymised)

print(f"DPDP done: {len(RESULTS)} cases so far")

# ---------------------------------------------------------------------------
# Family 14 — extra Validation: input-rejection negatives not already covered
# above (weak password on admin user-create, unknown UoM, blank master name).
# ---------------------------------------------------------------------------
r = admin.post("/api/auth/users/", {"username": "qa_weakpw_1000", "password": "123",
                                     "first_name": "Qa", "last_name": "Weak", "role": "F&B Cashier"}, format="json")
check("Validation", "Weak password rejected on user create", 400, r.status_code)

r = gm.post("/api/masters/departments/", {"name": ""}, format="json")
check("Validation", "Blank department name rejected", 400, r.status_code)

r = store.post("/api/inventory/", {"name": "QA Bad Unit Item", "unit": "furlongs", "current_stock": "0"}, format="json")
check("Validation", "Unknown unit-of-measure rejected", 400, r.status_code)

r = admin.post("/api/auth/users/", {"username": "gm", "password": "AnotherValid987!",
                                     "first_name": "Dup", "last_name": "User", "role": "Admin"}, format="json")
check("Validation", "Duplicate username rejected", 400, r.status_code)

print(f"Validation done: {len(RESULTS)} cases so far")

n_total = len(RESULTS)
n_pass = sum(1 for r in RESULTS if r["status"] == "PASS")
n_fail = n_total - n_pass
json.dump(RESULTS, open("qa1000_results.json", "w"), indent=2)
print(f"\n=== ALL DONE: {n_total} cases, {n_pass} pass, {n_fail} fail ===")
if n_fail:
    print("--- FAILURES ---")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"{r['id']}\t{r['family']}\t{r['desc']}\texpected={r['expected']}\tactual={r['actual']}")
