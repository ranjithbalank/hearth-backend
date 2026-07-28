"""Generate the full feature-configuration scenario catalog ("the 1000 loopholes").

Expands the real RBAC allow-lists + the feature dependency model into every
"WHEN <condition> THEN <behaviour>" rule, then writes a self-contained, searchable
HTML page (body content only — ready to publish as an Artifact).

Run:  backend/.venv/Scripts/python.exe backend/scripts/gen_scenarios.py
Out:  <scratchpad>/scenario_catalog.html  (path printed at the end)
"""
import html
import json
import os
import sys

import django

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hearth.settings.dev")
django.setup()

from apps.accounts import features as F  # noqa: E402
from apps.accounts.constants import (  # noqa: E402
    ALL_MODULES, ROLE_ALLOW, MODULE_ENTITLEMENT, edition_entitlements)

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "/tmp"), "scenario_catalog.html")

MODULE_LABEL = {k: v["label"] for k, v in F.FEATURES.items()}
for m in ALL_MODULES:
    MODULE_LABEL.setdefault(m, m.replace("_", " ").title())

# Named graceful-degradation behaviours (the Category-4 fallbacks), grounded in code.
DEGRADE = {
    "livegrid_full_states": ("Live Grid collapses the 6 room states to Occupied / Vacant / Out-of-order, with a one-tap “Mark ready”", "COLLAPSE", "frontend/src/features/livegrid/LiveGrid.tsx"),
    "checkout_marks_dirty": ("Check-out releases the room as Vacant-Clean (stays sellable) instead of Vacant-Dirty — it never waits on a cleaner", "REROUTE", "backend/apps/frontoffice/services.py:260"),
    "roommove_marks_dirty": ("A room move leaves the old room Vacant-Clean instead of Dirty", "REROUTE", "backend/apps/reservations/views.py:194"),
    "frontdesk_request_clean": ("Front Desk hides the “Request cleaning” button", "HIDE", "frontend/src/features/frontdesk/FrontDesk.tsx:87"),
    "housekeeping_alerts": ("Cleaning / dirty-room alerts are suppressed from the bell and Notifications list", "SUPPRESS", "backend/apps/notifications/views.py:39"),
    "minibar_to_folio": ("Minibar-to-folio posting moves to Front Desk room-service (or is disabled with a notice)", "RELOCATE", "backend/apps/housekeeping/views.py:97"),
    "lost_and_found": ("Lost & found relocates to Front Desk (or is disabled)", "RELOCATE", "backend/apps/housekeeping/views.py:120"),
    "ooo_workflow": ("Out-of-order rooms are set directly on Live Grid; there is no work-order queue", "COLLAPSE", "backend/apps/housekeeping/views.py:327"),
    "kot_stock_deduction": ("POS still fires KOTs, but no stock is deducted (the recipe→inventory link is gone)", "AUTO-COMPLETE", "backend/apps/recipes/services.py:28"),
    "beo_to_kds": ("Banquet BEO prep falls back to the printable BEO sheet only (no kitchen-display ticket)", "REROUTE", "backend/apps/pos/views.py:613"),
    "aggregator_intake": ("Zomato / Swiggy order intake is disabled; dine-in and takeaway are unaffected", "HIDE", "backend/apps/pos"),
    "qr_ordering": ("The guest QR self-ordering page is disabled", "HIDE", "frontend/src/features/public/GuestPages.tsx"),
    "loyalty_accrual": ("Loyalty points stop accruing; existing balances are frozen, not lost", "AUTO-COMPLETE", "backend/apps/crm"),
    "feedback_qr": ("Feedback QR + NPS capture is disabled", "HIDE", "backend/apps/crm"),
}

# Behavioural (non-gating) toggles — each has distinct ON vs OFF behaviour.
BEHAVIOURAL = [
    ("Bar mode = Separate", "Bar POS / Bar Table / Bar Menu shown; Bar Captain & Bar Cashier operate their own tabs", "app-context.tsx:112"),
    ("Bar mode = Combined", "Bar screens hidden; drinks are rung up on the one restaurant POS", "app-context.tsx:112"),
    ("KDS partial-ready = ON", "Kitchen marks individual items ready; the ticket auto-advances when all lines are ready", "pos/views.py:661"),
    ("KDS partial-ready = OFF", "Whole ticket bumps to ready together (course served together)", "pos/views.py:661"),
    ("GST billing = With GST", "Full tax invoice: CGST + SGST lines on every bill", "Property.gst_billing_mode"),
    ("GST billing = Without GST", "Bill of supply on room/incidental lines; F&B always keeps GST", "Property.gst_billing_mode"),
    ("Offline POS = ON", "Bills queue locally and sync idempotently when the connection returns", "lib/offline.ts"),
    ("Offline POS = OFF", "Billing requires a live connection", "lib/offline.ts"),
    ("Aggregators = ON", "Zomato / Swiggy webhook + payout reconciliation active; commission % nets realisation", "Property.zomato/swiggy_commission_pct"),
    ("Aggregators = OFF", "Aggregator report + intake hidden", "reports/views.py"),
    ("Multi-branch = ON", "Branch switcher shown; every list scopes to the active X-Branch-Id", "permissions.py:130"),
    ("Multi-branch = OFF", "Single-property mode; switcher hidden; no branch scoping", "app-context.tsx BranchSwitcher"),
    ("MFA (TOTP) = ON", "Login requires the 6-digit authenticator code after the password", "accounts MFA"),
    ("MFA (TOTP) = OFF", "Password-only login", "accounts MFA"),
    ("Token board = ON", "Takeaway / delivery pickup tokens shown on the public order-status board", "pos/TokenBoard.tsx"),
    ("Token board = OFF", "No pickup-token surface", "pos/TokenBoard.tsx"),
    ("Room service = ON", "Front Desk can order F&B to a room; the bill posts to the folio (needs Restaurant + Folio)", "frontoffice/views.py:191"),
    ("Room service = OFF", "Front Desk shows no “Order food” action", "frontoffice/views.py:191"),
    ("DPDP export/erase = ON", "A guest can request their data export or erasure (privacy compliance)", "DPDP"),
    ("Minibar-to-folio = ON", "Housekeeping posts minibar consumption straight to the guest folio", "housekeeping/views.py:97"),
    ("Happy hour = ON", "Time-boxed menu pricing applies automatically during the window", "menu pricing"),
    ("Discounts & coupons = ON", "Line/bill discounts + coupon codes, capped per user with manager override", "pos discounts"),
    ("Kitchen stations = ON", "A KOT splits into per-station tickets; each station has its own KDS / print", "KitchenStation"),
    ("E-invoice (IRP) = ON", "B2B invoices above threshold push to the IRP for an IRN + signed QR", "e-invoice (external)"),
    ("SMS / WhatsApp receipts = ON", "The guest gets a bill/booking message on settle or confirm", "messaging (external)"),
    ("Pre-check-in = ON", "Guests submit ID + ETA online; the desk check-in is pre-filled", "GuestPages pre-checkin"),
    ("Group blocks = ON", "Reservations can hold a block of rooms under one group booking", "reservations groups"),
    ("Table reservations/waitlist = ON", "POS holds tables for a time and queues walk-ins; seating frees the table", "pos reservations"),
    ("Table move/merge/split = ON", "A running bill can move tables, merge parties, or split a check", "pos table ops"),
    ("Void with reason = ON", "Removing a fired KOT line requires a reason and is audit-logged", "pos void"),
]

rows = []           # each: dict(id, cat, cond, result, type, module, role)
_id = 0


def add(cat, cond, result, rtype, module="", role=""):
    global _id
    _id += 1
    rows.append({"id": _id, "cat": cat, "cond": cond, "result": result,
                 "type": rtype, "module": module, "role": role})


# --- Category 1: RBAC role x module (who can open what) ---
for role, allow in ROLE_ALLOW.items():
    for m in ALL_MODULES:
        can = allow == "*" or m in (allow or [])
        add("RBAC role × module",
            f"Role “{role}” opens {MODULE_LABEL[m]}",
            "Visible & usable" if can else "Hidden — nav filtered, direct URL redirects, API returns 403",
            "VISIBLE" if can else "HIDDEN", module=m, role=role)

# --- Category 2: feature on/off gating (per toggleable feature x 5 consequences) ---
GATING = [
    ("its nav item is removed from the sidebar", "HIDE"),
    ("a direct URL to it redirects to the role's landing screen", "REDIRECT"),
    ("its API endpoints return 403", "BLOCK"),
    ("its alerts are removed from the bell + Notifications", "SUPPRESS"),
    ("its reports drop out of the Reports catalogue + exports", "SUPPRESS"),
]
for key, spec in F.FEATURES.items():
    if not spec["toggleable"]:
        add("Feature on/off", f"{spec['label']} is a core feature",
            "Always on — cannot be turned off (the app needs it)", "CORE", module=key)
        continue
    for text, t in GATING:
        add("Feature on/off", f"{spec['label']} = OFF",
            f"Then {text}", t, module=key)

# --- Category 3: dependency cascades ---
for key, spec in F.FEATURES.items():
    for req in spec["requires"]:
        add("Dependency cascade",
            f"{MODULE_LABEL.get(req, req)} = OFF (a prerequisite of {spec['label']})",
            f"{spec['label']} also turns OFF automatically — it cannot run without it",
            "CASCADE", module=key)
        add("Dependency cascade",
            f"You enable {spec['label']} while {MODULE_LABEL.get(req, req)} is OFF",
            f"{MODULE_LABEL.get(req, req)} is switched back ON with it (or the toggle is blocked with a reason)",
            "PREREQ", module=key)
    deps = sorted(F.dependents(key))
    if deps:
        add("Dependency cascade", f"{spec['label']} = OFF",
            "These also cascade OFF: " + ", ".join(MODULE_LABEL.get(d, d) for d in deps),
            "CASCADE", module=key)

# --- Category 4: graceful-degradation loopholes ---
for key, spec in F.FEATURES.items():
    for deg in spec["degrades"]:
        behaviour, t, seam = DEGRADE.get(deg, (deg, "REROUTE", ""))
        add("Graceful degradation", f"{spec['label']} = OFF",
            f"{behaviour}   — [{seam}]", t, module=key)

# --- Category 5: behavioural toggles ---
for name, behaviour, seam in BEHAVIOURAL:
    add("Behavioural toggle", name, f"{behaviour}   — [{seam}]", "BEHAVIOUR")

# --- Category 6: edition presets (what each licensed edition makes available) ---
EDITION_LABEL = {"hotel": "Hotel edition", "restaurant": "Restaurant edition", "both": "Both edition"}
for edition in ("hotel", "restaurant", "both"):
    res = F.resolve(edition_entitlements(edition), {})
    for key, spec in F.FEATURES.items():
        on = res.get(key, True)
        add("Edition preset",
            f"{EDITION_LABEL[edition]} — {spec['label']}",
            "Available" if on else "Not licensed in this edition (screen + API off)",
            "VISIBLE" if on else "HIDDEN", module=key)

# ---------------------------------------------------------------------------
cats = {}
for r in rows:
    cats[r["cat"]] = cats.get(r["cat"], 0) + 1

TYPE_COLOR = {
    "VISIBLE": "#16A34A", "HIDDEN": "#DC2626", "HIDE": "#DC2626", "BLOCK": "#DC2626",
    "REDIRECT": "#B45309", "SUPPRESS": "#B45309", "CASCADE": "#7C3AED", "PREREQ": "#2563EB",
    "REROUTE": "#0284C7", "COLLAPSE": "#0284C7", "RELOCATE": "#0284C7",
    "AUTO-COMPLETE": "#0891B2", "CORE": "#64748B", "BEHAVIOUR": "#C9932E",
}

data_json = json.dumps(rows, ensure_ascii=False)
cat_list = list(cats.keys())
role_list = sorted({r["role"] for r in rows if r["role"]})
module_list = sorted({MODULE_LABEL.get(r["module"], r["module"]) for r in rows if r["module"]})

summary_cards = "".join(
    f'<div class="stat"><div class="statn">{n}</div><div class="statl">{html.escape(c)}</div></div>'
    for c, n in cats.items()
)

page = f"""<title>Hearth — Feature Scenario Catalog</title>
<style>
  :root {{ --bg:#f5f7fa; --card:#fff; --ink:#0f1e33; --muted:#64748b; --line:#e2e8f0; --pine:#2563eb; --th:#f8fafc; --hover:#f8fafc; --seam:#94a3b8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:Inter,system-ui,sans-serif; color:var(--ink); background:var(--bg); }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 80px; }}
  h1 {{ font-family:'Newsreader',Georgia,serif; font-size:30px; margin:0 0 4px; letter-spacing:-.02em; }}
  .sub {{ color:var(--muted); font-size:14px; margin-bottom:20px; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:22px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; min-width:120px; }}
  .statn {{ font-family:'Newsreader',Georgia,serif; font-size:26px; font-weight:600; }}
  .statl {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin-top:2px; }}
  .total {{ background:linear-gradient(135deg,#3b82f6,#1d4ed8); color:#fff; border:none; }}
  .total .statl {{ color:rgba(255,255,255,.7); }}
  .controls {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px; position:sticky; top:0; background:var(--bg); padding:10px 0; z-index:2; }}
  select, input {{ font:inherit; font-size:14px; padding:8px 12px; border:1px solid var(--line); border-radius:10px; background:var(--card); color:var(--ink); }}
  input {{ flex:1; min-width:200px; }}
  .count {{ align-self:center; color:var(--muted); font-size:13px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); background:var(--th); padding:10px 12px; position:sticky; top:64px; }}
  td {{ padding:10px 12px; border-top:1px solid var(--line); font-size:13.5px; vertical-align:top; }}
  tr:hover td {{ background:var(--hover); }}
  .pill {{ display:inline-block; font-size:10.5px; font-weight:700; padding:2px 8px; border-radius:999px; color:#fff; white-space:nowrap; }}
  .idc {{ color:var(--seam); font-variant-numeric:tabular-nums; font-size:12px; width:44px; }}
  .cat {{ color:var(--muted); font-size:11px; white-space:nowrap; }}
  .seam {{ color:var(--seam); }}
  :focus-visible {{ outline:2px solid var(--pine); outline-offset:2px; border-radius:6px; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme]) {{ --bg:#0a1526; --card:#13233a; --ink:#e4e9f0; --muted:#96a5bd; --line:#22314b; --th:#16233b; --hover:#16233b; --seam:#5b6b85; }} }}
  :root[data-theme="dark"] {{ --bg:#0a1526; --card:#13233a; --ink:#e4e9f0; --muted:#96a5bd; --line:#22314b; --th:#16233b; --hover:#16233b; --seam:#5b6b85; }}
</style>
<div class="wrap">
  <h1>Feature Scenario Catalog</h1>
  <div class="sub">Every “when a feature is ON/OFF, the software does X” rule across Hearth — generated from the real RBAC allow-lists + the feature dependency model. Filter and search below.</div>
  <div class="stats">
    <div class="stat total"><div class="statn">{len(rows)}</div><div class="statl">Total scenarios</div></div>
    {summary_cards}
  </div>
  <div class="controls">
    <select id="fcat"><option value="">All categories</option>{"".join(f'<option>{html.escape(c)}</option>' for c in cat_list)}</select>
    <select id="ftype"><option value="">All outcomes</option>{"".join(f'<option>{t}</option>' for t in sorted(TYPE_COLOR))}</select>
    <select id="frole"><option value="">All roles</option>{"".join(f'<option>{html.escape(r)}</option>' for r in role_list)}</select>
    <select id="fmod"><option value="">All modules</option>{"".join(f'<option>{html.escape(m)}</option>' for m in module_list)}</select>
    <input id="q" placeholder="Search conditions & behaviours…" />
    <span class="count" id="count"></span>
  </div>
  <table>
    <thead><tr><th>#</th><th>Category</th><th>When (condition)</th><th>Then (behaviour)</th><th>Type</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<script>
  const DATA = {data_json};
  const LABEL = {json.dumps(MODULE_LABEL, ensure_ascii=False)};
  const COLOR = {json.dumps(TYPE_COLOR)};
  const esc = s => (s||"").replace(/[&<>]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c]));
  const el = id => document.getElementById(id);
  function render() {{
    const cat = el('fcat').value, typ = el('ftype').value, role = el('frole').value,
          mod = el('fmod').value, q = el('q').value.trim().toLowerCase();
    let out = [], n = 0;
    for (const r of DATA) {{
      if (cat && r.cat !== cat) continue;
      if (typ && r.type !== typ) continue;
      if (role && r.role !== role) continue;
      if (mod && (LABEL[r.module]||r.module) !== mod) continue;
      if (q && !(r.cond+" "+r.result).toLowerCase().includes(q)) continue;
      n++;
      if (n > 1500) continue;
      const c = COLOR[r.type] || '#64748b';
      out.push('<tr><td class="idc">'+r.id+'</td><td class="cat">'+esc(r.cat)+'</td><td>'+esc(r.cond)+'</td><td>'+esc(r.result).replace(/\\[(.*?)\\]/g,'<span class="seam">[$1]</span>')+'</td><td><span class="pill" style="background:'+c+'">'+r.type+'</span></td></tr>');
    }}
    el('rows').innerHTML = out.join('');
    el('count').textContent = n + ' shown';
  }}
  ['fcat','ftype','frole','fmod','q'].forEach(id => el(id).addEventListener('input', render));
  render();
</script>"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(page)

print(f"TOTAL scenarios: {len(rows)}")
for c, n in cats.items():
    print(f"  {c}: {n}")
print(f"WROTE: {OUT}")
