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
from apps.crm.models import Customer
from apps.pos.models import Category, MenuItem, Table
from apps.reservations.models import Reservation
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
        for username, first, last, role, is_staff in people:
            u, created = User.objects.get_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "role": role,
                "is_staff": is_staff or role in (ROLE_MD, ROLE_GM),
                "is_superuser": role == ROLE_MD,
                "email": f"{username}@hearth.example",
            })
            u.first_name, u.last_name, u.role = first, last, role
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
