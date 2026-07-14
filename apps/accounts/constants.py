"""Roles, RBAC allow-lists and edition/module mappings — the single source of truth.

Mirrors the prototype's ROLE map and entitlement-gated nav, but is enforced server-side.
"""

# --- Roles ---
# Twelve roles with segregation of duties. Super Admin / MD / GM have full
# access; every other role sees only its own desk — floor roles never see
# back-office modules and back-office roles never touch the floor.
ROLE_SUPER_ADMIN = "Super Admin"
ROLE_ADMIN = "Admin"
ROLE_MD = "Managing Director"
ROLE_CEO = "CEO"
ROLE_GM = "General Manager"
ROLE_FINANCE = "Finance"
ROLE_REST_MGR = "Restaurant Manager"
ROLE_FRONT_OFFICE = "Front Office"
ROLE_CASHIER = "F&B Cashier"
ROLE_CAPTAIN = "Captain"
ROLE_HOUSEKEEPING = "Housekeeping"
ROLE_CHEF = "Chef / Kitchen"
ROLE_STORE = "Store Keeper"
# Bar runs as its own operation, separate from the restaurant floor — its own
# tables, its own login, never the restaurant POS (and vice versa). Mirrors
# the restaurant's Cashier/Captain split: Bar Cashier handles cash at the bar
# counter, Bar Captain is tableside/digital-tenders-only.
ROLE_BAR_CAPTAIN = "Bar Captain"
ROLE_BAR_CASHIER = "Bar Cashier"

ROLE_CHOICES = [
    (ROLE_SUPER_ADMIN, ROLE_SUPER_ADMIN),
    (ROLE_ADMIN, ROLE_ADMIN),
    (ROLE_MD, ROLE_MD),
    (ROLE_CEO, ROLE_CEO),
    (ROLE_GM, ROLE_GM),
    (ROLE_FINANCE, ROLE_FINANCE),
    (ROLE_REST_MGR, ROLE_REST_MGR),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_CAPTAIN, ROLE_CAPTAIN),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
    (ROLE_CHEF, ROLE_CHEF),
    (ROLE_STORE, ROLE_STORE),
    (ROLE_BAR_CAPTAIN, ROLE_BAR_CAPTAIN),
    (ROLE_BAR_CASHIER, ROLE_BAR_CASHIER),
]

# Module keys used across nav, RBAC and entitlement gating.
ALL_MODULES = [
    "execdashboard", "dashboard", "frontdesk", "checkin", "checkout", "livegrid",
    "folio", "reservations", "housekeeping", "banquets", "revenue", "channel",
    "booking", "pos", "barpos", "inventory", "procurement", "pomanage", "matreq", "recipes",
    "accounting", "tax", "gstmaster", "roommaster", "tablemaster", "menumaster",
    "employees", "roles", "customers", "vendors", "suppliers", "hr", "engineering",
    "crm", "notifications", "reports", "settings", "cateringmaster",
]

# "*" == full access. Otherwise an explicit allow-list of module keys.
# Segregation of duties: each role sees only the modules its desk needs.
# Floor roles (Front Office, Cashier, Captain, Housekeeping, Chef) get no
# back-office; back-office roles (Finance, Admin) get no floor operations.
ROLE_ALLOW = {
    ROLE_SUPER_ADMIN: "*",
    ROLE_MD: "*",
    ROLE_GM: "*",
    # Admin — system administration: configuration masters, staff/user
    # management and settings. No day-to-day operations, no guest money.
    # Can still raise a general-supplies indent (office stationery etc.).
    ROLE_ADMIN: [
        "dashboard", "settings", "roles", "employees", "roommaster",
        "tablemaster", "menumaster", "gstmaster", "cateringmaster",
        "customers", "vendors", "suppliers", "notifications", "matreq",
    ],
    # CEO — executive oversight, read-heavy: dashboards, reports, revenue
    # strategy and CRM. No floor operations, no configuration.
    # CEO uses Executive Overview (with its own Hotel/Restaurant toggle) rather
    # than the operational Dashboard — that's for the two sector managers.
    ROLE_CEO: [
        "execdashboard", "reports", "revenue", "channel",
        "booking", "crm", "accounting", "tax", "hr", "notifications", "matreq",
    ],
    # Finance — books and statutory: accounting, tax/GST, AR (customers),
    # payables (vendors/suppliers/POs), payroll and reports. No floor ops.
    # Dashboard is now the two sector managers' own view (Front Office = hotel,
    # Restaurant Manager = restaurant) plus Admin and Super Admin/MD/GM — not
    # Finance by default. If Finance genuinely needs it, grant it per-property
    # via the Role Matrix rather than hardcoding it here.
    ROLE_FINANCE: [
        "accounting", "tax", "gstmaster", "reports",
        "customers", "vendors", "suppliers", "pomanage", "hr", "notifications",
        "matreq",
    ],
    # Restaurant Manager — runs the whole restaurant side: POS/KDS/online,
    # the store & supply chain (approves indents and POs), recipes and the
    # menu/table masters, restaurant reports. No rooms, no books.
    # barpos: oversight of the bar operation too, same as every other manager
    # who sees both sides — Bar Captain themselves only ever sees "barpos".
    ROLE_REST_MGR: [
        "dashboard", "pos", "barpos", "kds", "online", "inventory", "procurement",
        "pomanage", "matreq", "recipes", "suppliers", "vendors",
        "menumaster", "tablemaster", "reports", "notifications",
    ],
    # Front Office / Reception — the guest-facing desk only: front desk, room
    # assignment & status, reservations, folios/cashiering, banquets and guest
    # records. NO back-office (accounting, tax, reports, HR, CRM campaigns).
    # matreq: raises indents for its own supplies (stationery, amenities) and
    # is the approver for Housekeeping/Banquets/Front-Office indents below.
    ROLE_FRONT_OFFICE: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "customers", "notifications",
        "matreq",
    ],
    # F&B Cashier — POS & KOT; capped discounts; no rooms, no back-office.
    # Can raise indents for counter supplies; approval still routes elsewhere.
    ROLE_CASHIER: [
        "pos", "kds", "online", "notifications", "matreq",
    ],
    # Captain / steward — tableside ordering on mobile: POS only, plus raising
    # a material request when the section runs short of something.
    # Settlement is tender-restricted (see ROLE_TENDERS).
    ROLE_CAPTAIN: [
        "pos", "matreq",
    ],
    # Housekeeping — room status board, maintenance work orders, and indents
    # for its own supplies (linen, amenities, cleaning agents); also approves
    # Maintenance-department indents (it already owns the engineering module).
    ROLE_HOUSEKEEPING: [
        "housekeeping", "livegrid", "engineering", "notifications", "matreq",
    ],
    # Chef / Kitchen — kitchen display, recipes/BOM, kitchen stock and
    # material requests to the store. No sales, no purchasing.
    ROLE_CHEF: [
        "kds", "recipes", "inventory", "matreq", "notifications",
    ],
    # Store Keeper — stores & supply chain: stock, procurement, purchase
    # orders, material issue, supplier/vendor masters. No sales, no books.
    ROLE_STORE: [
        "inventory", "procurement", "pomanage", "matreq", "suppliers",
        "vendors", "notifications",
    ],
    # Bar Captain — runs the bar as its own desk: bar tables, bar tabs. Never
    # the restaurant POS/tables, and the restaurant floor never sees "barpos".
    ROLE_BAR_CAPTAIN: [
        "barpos", "matreq", "notifications",
    ],
    # Bar Cashier — the bar counter's cash handler, same split as F&B Cashier
    # vs Captain on the restaurant side.
    ROLE_BAR_CASHIER: [
        "barpos", "matreq", "notifications",
    ],
}

# Which entitlement flag each module requires. Modules absent here need none.
# Flags: hms, restaurant, banquets, rms.
MODULE_ENTITLEMENT = {
    # Rooms / hotel core (hms)
    "execdashboard": "hms",
    "frontdesk": "hms", "checkin": "hms", "checkout": "hms", "livegrid": "hms",
    "folio": "hms", "reservations": "hms", "housekeeping": "hms", "roommaster": "hms",
    "accounting": "hms", "engineering": "hms",
    # hr is a shared service — restaurants run staff attendance/payroll too.
    # Revenue / distribution
    "revenue": "rms", "channel": "hms", "booking": "hms",
    # Banquets
    "banquets": "banquets",
    "cateringmaster": "banquets",
    # Restaurant (restaurant)
    "pos": "restaurant", "barpos": "restaurant", "kds": "restaurant", "online": "restaurant",
    "inventory": "restaurant",
    "procurement": "restaurant", "pomanage": "restaurant",
    "recipes": "restaurant", "tablemaster": "restaurant", "menumaster": "restaurant",
    "suppliers": "restaurant",
    # matreq (material requests) is a shared service: hotel-only properties
    # use it for housekeeping/front-office/maintenance indents too.
    # dashboard, tax, gstmaster, crm, customers, vendors, employees, roles,
    # notifications, reports, settings -> no specific entitlement (shared services)
}


# --- Currency ---
# Symbol for each supported property currency (Settings > Masters > Currency).
# Mirrors the frontend's CURRENCIES list in lib/currency.ts — keep in sync.
CURRENCY_SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AED": "د.إ",
    "SAR": "﷼", "LKR": "Rs", "NPR": "रू", "BDT": "৳", "SGD": "S$",
    "MYR": "RM", "THB": "฿",
}


def currency_symbol() -> str:
    """The active property's currency symbol, for guest-facing message text
    (receipt SMS, report labels). Falls back to the code itself for a
    currency we don't have a symbol for, and to ₹ pre-setup."""
    from .models import Property
    code = (Property.objects.values_list("currency", flat=True).first() or "INR").upper()
    return CURRENCY_SYMBOLS.get(code, code + " ")


# --- Approval chains (segregation of duties) ---
# Spending money (PO approval) is a manager's call; issuing held stock
# (indent approval) is the store's call — never the requester's own.
PO_APPROVER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_FINANCE, ROLE_REST_MGR}

# Menu/recipe costing (plate cost, margin %) is ownership-level P&L information —
# Chef and Restaurant Manager build and run recipes without needing to see it.
COST_VISIBLE_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}

# A Chef-proposed new dish needs sign-off before it's orderable — the direct
# manager, with GM/MD/Super Admin as a universal override (same shape as
# PO_APPROVER_ROLES / indent approval below).
MENU_APPROVER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_REST_MGR}

# Material requests: every department has its own approver — the head who
# actually knows whether that indent makes sense — plus GM/MD/Super Admin as
# a universal override. The Store Keeper never approves (they're the
# custodian who hands over stock at the ISSUE step, not the requester's boss).
#
# The pattern that MUST hold for every department: requester role ≠ approver
# role (Housekeeping requests → Front Office approves is the model). Kitchen
# and Bar have no separate floor role that "owns" requesting the way
# Housekeeping does, so Chef/Cashier/Captain do the requesting and Restaurant
# Manager stays approver-ONLY there (see role_can_request_department below —
# Restaurant Manager is blocked from picking Kitchen/Bar, otherwise the same
# person could raise a request no one else could sign off on).
# Banquets and the Front Office's own supplies have the mirror problem: Front
# Office is the only role that operates there, so IT must be the requester —
# meaning the approver can't be Front Office too. Those two route to GM/MD/
# Super Admin only, same as any unlisted department.
DEPARTMENT_APPROVERS = {
    "Kitchen": {ROLE_REST_MGR},
    "Bar": {ROLE_REST_MGR},
    "Housekeeping": {ROLE_FRONT_OFFICE},   # "frontdesk manager" per house convention
    "Maintenance": {ROLE_HOUSEKEEPING},    # Housekeeping owns engineering/work-orders
    # "Banquets" and "Front Office" intentionally absent — Front Office is the
    # only role that would ever raise those, so it can't also approve them;
    # they fall through to the universal GM/MD/Super Admin approvers below.
}
UNIVERSAL_INDENT_APPROVERS = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}
# Any department not listed above (Banquets, Front Office, Other/general
# office supplies) still needs a real approver — GM+ only, never unapproved.


def indent_approvers_for(department: str) -> set:
    """Who may approve (not issue) an indent for this department."""
    return DEPARTMENT_APPROVERS.get(department, set()) | UNIVERSAL_INDENT_APPROVERS


def role_can_request_department(role: str, department: str) -> bool:
    """A role can't raise a request for a department it ALSO approves — that
    would let the same person be both sides of the approval (the Restaurant
    Manager requesting Kitchen stock and then being the only one who could
    sign off on it). Universal approvers (GM/MD/Super Admin) are exempt —
    they're already full-access executives everywhere else in this system.
    """
    if role in UNIVERSAL_INDENT_APPROVERS:
        return True
    return role not in DEPARTMENT_APPROVERS.get(department, set())


# Issuing approved stock is always the store's job — it's a physical handover
# from the shelf, regardless of which department the indent was for.
INDENT_ISSUER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_REST_MGR, ROLE_STORE}

# Marking food ready on the KDS is the kitchen's alone (chef + managers).
KITCHEN_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_REST_MGR, ROLE_CHEF}


# --- POS tender mapping (BRD 5.10 role mapping) ---
# Which tenders each role may accept when settling a bill. "*" == all tenders.
# Captains take digital payments tableside; whether a specific tender is
# captain-safe now comes from the PaymentMethod master's captain_allowed flag
# (Settings > Masters) — the static list below is only the fallback for
# tenders that predate the master or bypass it (aggregator prepaid rows).
ROLE_TENDERS = {
    ROLE_SUPER_ADMIN: "*",
    ROLE_MD: "*",
    ROLE_GM: "*",
    ROLE_FINANCE: "*",
    ROLE_REST_MGR: "*",
    ROLE_CASHIER: "*",
    ROLE_FRONT_OFFICE: "*",
    ROLE_CAPTAIN: ["UPI", "Gateway"],
    ROLE_BAR_CAPTAIN: ["UPI", "Gateway"],
    ROLE_BAR_CASHIER: "*",
}


def role_can_tender(role: str, tender: str) -> bool:
    allow = ROLE_TENDERS.get(role)
    if allow is None:
        return False
    from apps.masters.models import PaymentMethod
    pm = PaymentMethod.objects.filter(name=tender).first()
    if pm is not None:
        if not pm.active:
            return False
        return True if allow == "*" else pm.captain_allowed
    if allow == "*":
        return True
    return tender in allow


def role_can_access(role: str, module: str) -> bool:
    allow = ROLE_ALLOW.get(role)
    if allow == "*":
        return True
    if allow is None:
        return False
    return module in allow


def entitlement_allows(entitlements: dict, module: str) -> bool:
    flag = MODULE_ENTITLEMENT.get(module)
    if flag is None:
        return True
    return bool(entitlements.get(flag))


def edition_entitlements(edition: str) -> dict:
    """Map an edition choice to the four entitlement flags."""
    if edition == "hotel":
        return {"hms": True, "restaurant": False, "banquets": True, "rms": True}
    if edition == "restaurant":
        return {"hms": False, "restaurant": True, "banquets": False, "rms": False}
    # both / default
    return {"hms": True, "restaurant": True, "banquets": True, "rms": True}
