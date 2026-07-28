"""The feature-configuration layer — the third gate between entitlement and RBAC.

Three axes decide whether a screen/endpoint is usable:

    ENTITLEMENT  (what the property is licensed for — coarse: hms/restaurant/banquets/rms)
        ⊇
    FEATURE CONFIG  (what the owner turned ON within that entitlement — per-module, THIS file)
        →
    RBAC  (which of the 17 roles may open it — constants.ROLE_ALLOW / RoleConfig)

Historically only the 4 edition flags existed, so a whole side of the app went on/off
together (every hotel screen gated on the single `hms` flag — you could not, say, run a
hotel with no separate Housekeeping department). This module adds a per-feature toggle
plus a **dependency graph** so:

  * a feature is only ON when its entitlement allows it AND every prerequisite is ON;
  * turning a prerequisite OFF cascades its dependents OFF (never a dangling child);
  * a feature that is OFF but partially used by another flow degrades gracefully — the
    consuming code checks `feature_on("housekeeping")` and reroutes (see `degrades`).

`FEATURES` here is the single source of truth. The entitlement flag per module still
comes from `constants.MODULE_ENTITLEMENT` (imported, not duplicated); this file adds the
`requires` / `degrades` / `group` / `toggleable` metadata on top and the resolver that
combines all three axes.
"""
from .constants import MODULE_ENTITLEMENT

# Feature groups — the order/sections the Settings → Features admin screen renders.
GROUP_FRONT_OFFICE = "Front office"
GROUP_DISTRIBUTION = "Distribution"
GROUP_FNB = "Restaurant & bar"
GROUP_STORE = "Store & purchasing"
GROUP_GUESTS = "Guests & events"
GROUP_BACK_OFFICE = "Back office"
GROUP_CORE = "Core (always on)"


def _f(label, group, *, requires=(), degrades=(), toggleable=True, default=True, note=""):
    """One feature spec. `entitlement` is pulled from the existing MODULE_ENTITLEMENT
    map so there is exactly one place that mapping lives."""
    return {
        "label": label,
        "group": group,
        "entitlement": None,  # filled in below from MODULE_ENTITLEMENT
        "requires": list(requires),
        "degrades": list(degrades),
        "toggleable": toggleable,
        "default": default,
        "note": note,
    }


# --- The feature model -------------------------------------------------------
# key -> spec. `requires` names other feature keys that must be ON for this one to
# be ON. `degrades` names the flows in OTHER modules that must adapt when this
# feature is OFF (documentation for the scenario catalog + a checklist for the
# graceful-degradation seams; the consuming code calls `feature_on(key)`).
FEATURES = {
    # --- Core: structural, never toggled off (you need Settings to turn things
    #     back on; Dashboard/Notifications are the shell itself) ---
    "dashboard":      _f("Dashboard", GROUP_CORE, toggleable=False),
    "execdashboard":  _f("Executive Overview", GROUP_CORE, toggleable=False),
    "reports":        _f("Reports", GROUP_CORE, toggleable=False),
    "notifications":  _f("Notifications", GROUP_CORE, toggleable=False),
    "settings":       _f("Settings", GROUP_CORE, toggleable=False),
    "roles":          _f("Role Mapping", GROUP_CORE, toggleable=False),
    "branchmaster":   _f("Branch Master", GROUP_CORE, toggleable=False),
    "customers":      _f("Customers", GROUP_CORE, toggleable=False),
    "matreq":         _f("Material Requests", GROUP_CORE, toggleable=False),
    "leave":          _f("Leave", GROUP_CORE, toggleable=False),
    "gstmaster":      _f("GST Master", GROUP_CORE, toggleable=False),
    "tax":            _f("Tax & GST", GROUP_CORE, toggleable=False),

    # --- Front office (hotel core is hms-gated & not individually toggleable;
    #     the optional desks on top of it ARE toggleable) ---
    "roommaster":     _f("Room Master", GROUP_FRONT_OFFICE, toggleable=False),
    "livegrid":       _f("Live Grid", GROUP_FRONT_OFFICE, toggleable=False),
    "frontdesk":      _f("Front Desk", GROUP_FRONT_OFFICE, toggleable=False),
    "checkin":        _f("Check-in", GROUP_FRONT_OFFICE, toggleable=False),
    "checkout":       _f("Check-out", GROUP_FRONT_OFFICE, toggleable=False),
    "folio":          _f("Folios", GROUP_FRONT_OFFICE, toggleable=False),
    "reservations":   _f("Reservations", GROUP_FRONT_OFFICE, requires=["roommaster"]),
    "housekeeping":   _f(
        "Housekeeping", GROUP_FRONT_OFFICE, requires=["livegrid"],
        degrades=[
            "livegrid_full_states",   # L1: collapse cleaning/inspected → occupied/vacant
            "checkout_marks_dirty",   # L2: checkout releases VACANT_CLEAN not VACANT_DIRTY
            "roommove_marks_dirty",   # L3: same on room move
            "frontdesk_request_clean",  # L4: hide the Request-cleaning button
            "housekeeping_alerts",    # L5: suppress module:"housekeeping" alerts
            "minibar_to_folio",       # L6: relocate / disable
            "lost_and_found",         # L7: relocate / disable
        ],
        note="Turn off for a small hotel where Front Desk handles room readiness itself.",
    ),
    "engineering":    _f("Engineering", GROUP_FRONT_OFFICE, requires=["livegrid"],
                         degrades=["ooo_workflow"]),

    # --- Distribution / revenue (need rooms) ---
    "channel":        _f("Channel Manager", GROUP_DISTRIBUTION, requires=["roommaster"]),
    "booking":        _f("Booking Engine", GROUP_DISTRIBUTION, requires=["roommaster", "reservations"]),
    "revenue":        _f("Revenue Manager", GROUP_DISTRIBUTION, requires=["reservations"]),

    # --- Restaurant & bar (pos is the F&B core; the rest sit on it) ---
    "pos":            _f("Restaurant POS", GROUP_FNB, toggleable=False),
    "menumaster":     _f("Menu Master", GROUP_FNB, requires=["pos"], toggleable=False),
    "tablemaster":    _f("Table Master", GROUP_FNB, requires=["pos"], toggleable=False),
    "barpos":         _f("Bar POS", GROUP_FNB, requires=["pos"],
                         note="Also hidden when Bar mode = combined."),
    "kds":            _f("Kitchen Display", GROUP_FNB, requires=["pos"],
                         degrades=["beo_to_kds"]),  # L12: banquet BEO falls back to printed sheet
    "online":         _f("Online Orders", GROUP_FNB, requires=["pos"],
                         degrades=["aggregator_intake", "qr_ordering"]),

    # --- Store & purchasing ---
    "inventory":      _f("Inventory / Store", GROUP_STORE,
                         degrades=["kot_stock_deduction"]),  # L9: POS still fires, no deduction
    "recipes":        _f("Recipes & BOM", GROUP_STORE, requires=["inventory"],
                         degrades=["kot_stock_deduction"]),  # L10
    "suppliers":      _f("Suppliers", GROUP_STORE),
    "procurement":    _f("Procurement", GROUP_STORE, requires=["inventory", "suppliers"]),
    # Not independently toggleable: /api/purchase-orders/ is OR-gated on
    # ["procurement","pomanage"] (so Finance, who holds pomanage but not
    # procurement, can still reach POs — procurement/views.py:189). A standalone
    # "off" therefore wouldn't actually block the API. Folded under Procurement:
    # disabling Procurement cascades this off and blocks the shared endpoint.
    # (QA finding FC-0022, 2026-07-28.)
    "pomanage":       _f("Purchase Orders", GROUP_STORE, requires=["procurement"], toggleable=False),

    # --- Guests & events ---
    "crm":            _f("Guest CRM & Loyalty", GROUP_GUESTS,
                         degrades=["loyalty_accrual", "feedback_qr"]),  # L16
    "banquets":       _f("Banquets & Events", GROUP_GUESTS,
                         degrades=["beo_to_kds"]),  # L15 cascade covers cateringmaster
    "cateringmaster": _f("Catering Prices", GROUP_GUESTS, requires=["banquets"]),

    # --- Back office ---
    "accounting":     _f("Accounting", GROUP_BACK_OFFICE),
    "hr":             _f("HR & Staff", GROUP_BACK_OFFICE),
    "employees":      _f("Employees", GROUP_BACK_OFFICE, requires=["hr"], toggleable=False),
    "vendors":        _f("Vendors", GROUP_BACK_OFFICE, toggleable=False),
}

# Fill the entitlement flag for each feature from the one canonical mapping.
for _key, _spec in FEATURES.items():
    _spec["entitlement"] = MODULE_ENTITLEMENT.get(_key)


DEFAULT_ENTITLEMENTS = {"hms": True, "restaurant": True, "banquets": True, "rms": True}


def _entitlement_ok(spec, ent):
    flag = spec.get("entitlement")
    if flag is None:
        return True
    return bool(ent.get(flag, True))


def resolve(ent: dict | None = None, cfg: dict | None = None) -> dict:
    """Compute the effective ON/OFF for **every** feature, honouring all three axes:
    entitlement flag, the owner's stored toggle, and every prerequisite (recursively).

    `ent` = the 4-flag entitlement dict (Entitlement.as_dict()); `cfg` = the stored
    per-feature `{key: bool}` overrides. Missing keys default to the spec default (ON).
    A module absent from FEATURES entirely is treated as always-ON (shared service).
    """
    ent = ent or DEFAULT_ENTITLEMENTS
    cfg = cfg or {}
    result: dict[str, bool] = {}

    def on(key, stack=()):
        if key in result:
            return result[key]
        spec = FEATURES.get(key)
        if spec is None:
            return True  # unknown key = shared service, always available
        if key in stack:  # cycle guard (shouldn't happen — graph is a DAG)
            return True
        ok = True
        if not _entitlement_ok(spec, ent):
            ok = False
        elif spec.get("toggleable", True) and not cfg.get(key, spec.get("default", True)):
            ok = False
        else:
            for req in spec.get("requires", []):
                if not on(req, stack + (key,)):
                    ok = False
                    break
        result[key] = ok
        return ok

    for key in FEATURES:
        on(key)
    return result


def feature_on(module: str, ent: dict | None = None, cfg: dict | None = None) -> bool:
    """Is this feature effectively enabled? Modules with no spec are always on."""
    if module not in FEATURES:
        return True
    return resolve(ent, cfg).get(module, True)


def prerequisites_met(module: str, ent: dict, cfg: dict) -> list:
    """Which of `module`'s prerequisites are currently OFF (blockers to enabling it).
    Empty list = it can be turned on."""
    spec = FEATURES.get(module)
    if not spec:
        return []
    res = resolve(ent, cfg)
    return [req for req in spec.get("requires", []) if not res.get(req, True)]


def dependents(module: str) -> set:
    """Every feature that (transitively) requires `module` — the set that must
    cascade OFF when `module` is turned off."""
    out = set()
    frontier = {module}
    changed = True
    while changed:
        changed = False
        for key, spec in FEATURES.items():
            if key in out:
                continue
            if frontier & set(spec.get("requires", [])):
                out.add(key)
                frontier.add(key)
                changed = True
    return out


def apply_toggle(cfg: dict, module: str, enabled: bool, ent: dict) -> dict:
    """Return a new config with `module` set, cascading as needed:
      * enabling → also enable any OFF prerequisites (so the choice always sticks);
      * disabling → also disable every dependent (never leave a dangling child).
    Raises ValueError if `module` isn't a toggleable feature."""
    spec = FEATURES.get(module)
    if spec is None or not spec.get("toggleable", True):
        raise ValueError(f"'{module}' is not a toggleable feature")
    new = dict(cfg)
    if enabled:
        new[module] = True
        for req in spec.get("requires", []):
            req_spec = FEATURES.get(req)
            if req_spec and req_spec.get("toggleable", True):
                new[req] = True
    else:
        new[module] = False
        for dep in dependents(module):
            if FEATURES[dep].get("toggleable", True):
                new[dep] = False
    return new


def feature_model() -> list:
    """Serializable description of every feature — consumed by the frontend
    (`GET /auth/feature-model/`) so the client stops hand-mirroring the map."""
    return [
        {
            "key": key,
            "label": spec["label"],
            "group": spec["group"],
            "entitlement": spec["entitlement"],
            "requires": spec["requires"],
            "toggleable": spec["toggleable"],
            "default": spec["default"],
            "note": spec["note"],
        }
        for key, spec in FEATURES.items()
    ]


# --- DB accessors (lazy Property import, mirroring constants.currency_symbol) ---
def active_config() -> dict:
    """The stored per-feature overrides for the single property (empty pre-setup)."""
    from .models import Property
    prop = Property.objects.select_related("entitlement").first()
    if prop and hasattr(prop, "entitlement"):
        return getattr(prop.entitlement, "features", None) or {}
    return {}


def active_features() -> dict:
    """Effective ON/OFF for every feature for the live property — the runtime
    equivalent of permissions.active_entitlements()."""
    from .models import Property
    prop = Property.objects.select_related("entitlement").first()
    if prop and hasattr(prop, "entitlement"):
        ent = prop.entitlement.as_dict()
        cfg = getattr(prop.entitlement, "features", None) or {}
    else:
        ent, cfg = DEFAULT_ENTITLEMENTS, {}
    return resolve(ent, cfg)


def is_enabled(module: str) -> bool:
    """Runtime check for degradation seams: `if not is_enabled("housekeeping"): ...`."""
    if module not in FEATURES:
        return True
    return active_features().get(module, True)
