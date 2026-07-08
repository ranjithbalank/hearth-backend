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
# Hotel Manager is Front Office's manager tier, mirroring Restaurant Manager
# on the F&B side: runs the hotel-side dashboard, approves Housekeeping/
# Banquets/Front-Office indents, but — same rule as Restaurant Manager and
# Kitchen/Bar — never requests for those departments itself.
ROLE_HOTEL_MGR = "Hotel Manager"
ROLE_FRONT_OFFICE = "Front Office"
ROLE_CASHIER = "F&B Cashier"
ROLE_CAPTAIN = "Captain"
ROLE_HOUSEKEEPING = "Housekeeping"
ROLE_CHEF = "Chef / Kitchen"
ROLE_STORE = "Store Keeper"
# HR Manager — people operations: the staff roster, attendance/payroll and
# the leave desk (types master, on-behalf entry, oversight). Leave is a
# two-level approval: the department's own manager first (see
# LEAVE_DEPARTMENT_APPROVERS), then HR gives the final sign-off that puts it
# on the attendance/payroll record.
ROLE_HR = "HR Manager"
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
    (ROLE_HOTEL_MGR, ROLE_HOTEL_MGR),
    (ROLE_FRONT_OFFICE, ROLE_FRONT_OFFICE),
    (ROLE_CASHIER, ROLE_CASHIER),
    (ROLE_CAPTAIN, ROLE_CAPTAIN),
    (ROLE_HOUSEKEEPING, ROLE_HOUSEKEEPING),
    (ROLE_CHEF, ROLE_CHEF),
    (ROLE_STORE, ROLE_STORE),
    (ROLE_BAR_CAPTAIN, ROLE_BAR_CAPTAIN),
    (ROLE_BAR_CASHIER, ROLE_BAR_CASHIER),
    (ROLE_HR, ROLE_HR),
]

# Module keys used across nav, RBAC and entitlement gating.
ALL_MODULES = [
    "execdashboard", "dashboard", "frontdesk", "checkin", "checkout", "livegrid",
    "folio", "reservations", "housekeeping", "banquets", "revenue", "channel",
    "booking", "pos", "barpos", "inventory", "procurement", "pomanage", "matreq", "recipes",
    "accounting", "tax", "gstmaster", "roommaster", "tablemaster", "menumaster",
    "employees", "roles", "customers", "vendors", "suppliers", "hr", "engineering",
    "crm", "notifications", "reports", "settings", "cateringmaster", "branchmaster",
    # leave is a shared service like matreq — every role can open its own
    # leave desk (apply, balances, track); approvals are role-gated inside.
    "leave",
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
        "tablemaster", "menumaster", "gstmaster", "cateringmaster", "branchmaster",
        "customers", "vendors", "suppliers", "notifications", "matreq", "leave",
    ],
    # CEO — executive oversight, read-heavy: dashboards, reports, revenue
    # strategy and CRM. No floor operations, no configuration.
    # CEO uses Executive Overview (with its own Hotel/Restaurant toggle) rather
    # than the operational Dashboard — that's for the two sector managers.
    ROLE_CEO: [
        "execdashboard", "reports", "revenue", "channel",
        "booking", "crm", "accounting", "tax", "hr", "notifications", "matreq", "leave",
    ],
    # Finance — books and statutory: accounting, tax/GST, AR (customers),
    # payables (vendors/suppliers/POs), payroll and reports. No floor ops.
    # Dashboard is now the two sector managers' own view (Hotel Manager =
    # hotel, Restaurant Manager = restaurant) plus Admin and Super Admin/MD/GM
    # — not Finance by default. If Finance genuinely needs it, grant it
    # per-property via the Role Matrix rather than hardcoding it here.
    ROLE_FINANCE: [
        "accounting", "tax", "gstmaster", "reports",
        "customers", "vendors", "suppliers", "pomanage", "hr", "notifications",
        "matreq", "leave",
    ],
    # Restaurant Manager — runs the whole restaurant side: POS/KDS/online,
    # the store & supply chain (approves indents and POs), recipes and the
    # menu/table masters, restaurant reports. No rooms, no books.
    # barpos: oversight of the bar operation too, same as every other manager
    # who sees both sides — Bar Captain themselves only ever sees "barpos".
    ROLE_REST_MGR: [
        "dashboard", "pos", "barpos", "kds", "online", "inventory", "procurement",
        "pomanage", "matreq", "recipes", "suppliers", "vendors",
        "menumaster", "tablemaster", "reports", "notifications", "leave",
    ],
    # Hotel Manager — the hotel-side counterpart to Restaurant Manager: runs
    # the hotel dashboard, oversees Front Desk's operations, and approves
    # Housekeeping/Banquets/Front-Office indents. Never requests for those
    # departments itself (same rule as Restaurant Manager and Kitchen/Bar —
    # see role_can_request_department below). No books, no RBAC config.
    ROLE_HOTEL_MGR: [
        "dashboard", "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "roommaster", "cateringmaster",
        "customers", "reports", "matreq", "notifications", "leave",
    ],
    # Front Office / Reception — the guest-facing desk only: front desk, room
    # assignment & status, reservations, folios/cashiering, banquets and guest
    # records. NO back-office (accounting, tax, reports, HR, CRM campaigns).
    # No "dashboard" — that's Hotel Manager's, same as Cashier/Captain never
    # having it on the restaurant side.
    # matreq: raises indents for its own supplies (stationery, amenities);
    # Hotel Manager approves Housekeeping/Banquets/Front-Office indents below.
    ROLE_FRONT_OFFICE: [
        "frontdesk", "checkin", "checkout", "livegrid", "folio",
        "reservations", "housekeeping", "banquets", "customers", "notifications",
        "matreq", "leave",
    ],
    # F&B Cashier — POS & KOT; capped discounts; no rooms, no back-office.
    # Can raise indents for counter supplies; approval still routes elsewhere.
    ROLE_CASHIER: [
        "pos", "kds", "online", "notifications", "matreq", "leave",
    ],
    # Captain / steward — tableside ordering on mobile: POS only, plus raising
    # a material request when the section runs short of something.
    # Settlement is tender-restricted (see ROLE_TENDERS).
    ROLE_CAPTAIN: [
        "pos", "matreq", "notifications", "leave",
    ],
    # Housekeeping — room status board, maintenance work orders, and indents
    # for its own supplies (linen, amenities, cleaning agents); also approves
    # Maintenance-department indents (it already owns the engineering module).
    ROLE_HOUSEKEEPING: [
        "housekeeping", "livegrid", "engineering", "notifications", "matreq", "leave",
    ],
    # Chef / Kitchen — kitchen display, recipes/BOM, kitchen stock and
    # material requests to the store. No sales, no purchasing.
    ROLE_CHEF: [
        "kds", "recipes", "inventory", "matreq", "notifications", "leave",
    ],
    # Store Keeper — stores & supply chain: stock, procurement, purchase
    # orders, material issue, supplier/vendor masters. No sales, no books.
    ROLE_STORE: [
        "inventory", "procurement", "pomanage", "matreq", "suppliers",
        "vendors", "notifications", "leave",
    ],
    # Bar Captain — runs the bar as its own desk: bar tables, bar tabs. Never
    # the restaurant POS/tables, and the restaurant floor never sees "barpos".
    ROLE_BAR_CAPTAIN: [
        "barpos", "matreq", "notifications", "leave",
    ],
    # Bar Cashier — the bar counter's cash handler, same split as F&B Cashier
    # vs Captain on the restaurant side.
    ROLE_BAR_CASHIER: [
        "barpos", "matreq", "notifications", "leave",
    ],
    # HR Manager — people operations: staff roster, attendance, payroll and
    # the leave desk (types master, on-behalf requests, full oversight).
    # No floor operations, no guest money, no RBAC config.
    ROLE_HR: [
        "hr", "employees", "leave", "matreq", "notifications",
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


# --- Approval chains (segregation of duties) ---
# Spending money (PO approval) is a manager's call; issuing held stock
# (indent approval) is the store's call — never the requester's own.
PO_APPROVER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_FINANCE, ROLE_REST_MGR}

# Raising and receiving a PO are the buying side's job — Store Keeper and
# Restaurant Manager physically deal with the goods. Finance approves the
# spend but never originates or receives it — same "never both sides of a
# handoff" rule as everywhere else (matreq, leave): without this, Finance
# having the "procurement" module (needed just to see/approve POs) would
# otherwise let Finance also raise a PO and receive goods against it,
# nobody else in the loop.
PO_HANDLER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_REST_MGR, ROLE_STORE}

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
# role. Kitchen and Bar have no separate floor role that "owns" requesting
# the way Housekeeping does, so Chef/Cashier/Captain do the requesting and
# Restaurant Manager stays approver-ONLY there (see role_can_request_department
# below — Restaurant Manager is blocked from picking Kitchen/Bar, otherwise
# the same person could raise a request no one else could sign off on).
# Housekeeping/Banquets/Front-Office-supplies mirror that on the hotel side:
# Hotel Manager approves all three but never requests for them — Front
# Office (and Housekeeping, for its own department) does the requesting.
DEPARTMENT_APPROVERS = {
    "Kitchen": {ROLE_REST_MGR},
    "Bar": {ROLE_REST_MGR},
    "Housekeeping": {ROLE_HOTEL_MGR},
    "Banquets": {ROLE_HOTEL_MGR},
    "Front Office": {ROLE_HOTEL_MGR},
    "Maintenance": {ROLE_HOUSEKEEPING},    # Housekeeping owns engineering/work-orders
}
UNIVERSAL_INDENT_APPROVERS = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}
# Any department not listed above (general office supplies etc.) still needs
# a real approver — GM+ only, never unapproved.

# CEO gets the same full-property visibility as the universal approvers
# (every department, every status, in matreq's list()) for oversight — but
# is deliberately NOT folded into UNIVERSAL_INDENT_APPROVERS itself, since
# that set also drives indent_approvers_for() and role_can_request_department()
# below. Adding CEO there would silently make CEO a real approver for every
# department. This set is visibility-only: CEO can see who's waiting on what,
# never approve or issue it themselves.
INDENT_OVERSIGHT_ROLES = UNIVERSAL_INDENT_APPROVERS | {ROLE_CEO}


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


# --- Leave approvals (FR-HRM — same shape as DEPARTMENT_APPROVERS above) ---
# Two-level flow: the employee's department manager approves first (they know
# the roster — hotel-side departments the Hotel Manager, F&B-side the
# Restaurant Manager), then HR gives the FINAL sign-off; only that final
# approval writes the attendance marks that feed payroll. GM/MD/Super Admin
# are universal override at both levels. A manager's own leave (or any
# unmapped department) falls through to the universal approvers for the
# first level; HR's own leave gets its final decision from a universal role
# (nobody ever decides their own request at either level).
LEAVE_DEPARTMENT_APPROVERS = {
    "Kitchen": {ROLE_REST_MGR},
    "Bar": {ROLE_REST_MGR},
    "F&B": {ROLE_REST_MGR},
    "Restaurant": {ROLE_REST_MGR},
    "Housekeeping": {ROLE_HOTEL_MGR},
    "Banquets": {ROLE_HOTEL_MGR},
    "Front Office": {ROLE_HOTEL_MGR},
    "Reservations": {ROLE_HOTEL_MGR},
    "Maintenance": {ROLE_HOTEL_MGR},
}
UNIVERSAL_LEAVE_APPROVERS = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM}
# Level 2 — the final sign-off after the department manager's approval.
LEAVE_FINAL_APPROVERS = {ROLE_HR} | UNIVERSAL_LEAVE_APPROVERS
# Full visibility of every request (mirror of INDENT_OVERSIGHT_ROLES):
# HR runs the desk, CEO watches without any approval rights.
LEAVE_OVERSIGHT_ROLES = UNIVERSAL_LEAVE_APPROVERS | {ROLE_HR, ROLE_CEO}
# The leave-types master (quotas, paid/unpaid) is HR's configuration surface,
# plus Admin (owns the other masters) and the universal roles.
LEAVE_TYPE_MANAGER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_ADMIN, ROLE_HR}

# Running / finalizing / paying a payroll month moves real money — HR runs
# it, Finance pays it, GM/MD/Super Admin override. CEO keeps the "hr" module
# for oversight but stays read-only here.
PAYROLL_MANAGER_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_HR, ROLE_FINANCE}


def leave_approvers_for(department: str) -> set:
    """Who may approve a leave request from this department's staff."""
    return LEAVE_DEPARTMENT_APPROVERS.get(department, set()) | UNIVERSAL_LEAVE_APPROVERS


def can_enter_leave_on_behalf(role: str, department: str) -> bool:
    """HR, the department's own approver, or a universal role may file a
    request for an employee without a login (kitchen helpers, cleaners)."""
    return role == ROLE_HR or role in leave_approvers_for(department)

# Marking food ready on the KDS is the kitchen's alone (chef + managers).
KITCHEN_ROLES = {ROLE_SUPER_ADMIN, ROLE_MD, ROLE_GM, ROLE_REST_MGR, ROLE_CHEF}


# --- Reports scoping ---
# Having the "reports" module (see ROLE_ALLOW) only gates that a role can
# open the Reports screen at all — it says nothing about WHICH report. The
# non-universal roles that carry "reports" (Restaurant Manager, Hotel
# Manager, Finance, CEO) each only get a slice, mirroring their module access
# everywhere else: Restaurant Manager gets F&B/restaurant-analytics, Hotel
# Manager gets the hotel side, Finance/CEO get the money + strategic reports.
# Guest KYC ("guests" — raw ID numbers) is deliberately absent from ALL of
# them: Super Admin/MD/GM only, the tightest PII gate in the system.
RESTAURANT_ANALYTICS_REPORTS = {
    "recipe_consumption", "sales_vs_consumption", "purchase_vs_consumption",
    "food_cost", "item_profitability", "aggregator",
}
ROLE_REPORT_ACCESS = {
    ROLE_REST_MGR: {"sales"} | RESTAURANT_ANALYTICS_REPORTS,
    ROLE_HOTEL_MGR: {"sales", "source", "occupancy"},
    ROLE_FINANCE: {"sales", "tax", "accounting"} | RESTAURANT_ANALYTICS_REPORTS,
    ROLE_CEO: {"sales", "tax", "accounting", "source", "occupancy"} | RESTAURANT_ANALYTICS_REPORTS,
}


def role_can_view_report(role: str, report: str) -> bool:
    """Full-access roles (Super Admin/MD/GM) see every report. Everyone else
    with 'reports' only sees their slice — see ROLE_REPORT_ACCESS above."""
    if ROLE_ALLOW.get(role) == "*":
        return True
    return report in ROLE_REPORT_ACCESS.get(role, set())


# --- POS tender mapping (BRD 5.10 role mapping) ---
# Which tenders each role may accept when settling a bill. "*" == all tenders.
# Captains take digital payments (UPI / gateway) tableside; cash is counted and
# reconciled only at the cashier counter, so it stays with cashier/managers.
ROLE_TENDERS = {
    ROLE_SUPER_ADMIN: "*",
    ROLE_MD: "*",
    ROLE_GM: "*",
    ROLE_FINANCE: "*",
    ROLE_REST_MGR: "*",
    ROLE_CASHIER: "*",
    ROLE_HOTEL_MGR: "*",
    ROLE_FRONT_OFFICE: "*",
    ROLE_CAPTAIN: ["UPI", "Gateway"],
    ROLE_BAR_CAPTAIN: ["UPI", "Gateway"],
    ROLE_BAR_CASHIER: "*",
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
