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
ROLE_FRONT_OFFICE = "Front Office"
ROLE_CASHIER = "F&B Cashier"
ROLE_CAPTAIN = "Captain"
ROLE_HOUSEKEEPING = "Housekeeping"
ROLE_CHEF = "Chef / Kitchen"
ROLE_STORE = "Store Keeper"

ROLE_CHOICES = [
    (ROLE_SUPER_ADMIN, ROLE_SUPER_ADMIN),
    (ROLE_ADMIN, ROLE_ADMIN),
    (ROLE_MD, ROLE_MD),
    (ROLE_CEO, ROLE_CEO),
    (ROLE_GM, ROLE_GM),
    (ROLE_FINANCE, ROLE_FINANCE),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_CAPTAIN, ROLE_CAPTAIN),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
    (ROLE_CHEF, ROLE_CHEF),
    (ROLE_STORE, ROLE_STORE),
]

# Module keys used across nav, RBAC and entitlement gating.
ALL_MODULES = [
    "execdashboard", "dashboard", "frontdesk", "checkin", "checkout", "livegrid",
    "folio", "reservations", "housekeeping", "banquets", "revenue", "channel",
    "booking", "pos", "inventory", "procurement", "pomanage", "matreq", "recipes",
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
    ROLE_ADMIN: [
        "dashboard", "settings", "roles", "employees", "roommaster",
        "tablemaster", "menumaster", "gstmaster", "cateringmaster",
        "customers", "vendors", "suppliers", "notifications",
    ],
    # CEO — executive oversight, read-heavy: dashboards, reports, revenue
    # strategy and CRM. No floor operations, no configuration.
    ROLE_CEO: [
        "execdashboard", "dashboard", "reports", "revenue", "channel",
        "booking", "crm", "accounting", "tax", "hr", "notifications",
    ],
    # Finance — books and statutory: accounting, tax/GST, AR (customers),
    # payables (vendors/suppliers/POs), payroll and reports. No floor ops.
    ROLE_FINANCE: [
        "dashboard", "accounting", "tax", "gstmaster", "reports",
        "customers", "vendors", "suppliers", "pomanage", "hr", "notifications",
    ],
    # Front Office / Reception — the guest-facing desk only: front desk, room
    # assignment & status, reservations, folios/cashiering, banquets and guest
    # records. NO back-office (accounting, tax, reports, HR, CRM campaigns).
    ROLE_FRONT_OFFICE: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "customers", "notifications",
    ],
    # F&B Cashier — POS & KOT; capped discounts; no rooms, no back-office.
    ROLE_CASHIER: [
        "pos", "kds", "online", "notifications",
    ],
    # Captain / steward — tableside ordering on mobile: POS only.
    # Settlement is tender-restricted (see ROLE_TENDERS).
    ROLE_CAPTAIN: [
        "pos",
    ],
    # Housekeeping — room status board and maintenance work orders.
    ROLE_HOUSEKEEPING: [
        "housekeeping", "livegrid", "engineering", "notifications",
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
    "pos": "restaurant", "kds": "restaurant", "online": "restaurant", "inventory": "restaurant",
    "procurement": "restaurant", "pomanage": "restaurant", "matreq": "restaurant",
    "recipes": "restaurant", "tablemaster": "restaurant", "menumaster": "restaurant",
    "suppliers": "restaurant",
    # dashboard, tax, gstmaster, crm, customers, vendors, employees, roles,
    # notifications, reports, settings -> no specific entitlement (shared services)
}


# --- POS tender mapping (BRD 5.10 role mapping) ---
# Which tenders each role may accept when settling a bill. "*" == all tenders.
# Captains take digital payments (UPI / gateway) tableside; cash is counted and
# reconciled only at the cashier counter, so it stays with cashier/managers.
ROLE_TENDERS = {
    ROLE_SUPER_ADMIN: "*",
    ROLE_MD: "*",
    ROLE_GM: "*",
    ROLE_FINANCE: "*",
    ROLE_CASHIER: "*",
    ROLE_FRONT_OFFICE: "*",
    ROLE_CAPTAIN: ["UPI", "Gateway"],
}


def role_can_tender(role: str, tender: str) -> bool:
    allow = ROLE_TENDERS.get(role)
    if allow == "*":
        return True
    if allow is None:
        return False
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
