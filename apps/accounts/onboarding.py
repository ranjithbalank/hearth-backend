"""First-run setup: what is still missing, and the starter data that shortcuts it.

Onboarding used to be two fields and a Finish button, which left the owner
looking at an app where every operational screen was empty — no room types, no
menu, no tables. Nobody types two hundred dishes to evaluate software, so this
module does two things:

  checklist()    what still needs doing, computed from live rows rather than a
                 stored "step 4 of 9" counter. It stays honest when the owner
                 does things out of order, imports a spreadsheet instead, or
                 deletes something later.

  apply_packs()  opinionated starter data per business shape, idempotent, that
                 the wizard offers as a shortcut. Everything it writes is
                 ordinary editable data — no pack marker rows, nothing magic —
                 so "apply then edit" and "type it yourself" converge on the
                 same place.

Both are scoped to the licence: a restaurant-only install is never offered room
types, and a hotel-only one is never offered a menu.

The money figures and GST slabs here are *defaults to edit*, not tax advice —
they exist so the first screen isn't blank. Tariff-linked room GST and the 5%
vs 18% restaurant question depend on the customer's own registration.
"""
from decimal import Decimal

# --- Starter packs -----------------------------------------------------------
# (name, sort, [(dish, price, diet)]) — prices in INR, deliberately mid-market.

_SOUTH_INDIAN = [
    ("Tiffin", 1, [
        ("Idli (2 pcs)", 40, "veg"), ("Medu Vada (2 pcs)", 45, "veg"),
        ("Masala Dosa", 80, "veg"), ("Ghee Roast Dosa", 110, "veg"),
        ("Ven Pongal", 70, "veg"), ("Rava Upma", 55, "veg"),
    ]),
    ("Rice & Biryani", 2, [
        ("Curd Rice", 70, "veg"), ("Lemon Rice", 70, "veg"),
        ("Veg Biryani", 160, "veg"), ("Chicken Biryani", 220, "nonveg"),
        ("Mutton Biryani", 280, "nonveg"),
    ]),
    ("Curries", 3, [
        ("Sambar", 50, "veg"), ("Kara Kuzhambu", 90, "veg"),
        ("Paneer Butter Masala", 190, "veg"), ("Chicken Chettinad", 220, "nonveg"),
        ("Meen Kuzhambu (Fish)", 240, "nonveg"),
    ]),
    ("Breads", 4, [
        ("Chapati", 25, "veg"), ("Parotta", 30, "veg"),
        ("Appam", 35, "veg"), ("Poori (2 pcs)", 60, "veg"),
    ]),
    ("Beverages", 5, [
        ("Filter Coffee", 30, "veg"), ("Masala Chai", 25, "veg"),
        ("Buttermilk", 30, "veg"), ("Fresh Lime Soda", 50, "veg"),
    ]),
    ("Desserts", 6, [
        ("Gulab Jamun", 60, "veg"), ("Semiya Payasam", 70, "veg"),
        ("Rava Kesari", 60, "veg"),
    ]),
]

_MULTICUISINE = [
    ("Starters", 1, [
        ("Paneer Tikka", 240, "veg"), ("Veg Manchurian", 200, "veg"),
        ("Chicken 65", 260, "nonveg"), ("Fish Amritsari", 300, "nonveg"),
    ]),
    ("Main Course", 2, [
        ("Dal Makhani", 220, "veg"), ("Kadai Paneer", 260, "veg"),
        ("Butter Chicken", 340, "nonveg"), ("Mutton Rogan Josh", 380, "nonveg"),
    ]),
    ("Rice & Noodles", 3, [
        ("Jeera Rice", 160, "veg"), ("Veg Fried Rice", 190, "veg"),
        ("Hakka Noodles", 190, "veg"), ("Chicken Fried Rice", 230, "nonveg"),
    ]),
    ("Breads", 4, [
        ("Tandoori Roti", 40, "veg"), ("Butter Naan", 60, "veg"),
        ("Garlic Naan", 80, "veg"), ("Laccha Paratha", 70, "veg"),
    ]),
    ("Desserts", 5, [
        ("Gulab Jamun", 90, "veg"), ("Ice Cream", 80, "veg"),
        ("Brownie & Ice Cream", 160, "egg"),
    ]),
    ("Beverages", 6, [
        ("Fresh Lime Soda", 90, "veg"), ("Masala Chai", 60, "veg"),
        ("Cold Coffee", 140, "veg"),
    ]),
]

# Alcohol sits OUTSIDE GST — it is state excise/VAT, which varies by state — so
# the liquor lines go in at 0 and the pack note tells the owner to set their own
# rate. Mixers and mocktails are ordinary F&B and keep the 5% slab.
_BAR = [
    ("Beer", 1, 0, [
        ("Kingfisher Premium", 250, "veg"), ("Bira 91 White", 280, "veg"),
        ("Heineken", 320, "veg"),
    ]),
    ("Whisky", 2, 0, [
        ("Blenders Pride (30ml)", 260, "veg"), ("Johnnie Walker Black (30ml)", 650, "veg"),
        ("Glenlivet 12 (30ml)", 850, "veg"),
    ]),
    ("Cocktails", 3, 0, [
        ("Mojito", 350, "veg"), ("Long Island Iced Tea", 480, "veg"),
        ("Cosmopolitan", 420, "veg"),
    ]),
    ("Mocktails", 4, 5, [
        ("Virgin Mojito", 220, "veg"), ("Blue Lagoon", 240, "veg"),
    ]),
    ("Soft Drinks", 5, 5, [
        ("Coke", 60, "veg"), ("Soda", 40, "veg"), ("Tonic Water", 90, "veg"),
    ]),
]

# (code, name, base rate, max occupancy, GST slab). The 12 vs 18 split follows
# the ₹7,500 tariff line, which is why the Suite differs.
_ROOM_TYPES = [
    ("STD", "Standard", 3500, 2, 12),
    ("DLX", "Deluxe", 5500, 3, 12),
    ("STE", "Suite", 9500, 4, 18),
]

# (suffix, premium over base, inclusions) — the standard Indian plan ladder.
_RATE_PLANS = [
    ("Room Only (EP)", 0, "Room only"),
    ("Bed & Breakfast (CP)", 800, "Room + breakfast"),
    ("Half Board (MAP)", 1800, "Room + breakfast + one meal"),
]

_GST_SLABS = [
    ("Rooms (low)", "12", "996311", "rooms"),
    ("Rooms (high)", "18", "996311", "rooms"),
    ("F&B", "5", "996331", "fnb"),
    ("Banquet", "18", "996334", "banquet"),
]

PACKS = {
    "gst_india": {
        "label": "Indian GST slabs",
        "detail": "Room 12%/18% either side of the ₹7,500 tariff line, F&B 5%, banquet 18%.",
        "needs": None,
        "note": "Confirm against your own GST registration before you bill.",
    },
    "hotel_rooms": {
        "label": "Room types & rate plans",
        "detail": "Standard, Deluxe and Suite, each with Room Only / B&B / Half Board.",
        "needs": "hms",
        "note": "Tariffs are mid-market placeholders — edit them in Room Master.",
    },
    "menu_south_indian": {
        "label": "South Indian menu",
        "detail": "6 categories, 27 dishes — tiffin, biryani, curries, breads, beverages.",
        "needs": "restaurant",
    },
    "menu_multicuisine": {
        "label": "Multi-cuisine menu",
        "detail": "6 categories, 22 dishes — starters, mains, rice & noodles, breads.",
        "needs": "restaurant",
    },
    "bar_menu": {
        "label": "Bar menu",
        "detail": "Beer, whisky, cocktails, mocktails and soft drinks.",
        "needs": "restaurant",
        "note": "Liquor is state excise/VAT, not GST — the lines go in at 0%, set your state's rate.",
    },
    "tables": {
        "label": "Restaurant tables",
        "detail": "A numbered floor plan you can rename and rearrange.",
        "needs": "restaurant",
        "option": {"key": "tables_count", "label": "How many tables?", "default": 12},
    },
    "rooms": {
        "label": "Rooms",
        "detail": "Numbered by floor (101, 102… 201, 202…) and spread across your room types.",
        "needs": "hms",
        "option": {"key": "rooms_count", "label": "How many rooms?", "default": 20},
        "note": "Needs room types — applied together, they come out consistent.",
    },
}


def pack_catalog(prop):
    """The packs worth offering this install, given what it is licensed for."""
    ent = prop.entitlement
    out = []
    for key, spec in PACKS.items():
        needs = spec["needs"] if "needs" in spec else None
        if needs and not getattr(ent, needs, False):
            continue
        out.append({"key": key, **{k: v for k, v in spec.items() if k != "needs"}})
    return out


def apply_packs(prop, keys, options=None):
    """Write the requested packs. Idempotent — get_or_create throughout, so
    re-running never doubles a menu, and a pack applied over hand-entered data
    fills the gaps instead of fighting it."""
    from apps.pos.models import Category, MenuItem, Table
    from apps.rooms.models import RatePlan, Room, RoomType
    from apps.tax.models import GstSlab

    options = options or {}
    ent = prop.entitlement
    branch = prop.branches.first()
    created = {}

    def bump(kind, n):
        if n:
            created[kind] = created.get(kind, 0) + n

    def menu(spec, is_bar=False):
        cats = items = 0
        for entry in spec:
            if is_bar:
                name, sort, gst, dishes = entry
            else:
                name, sort, dishes = entry
                gst = 5
            cat, made = Category.objects.get_or_create(
                name=name, is_bar=is_bar, defaults={"sort_order": sort})
            cats += int(made)
            for dish, price, diet in dishes:
                _, made = MenuItem.objects.get_or_create(
                    name=dish, category=cat,
                    defaults={"price": Decimal(price), "gst_rate": Decimal(gst),
                              "diet": diet, "station": "bar" if is_bar else "kitchen",
                              "bar_menu": is_bar},
                )
                items += int(made)
        bump("categories", cats)
        bump("menu items", items)

    for key in keys:
        spec = PACKS.get(key)
        if spec is None:
            continue
        needs = spec["needs"] if "needs" in spec else None
        if needs and not getattr(ent, needs, False):
            continue  # not licensed for it — silently skip rather than 400

        if key == "gst_india":
            n = 0
            for name, rate, hsn, applies in _GST_SLABS:
                _, made = GstSlab.objects.get_or_create(
                    name=name, defaults={"rate": Decimal(rate), "hsn_sac": hsn,
                                         "applies_to": applies})
                n += int(made)
            bump("GST slabs", n)

        elif key == "hotel_rooms":
            types = plans = 0
            for code, name, rate, occ, slab in _ROOM_TYPES:
                rt, made = RoomType.objects.get_or_create(
                    code=code, defaults={"name": name, "base_rate": Decimal(rate),
                                         "max_occupancy": occ, "gst_slab": Decimal(slab)})
                types += int(made)
                for suffix, premium, inclusions in _RATE_PLANS:
                    _, made = RatePlan.objects.get_or_create(
                        name=f"{name} — {suffix}", room_type=rt,
                        defaults={"rate": Decimal(rate + premium), "inclusions": inclusions})
                    plans += int(made)
            bump("room types", types)
            bump("rate plans", plans)

        elif key == "rooms":
            count = _as_int(options.get("rooms_count"), 20)
            types = list(RoomType.objects.order_by("base_rate"))
            if not types:
                continue  # nothing to hang them on; apply hotel_rooms first
            n = 0
            for i in range(count):
                floor = i // 10 + 1
                number = f"{floor}{i % 10 + 1:02d}"
                # Cheapest type on the low floors, suites at the top — the way
                # a real property is usually numbered.
                rt = types[min(floor - 1, len(types) - 1)]
                _, made = Room.objects.get_or_create(
                    number=number, branch="Main", location=branch,
                    defaults={"room_type": rt, "floor": floor},
                )
                n += int(made)
            bump("rooms", n)

        elif key == "menu_south_indian":
            menu(_SOUTH_INDIAN)
        elif key == "menu_multicuisine":
            menu(_MULTICUISINE)
        elif key == "bar_menu":
            menu(_BAR, is_bar=True)

        elif key == "tables":
            count = _as_int(options.get("tables_count"), 12)
            n = 0
            for i in range(1, count + 1):
                _, made = Table.objects.get_or_create(
                    name=f"T{i}", location=branch,
                    defaults={"section": "Main", "seats": 2 if i % 3 == 0 else 4},
                )
                n += int(made)
            bump("tables", n)

    return created


def _as_int(value, default, lo=1, hi=500):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# --- The setup checklist -----------------------------------------------------

def checklist(prop):
    """Every setup step with its live done/not-done state.

    `required` marks the minimum to actually operate — the owner can start
    taking bookings or orders the moment those are met and leave the rest for
    later, which is the whole point of not making this a blocking wizard.
    """
    from apps.pos.models import Category, MenuItem, Table
    from apps.rooms.models import RatePlan, Room, RoomType
    from apps.tax.models import GstSlab

    from .models import Branch, User

    ent = prop.entitlement
    steps = []

    def step(key, label, hint, done, where, required=False):
        steps.append({"key": key, "label": label, "hint": hint, "where": where,
                      "done": bool(done), "required": required})

    step("branch", "Confirm your location",
         "Address, city and the GSTIN you bill under.",
         Branch.objects.filter(property=prop).exists(),
         "/config/branches", required=True)
    step("tax", "Check your GST slabs",
         "Standard Indian slabs are pre-filled — confirm they match your registration.",
         GstSlab.objects.exists(), "/config/gst", required=True)

    if ent.hms:
        step("roomtypes", "Add your room types",
             "Standard, Deluxe, Suite — a base tariff and GST slab each.",
             RoomType.objects.exists(), "/config/rooms", required=True)
        step("rooms", "Add your rooms",
             "Room numbers by floor. The live grid and every booking hang off these.",
             Room.objects.exists(), "/config/rooms", required=True)
        step("rateplans", "Set up rate plans",
             "Room Only, Bed & Breakfast, Half Board — per room type.",
             RatePlan.objects.exists(), "/config/rooms")

    if ent.restaurant:
        step("menucats", "Build your menu categories",
             "Starters, Main Course, Beverages — the tabs across your POS.",
             Category.objects.filter(is_bar=False).exists(), "/config/menu", required=True)
        step("menuitems", "Add your dishes",
             "Name, price and GST rate. Import a spreadsheet if you already have one.",
             MenuItem.objects.exists(), "/config/menu", required=True)
        step("tables", "Lay out your tables",
             "Table names and covers per section — your POS floor plan.",
             Table.objects.exists(), "/config/tables", required=True)

    # The chain the rest of Hearth enforces: the owner appoints an admin (or a
    # manager), who staffs up everyone below. Asked separately from "invite your
    # team" because it's the account that unblocks the rest — a property whose
    # only login is the owner has nobody who can actually run it. Not marked
    # `required`: it shouldn't stand between anyone and a first look around.
    from .rbac import role_rank
    has_deputy = any(role_rank(u.role) >= 2
                     for u in User.objects.exclude(is_superuser=True).only("role"))
    step("admin", "Appoint your admin",
         "The person who runs the property day to day and adds everyone else.",
         has_deputy, "/settings?section=users")
    step("staff", "Invite your team",
         "Everyone past the owner account, each with a role.",
         User.objects.exclude(is_superuser=True).exists(), "/settings?section=users")

    total = len(steps)
    done = sum(1 for s in steps if s["done"])
    blocking = [s["key"] for s in steps if s["required"] and not s["done"]]
    return {
        "steps": steps,
        "done": done,
        "total": total,
        "pct": round(done * 100 / total) if total else 100,
        "blocking": blocking,
        "ready_to_operate": not blocking,
    }
