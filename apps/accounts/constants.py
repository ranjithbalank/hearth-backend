"""Roles, RBAC allow-lists and edition/module mappings — the single source of truth.

Mirrors the prototype's ROLE map and entitlement-gated nav, but is enforced server-side.
"""

# --- Roles ---
ROLE_MD = "Managing Director"
ROLE_GM = "General Manager"
ROLE_FRONT_OFFICE = "Front Office"
ROLE_CASHIER = "F&B Cashier"
ROLE_HOUSEKEEPING = "Housekeeping"

ROLE_CHOICES = [
    (ROLE_MD, ROLE_MD),
    (ROLE_GM, ROLE_GM),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
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
ROLE_ALLOW = {
    ROLE_MD: "*",
    ROLE_GM: "*",
    ROLE_FRONT_OFFICE: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "channel", "crm", "reports",
        "notifications",
    ],
    ROLE_CASHIER: [
        "dashboard", "pos", "kds", "matreq", "inventory", "procurement", "recipes",
        "reports", "notifications",
    ],
    ROLE_HOUSEKEEPING: [
        "dashboard", "housekeeping", "livegrid", "engineering", "notifications",
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
    "pos": "restaurant", "kds": "restaurant", "inventory": "restaurant",
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
