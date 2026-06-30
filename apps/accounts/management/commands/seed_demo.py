"""Seed Hearth with demo data so every Phase-1 screen renders immediately.

Idempotent: safe to run repeatedly. Loads the prototype's sample data
(property + Both edition, 5 role users, rooms, menu, tables, arrivals, customers).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.constants import (
    ROLE_CASHIER,
    ROLE_FRONT_OFFICE,
    ROLE_GM,
    ROLE_HOUSEKEEPING,
    ROLE_MD,
)
from apps.accounts.models import Entitlement, Property, User
from apps.banquets.models import Event, FunctionSpace
from apps.channel.models import Channel, ChannelRate
from apps.crm.models import Customer
from apps.hr.models import Employee
from apps.inventory.models import Ingredient
from apps.matreq.models import MaterialRequest, MaterialRequestLine
from apps.housekeeping.models import LostFoundItem
from apps.pos.models import (
    AddOn, AddOnGroup, Category, ChannelPrice, ComboComponent, Coupon, MenuItem,
    MenuSchedule, Table, Variant,
)
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier, Vendor
from apps.tax.models import GstSlab
from apps.recipes.models import Recipe, RecipeLine
from apps.reservations.models import Reservation
from apps.revenue.models import RateRecommendation
from apps.rooms.models import RatePlan, Room, RoomType

PASSWORD = "hearth123"


class Command(BaseCommand):
    help = "Load demo data for Hearth (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding Hearth demo data…")
        prop = self._property()
        self._users()
        room_types = self._room_types()
        self._rooms(room_types)
        self._reservations(room_types)
        self._restaurant()
        self._customers()
        self._distribution(room_types)
        self._supply_chain()
        self._banquets()
        self._hr()
        self._masters()
        self.stdout.write(self.style.SUCCESS(
            f"Done. Property '{prop.name}' [{prop.edition}]. "
            f"Logins: md / gm / frontoffice / cashier / housekeeping (pwd: {PASSWORD})"
        ))

    def _property(self):
        prop, _ = Property.objects.get_or_create(
            id=1, defaults={"name": "Hearth Grand", "gstin": "29ABCDE1234F1Z5"}
        )
        prop.name = "Hearth Grand"
        prop.edition = "both"
        prop.setup_done = True
        if not prop.business_date:
            prop.business_date = date.today()
        prop.save()
        ent, _ = Entitlement.objects.get_or_create(property=prop)
        ent.hms = ent.restaurant = ent.banquets = ent.rms = True
        ent.save()
        return prop

    def _users(self):
        people = [
            ("md", "Karthik", "Subramanian", ROLE_MD, True),
            ("gm", "Meera", "Rao", ROLE_GM, True),
            ("frontoffice", "Anil", "Kumar", ROLE_FRONT_OFFICE, False),
            ("cashier", "Priya", "Nair", ROLE_CASHIER, False),
            ("housekeeping", "Sunita", "Pal", ROLE_HOUSEKEEPING, False),
        ]
        # Per-user discount caps + manager passcodes (BRD FR-USR-004/006).
        caps = {
            "cashier": ("percent", 10),   # cashier capped at 10%
            "frontoffice": ("percent", 15),
        }
        passcodes = {"md": "1234", "gm": "4321"}
        for username, first, last, role, is_staff in people:
            u, created = User.objects.get_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "role": role,
                "is_staff": is_staff or role in (ROLE_MD, ROLE_GM),
                "is_superuser": role == ROLE_MD,
                "email": f"{username}@hearth.example",
            })
            u.first_name, u.last_name, u.role = first, last, role
            cap = caps.get(username)
            if cap:
                u.discount_cap_type, u.discount_cap_value = cap
            u.passcode = passcodes.get(username, "")
            u.set_password(PASSWORD)
            u.save()

    def _room_types(self):
        data = [
            ("STD", "Standard", 4500, 2, 12),
            ("DLX", "Deluxe", 6500, 3, 12),
            ("STE", "Suite", 9500, 4, 18),
        ]
        out = {}
        for code, name, rate, occ, slab in data:
            rt, _ = RoomType.objects.get_or_create(code=code, defaults={
                "name": name, "base_rate": Decimal(rate),
                "max_occupancy": occ, "gst_slab": Decimal(slab),
            })
            RatePlan.objects.get_or_create(
                name=f"{name} — Room Only", room_type=rt,
                defaults={"rate": Decimal(rate), "inclusions": "Room only"},
            )
            RatePlan.objects.get_or_create(
                name=f"{name} — B&B", room_type=rt,
                defaults={"rate": Decimal(rate) + 800, "inclusions": "Room + breakfast"},
            )
            out[code] = rt
        return out

    def _rooms(self, room_types):
        if Room.objects.exists():
            return
        codes = ["STD", "STD", "STD", "DLX", "DLX", "STE"]
        for floor in range(1, 7):
            for i, code in enumerate(codes, start=1):
                number = f"{floor}{i:02d}"
                Room.objects.get_or_create(
                    branch="Main", number=number,
                    defaults={
                        "room_type": room_types[code],
                        "floor": floor,
                        "status": Room.VACANT_CLEAN if i % 4 else Room.VACANT_DIRTY,
                    },
                )

    def _reservations(self, room_types):
        if Reservation.objects.exists():
            return
        today = date.today()
        samples = [
            ("Rahul Mehta", "STD", "ota", 4500, True, 4500),
            ("Lakshmi Iyer", "DLX", "direct", 6500, False, 0),
            ("John Carter", "STE", "booking", 9500, True, 9500),
            ("Sneha Gupta", "STD", "walkin", 4500, False, 0),
        ]
        for name, code, source, rate, prepaid, deposit in samples:
            Reservation.objects.create(
                guest_name=name, room_type=room_types[code], source=source,
                checkin_date=today, checkout_date=today + timedelta(days=2),
                nights=2, rate=Decimal(rate), prepaid=prepaid,
                deposit=Decimal(deposit), status=Reservation.BOOKED,
            )

    def _restaurant(self):
        cats = {}
        for i, name in enumerate(["Starters", "Main Course", "Breads", "Rice & Biryani",
                                  "Desserts", "Beverages"]):
            c, _ = Category.objects.get_or_create(name=name, defaults={"sort_order": i})
            cats[name] = c
        items = [
            ("Paneer Tikka", "Starters", 320, "veg"),
            ("Chicken 65", "Starters", 360, "nonveg"),
            ("Dal Makhani", "Main Course", 290, "veg"),
            ("Butter Chicken", "Main Course", 420, "nonveg"),
            ("Garlic Naan", "Breads", 70, "veg"),
            ("Tandoori Roti", "Breads", 35, "veg"),
            ("Veg Biryani", "Rice & Biryani", 280, "veg"),
            ("Chicken Biryani", "Rice & Biryani", 360, "nonveg"),
            ("Gulab Jamun", "Desserts", 120, "veg"),
            ("Masala Chai", "Beverages", 60, "veg"),
            ("Fresh Lime Soda", "Beverages", 90, "veg"),
        ]
        for name, cat, price, diet in items:
            MenuItem.objects.get_or_create(name=name, defaults={
                "category": cats[cat], "price": Decimal(price),
                "gst_rate": Decimal("5"), "diet": diet,
                "short_code": "".join(w[0] for w in name.split()).upper(),
            })
        if not Table.objects.exists():
            for sec, count in [("AC", 6), ("Non-AC", 4), ("Outdoor", 4)]:
                for n in range(1, count + 1):
                    Table.objects.create(
                        name=f"{sec[0]}{n}", section=sec,
                        seats=4 if n % 2 else 2,
                    )
        # QR token per table (embedded in the table's QR code) for guest ordering.
        for t in Table.objects.filter(qr_token=""):
            t.qr_token = f"QR{t.id:04d}"
            t.save(update_fields=["qr_token"])
        Coupon.objects.get_or_create(code="WELCOME10", defaults={
            "kind": "percent", "value": Decimal("10"), "min_bill": Decimal("500")})
        Coupon.objects.get_or_create(code="FLAT100", defaults={
            "kind": "fixed", "value": Decimal("100"), "min_bill": Decimal("400"),
            "usage_limit": 100})

        # Variants + add-ons on a couple of items (BRD 5.11).
        biry = MenuItem.objects.filter(name="Chicken Biryani").first()
        if biry and not biry.variants.exists():
            Variant.objects.create(menu_item=biry, name="Half", price=Decimal("240"), short_code="H")
            Variant.objects.create(menu_item=biry, name="Full", price=Decimal("360"), short_code="F")
            g = AddOnGroup.objects.create(menu_item=biry, name="Add-ons", min_select=0, max_select=2)
            AddOn.objects.create(group=g, name="Extra Raita", price=Decimal("40"))
            AddOn.objects.create(group=g, name="Boiled Egg", price=Decimal("25"))
        tikka = MenuItem.objects.filter(name="Paneer Tikka").first()
        if tikka and not tikka.addon_groups.exists():
            spice = AddOnGroup.objects.create(menu_item=tikka, name="Spice level", min_select=1, max_select=1)
            for lvl in ["Mild", "Medium", "Spicy"]:
                AddOn.objects.create(group=spice, name=lvl, price=Decimal("0"))

        # Combo / meal (FR-MNU-006): bundles components at a combo price.
        combos = MenuItem.objects.filter(name="Veg Thali Combo").first()
        if not combos:
            combo_cat, _ = Category.objects.get_or_create(name="Combos", defaults={"sort_order": 7})
            combos = MenuItem.objects.create(
                name="Veg Thali Combo", category=combo_cat, price=Decimal("420"),
                gst_rate=Decimal("5"), diet="veg", short_code="VTC")
            for comp_name, q in [("Dal Makhani", 1), ("Garlic Naan", 2), ("Veg Biryani", 1),
                                 ("Gulab Jamun", 1)]:
                comp = MenuItem.objects.filter(name=comp_name).first()
                if comp:
                    ComboComponent.objects.create(combo=combos, component=comp, qty=q)

        # Per-channel pricing (FR-MNU-003): delivery/online carry a ~12% uplift.
        for mi in MenuItem.objects.all():
            for chan, factor in [("delivery", Decimal("1.12")), ("online", Decimal("1.12"))]:
                ChannelPrice.objects.get_or_create(
                    menu_item=mi, channel=chan,
                    defaults={"price": (mi.price * factor).quantize(Decimal("1"))})

        # Happy-hour (FR-MNU-011): beverages cheaper 16:00–19:00.
        from datetime import time as _t
        for bev in ["Masala Chai", "Fresh Lime Soda"]:
            mi = MenuItem.objects.filter(name=bev).first()
            if mi and not mi.schedules.exists():
                MenuSchedule.objects.create(menu_item=mi, name="Happy hour",
                                            start_time=_t(16, 0), end_time=_t(19, 0),
                                            price=(mi.price * Decimal("0.7")).quantize(Decimal("1")))

    def _customers(self):
        data = [
            ("Rahul Mehta", "9876500001", "guest", False, 0),
            ("Acme Corp", "9876500002", "corporate", True, 18500),
            ("TravelWings Agency", "9876500003", "agent", True, 42000),
            ("Lakshmi Iyer", "9876500004", "guest", False, 0),
        ]
        for name, mobile, ctype, btc, outstanding in data:
            Customer.objects.get_or_create(mobile=mobile, defaults={
                "name": name, "customer_type": ctype, "btc_enabled": btc,
                "outstanding": Decimal(outstanding),
                "gstin": "29AACCA1234R1Z9" if ctype == "corporate" else "",
            })

    def _distribution(self, room_types):
        channels = [
            ("Booking.com", 15), ("Expedia", 18), ("Agoda", 17),
            ("MakeMyTrip", 16), ("Airbnb", 14), ("Google", 12),
        ]
        chans = []
        for name, comm in channels:
            c, _ = Channel.objects.get_or_create(name=name, defaults={"commission_pct": comm})
            chans.append(c)
        # Seed ARI; introduce one deliberate parity breach on STD for the demo.
        for code, rt in room_types.items():
            for i, ch in enumerate(chans):
                rate = rt.base_rate + (200 if code == "STD" and ch.name == "Expedia" else 0)
                ChannelRate.objects.get_or_create(
                    channel=ch, room_type=rt,
                    defaults={"rate": rate, "availability": 4},
                )
        if not RateRecommendation.objects.exists():
            recs = [
                ("DLX", 6500, 7200, "High weekend demand; +10.8%", 78),
                ("STE", 9500, 8800, "Soft midweek pickup; -7.4%", 38),
                ("STD", 4500, 4900, "Competitor parity gap; +8.9%", 64),
            ]
            for code, cur, rec, reason, idx in recs:
                RateRecommendation.objects.create(
                    room_type=room_types[code], current_rate=Decimal(cur),
                    recommended_rate=Decimal(rec), reason=reason, demand_index=idx,
                )

    def _supply_chain(self):
        ings = [
            ("Paneer", "kg", 12, 5, 320), ("Chicken", "kg", 18, 8, 240),
            ("Basmati Rice", "kg", 40, 15, 95), ("Onion", "kg", 25, 10, 35),
            ("Tomato", "kg", 20, 8, 40), ("Wheat Flour", "kg", 50, 20, 45),
            ("Butter", "kg", 8, 4, 480), ("Milk", "l", 30, 12, 60),
            ("Cooking Oil", "l", 35, 15, 140), ("Sugar", "kg", 22, 10, 48),
        ]
        ing_by_name = {}
        for name, unit, stock, reorder, cost in ings:
            i, _ = Ingredient.objects.get_or_create(name=name, defaults={
                "unit": unit, "current_stock": Decimal(stock),
                "reorder_level": Decimal(reorder), "unit_cost": Decimal(cost),
                "category": "Kitchen",
            })
            ing_by_name[name] = i
        # Drive one item below par for the low-stock demo.
        if "Butter" in ing_by_name:
            b = ing_by_name["Butter"]; b.current_stock = Decimal("3"); b.save()

        # Recipes (BOM) for a few menu items.
        recipes = {
            "Paneer Tikka": [("Paneer", 0.2), ("Onion", 0.05), ("Cooking Oil", 0.02)],
            "Butter Chicken": [("Chicken", 0.25), ("Butter", 0.05), ("Tomato", 0.1)],
            "Dal Makhani": [("Butter", 0.03), ("Tomato", 0.05), ("Onion", 0.04)],
            "Chicken Biryani": [("Chicken", 0.2), ("Basmati Rice", 0.18), ("Onion", 0.06)],
            "Garlic Naan": [("Wheat Flour", 0.12), ("Butter", 0.01)],
        }
        for item_name, lines in recipes.items():
            mi = MenuItem.objects.filter(name=item_name).first()
            if not mi or hasattr(mi, "recipe"):
                continue
            r = Recipe.objects.create(menu_item=mi)
            for ing_name, qty in lines:
                if ing_name in ing_by_name:
                    RecipeLine.objects.create(recipe=r, ingredient=ing_by_name[ing_name],
                                              qty=Decimal(str(qty)))

        # Suppliers + a pending and an approved PO.
        suppliers = [
            ("Fresh Farms Pvt Ltd", "29AAACF1234A1Z1", "Veg & dairy", 1, 4.6),
            ("Metro Cash & Carry", "29AAACM5678B1Z2", "Dry goods", 2, 4.3),
            ("Coastal Meats", "29AAACC9012C1Z3", "Poultry & meat", 1, 4.8),
        ]
        sup_objs = []
        for name, gstin, terms, lead, rating in suppliers:
            s, _ = Supplier.objects.get_or_create(name=name, defaults={
                "gstin": gstin, "payment_terms": terms,
                "lead_time_days": lead, "rating": Decimal(str(rating)),
            })
            sup_objs.append(s)
        if not PurchaseOrder.objects.exists() and ing_by_name:
            po1 = PurchaseOrder.objects.create(supplier=sup_objs[0], status=PurchaseOrder.PENDING)
            PurchaseOrderLine.objects.create(purchase_order=po1, ingredient=ing_by_name["Butter"],
                                             qty=Decimal("10"), rate=Decimal("480"))
            PurchaseOrderLine.objects.create(purchase_order=po1, ingredient=ing_by_name["Milk"],
                                             qty=Decimal("20"), rate=Decimal("60"))
            po2 = PurchaseOrder.objects.create(supplier=sup_objs[2], status=PurchaseOrder.APPROVED)
            PurchaseOrderLine.objects.create(purchase_order=po2, ingredient=ing_by_name["Chicken"],
                                             qty=Decimal("15"), rate=Decimal("240"))

        if not MaterialRequest.objects.exists() and ing_by_name:
            req = MaterialRequest.objects.create(department="Kitchen", requested_by="Ravi Shah")
            MaterialRequestLine.objects.create(request=req, ingredient=ing_by_name["Onion"], qty=Decimal("5"))
            MaterialRequestLine.objects.create(request=req, ingredient=ing_by_name["Tomato"], qty=Decimal("3"))
            req2 = MaterialRequest.objects.create(department="Bar", requested_by="Priya Nair",
                                                  status=MaterialRequest.APPROVED)
            MaterialRequestLine.objects.create(request=req2, ingredient=ing_by_name["Sugar"], qty=Decimal("2"))

    def _banquets(self):
        spaces = [("Grand Ballroom", 400), ("Garden Lawn", 250), ("Boardroom", 40)]
        sp = {}
        for name, cap in spaces:
            s, _ = FunctionSpace.objects.get_or_create(name=name, defaults={"capacity": cap})
            sp[name] = s
        if not Event.objects.exists():
            from datetime import timedelta
            today = date.today()
            Event.objects.create(space=sp["Grand Ballroom"], title="Sharma Wedding Reception",
                                 host="Sharma Family", event_date=today + timedelta(days=10),
                                 covers=300, package_amount=Decimal("450000"),
                                 deposit=Decimal("100000"), status=Event.CONFIRMED)
            Event.objects.create(space=sp["Garden Lawn"], title="TechCorp Annual Offsite",
                                 host="TechCorp", event_date=today + timedelta(days=20),
                                 covers=150, package_amount=Decimal("220000"),
                                 status=Event.TENTATIVE)

    def _hr(self):
        if Employee.objects.exists():
            return
        staff = [
            ("Anil Kumar", "Front Office", "Front Office Manager", ["M","M","M","O","E","E","M"]),
            ("Sunita Pal", "Housekeeping", "HK Supervisor", ["M","M","M","M","O","M","M"]),
            ("Priya Nair", "F&B", "Cashier", ["E","E","O","E","E","N","N"]),
            ("Ravi Shah", "Kitchen", "Sous Chef", ["M","O","M","M","M","M","O"]),
            ("Deepa Iyer", "Reservations", "Reservations Exec", ["M","M","M","M","M","O","O"]),
        ]
        for name, dept, role, shifts in staff:
            Employee.objects.create(name=name, department=dept, role=role, shifts=shifts)
        if not LostFoundItem.objects.exists():
            LostFoundItem.objects.create(description="Black umbrella", location="Lobby", handler="Anil Kumar")
            LostFoundItem.objects.create(description="Phone charger", location="Room 204", handler="Sunita Pal")

    def _masters(self):
        vendors = [
            ("CoolAir HVAC Services", "Maintenance", "Net 30"),
            ("BrightClean Laundry", "Laundry", "Net 15"),
            ("SecureGuard Pvt Ltd", "Security", "Net 30"),
            ("PestAway Solutions", "Pest control", "Net 45"),
        ]
        for name, cat, terms in vendors:
            Vendor.objects.get_or_create(name=name, defaults={
                "category": cat, "payment_terms": terms,
                "contact": "+91 90000 00000"})
        slabs = [
            ("Rooms (low)", "12", "996311", "rooms"),
            ("Rooms (high)", "18", "996311", "rooms"),
            ("F&B", "5", "996331", "fnb"),
            ("Banquet", "18", "996334", "banquet"),
        ]
        for name, rate, hsn, applies in slabs:
            GstSlab.objects.get_or_create(name=name, defaults={
                "rate": Decimal(rate), "hsn_sac": hsn, "applies_to": applies})
