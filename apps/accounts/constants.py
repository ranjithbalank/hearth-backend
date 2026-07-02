"""Roles, RBAC allow-lists and edition/module mappings — the single source of truth.

Mirrors the prototype's ROLE map and entitlement-gated nav, but is enforced server-side.
"""

# --- Roles ---
# Six operational roles. MD & GM have full access; the rest are scoped.
ROLE_MD = "Managing Director"
ROLE_GM = "General Manager"
ROLE_FRONT_OFFICE = "Front Office"
ROLE_CASHIER = "F&B Cashier"
ROLE_CAPTAIN = "Captain"
ROLE_HOUSEKEEPING = "Housekeeping"

ROLE_CHOICES = [
    (ROLE_MD, ROLE_MD),
    (ROLE_GM, ROLE_GM),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_CAPTAIN, ROLE_CAPTAIN),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
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
# Least privilege: MD/GM run everything; the three floor roles are scoped to
# what that desk actually does. Back-office/distribution/config stay with MD/GM.
ROLE_ALLOW = {
    ROLE_MD: "*",
    ROLE_GM: "*",
    # Front Office / Reception — the single guest-facing desk: front desk, room
    # assignment & status, reservations, folios/cashiering, banquets & events,
    # and guest records.
    ROLE_FRONT_OFFICE: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "cateringmaster", "crm", "customers",
        "reports", "notifications",
    ],
    # F&B Cashier — POS & KOT; capped discounts; no rooms access.
    ROLE_CASHIER: [
        "pos", "kds", "online", "reports", "notifications",
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
    ROLE_MD: "*",
    ROLE_GM: "*",
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
