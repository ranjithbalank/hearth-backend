"""One comprehensive end-to-end journey covering the core cross-module flows:
setup → walk-in check-in → POS order → post-to-room → check-out → dashboard.
This locks the integration seams (POS→folio, room charges, GST, KPIs)."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Entitlement, Property, User
from apps.pos.models import Category, MenuItem, Table
from apps.rooms.models import Room, RoomType


class GuestJourneyE2E(TestCase):
    def setUp(self):
        prop = Property.objects.create(name="Hearth Test", edition="both", setup_done=True,
                                       business_date=date.today(), gstin="29ABCDE1234F1Z5")
        Entitlement.objects.create(property=prop)
        self.gm = User.objects.create_user(username="gm", password="Tk9$mZ2pQw!7",
                                           role="General Manager")
        self.client = APIClient()
        self.client.force_authenticate(self.gm)
        self.rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("6500"))
        self.room = Room.objects.create(number="201", room_type=self.rt, status=Room.VACANT_CLEAN)
        cat = Category.objects.create(name="Main")
        self.item = MenuItem.objects.create(name="Biryani", category=cat,
                                            price=Decimal("400"), gst_rate=Decimal("5"))
        Table.objects.create(name="A1", section="AC")

    def test_full_journey(self):
        c = self.client
        # 1. Walk-in -> reservation
        resv = c.post(reverse("reservation-walkin"),
                      {"guest_name": "Journey Guest", "mobile": "9000000009",
                       "room_type": "DLX", "nights": 1}, format="json").data
        # 2. Check in -> folio + room occupied
        folio = c.post(reverse("checkin-list"),
                       {"reservation": resv["id"], "room": self.room.id,
                        "id_type": "Passport", "id_number": "P1", "guest_type": "individual",
                        "mobile": "+91 9000000000"}).data
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.OCCUPIED)
        # 3. POS Room-channel order -> fire KOT -> post to the guest's own folio.
        # (Table orders can no longer post to rooms — segregation loophole fix.)
        order = c.post(reverse("order-list"), {"mode": "room", "folio": folio["id"]},
                       format="json").data
        c.post(reverse("order-add-item", args=[order["id"]]),
               {"menu_item": self.item.id, "qty": 2}, format="json")
        c.post(reverse("order-fire-kot", args=[order["id"]]))
        c.post(reverse("order-post-to-room", args=[order["id"]]), {}, format="json")
        # folio now carries the F&B charge
        f = c.get(reverse("folio-detail", args=[folio["id"]])).data
        self.assertTrue(any(line["kind"] == "fnb" for line in f["lines"]))
        # 4. Check out -> posts room night, settles, releases room, issues invoice
        res = c.post(reverse("folio-checkout", args=[folio["id"]]),
                     {"payments": [{"tender": "Card", "amount": f["balance"]}]}, format="json").data
        self.assertEqual(res["status"], "settled")
        self.assertTrue(res["invoice_no"])
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.VACANT_DIRTY)
        # 5. Dashboard reflects the revenue
        d = c.get(reverse("reports-dashboard")).data
        self.assertGreater(Decimal(d["rooms"]["room_revenue"]), 0)
        self.assertGreater(Decimal(d["fnb"]["fnb_sales"]), 0)
