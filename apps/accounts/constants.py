"""Roles, RBAC allow-lists and edition/module mappings — the single source of truth.

Mirrors the prototype's ROLE map and entitlement-gated nav, but is enforced server-side.
"""

# --- Roles (BRD §3.1) ---
ROLE_MD = "Managing Director"
ROLE_GM = "General Manager"
ROLE_FRONT_OFFICE = "Front Office"
ROLE_REVENUE = "Revenue Manager"
ROLE_HOUSEKEEPING = "Housekeeping"
ROLE_SALES_BANQUETS = "Sales & Banquets"
ROLE_CASHIER = "F&B Cashier"
ROLE_STORE = "Store & Purchase"
ROLE_NIGHT_AUDIT = "Night Auditor"

ROLE_CHOICES = [
    (ROLE_MD, ROLE_MD),
    (ROLE_GM, ROLE_GM),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_REVENUE, ROLE_REVENUE),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
    (ROLE_SALES_BANQUETS, ROLE_SALES_BANQUETS),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_STORE, ROLE_STORE),
    (ROLE_NIGHT_AUDIT, ROLE_NIGHT_AUDIT),
]

# Module keys used across nav, RBAC and entitlement gating.
ALL_MODULES = [
    "execdashboard", "dashboard", "frontdesk", "checkin", "checkout", "livegrid",
    "folio", "reservations", "housekeeping", "banquets", "revenue", "channel",
    "booking", "pos", "inventory", "procurement", "pomanage", "matreq", "recipes",
    "accounting", "tax", "gstmaster", "roommaster", "tablemaster", "menumaster",
    "employees", "roles", "customers", "vendors", "suppliers", "hr", "engineering",
    "crm", "notifications", "reports", "settings",
]

# "*" == full access. Otherwise an explicit allow-list of module keys.
# Allow-lists follow the BRD §3.1 "typical access" per role (least privilege,
# with segregation of duties between front office, revenue, banquets and stores).
ROLE_ALLOW = {
    ROLE_MD: "*",
    ROLE_GM: "*",
    # Front Office / Reception — front desk, folios, cashiering; reads room
    # status for assignment. No revenue, banquets or stores.
    ROLE_FRONT_OFFICE: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "crm", "customers", "reports", "notifications",
    ],
    # Reservations / Revenue Manager — rates, availability, channels, forecasts.
    ROLE_REVENUE: [
        "dashboard", "reservations", "revenue", "channel", "booking", "livegrid",
        "reports", "notifications",
    ],
    # Housekeeping — room status and maintenance work orders.
    ROLE_HOUSEKEEPING: [
        "dashboard", "housekeeping", "livegrid", "engineering", "notifications",
    ],
    # Sales & Banquets — event enquiries, function bookings, event folios.
    ROLE_SALES_BANQUETS: [
        "dashboard", "banquets", "crm", "customers", "reports", "notifications",
    ],
    # F&B Cashier / Captain — POS & KOT only; capped discounts; no rooms/stores.
    ROLE_CASHIER: [
        "dashboard", "pos", "kds", "online", "reports", "notifications",
    ],
    # Store / Purchase keeper — stores, inventory, purchasing, recipes/BOM.
    ROLE_STORE: [
        "dashboard", "inventory", "procurement", "pomanage", "matreq", "suppliers",
        "vendors", "recipes", "reports", "notifications",
    ],
    # Night Auditor — end-of-day close, postings, day-end reports.
    ROLE_NIGHT_AUDIT: [
        "dashboard", "accounting", "folio", "livegrid", "tax", "reports", "notifications",
    ],
}

# Which entitlement flag each module requires. Modules absent here need none.
# Flags: hms, restaurant, banquets, rms.
MODULE_ENTITLEMENT = {
    # Rooms / hotel core (hms)
    "execdashboard": "hms",
    "frontdesk": "hms", "checkin": "hms", "checkout": "hms", "livegrid": "hms",
    "folio": "hms", "reservations": "hms", "housekeeping": "hms", "roommaster": "hms",
    "accounting": "hms", "engineering": "hms", "hr": "hms",
    # Revenue / distribution
    "revenue": "rms", "channel": "hms", "booking": "hms",
    # Banquets
    "banquets": "banquets",
    # Restaurant (restaurant)
    "pos": "restaurant", "kds": "restaurant", "online": "restaurant", "inventory": "restaurant",
    "procurement": "restaurant", "pomanage": "restaurant", "matreq": "restaurant",
    "recipes": "restaurant", "tablemaster": "restaurant", "menumaster": "restaurant",
    "suppliers": "restaurant",
    # dashboard, tax, gstmaster, crm, customers, vendors, employees, roles,
    # notifications, reports, settings -> no specific entitlement (shared services)
}


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
