from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType

from . import services
from .models import Folio, FolioLine

# Tiny valid image data URL (1×1 PNG) for registration-card tests.
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
         "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class FrontOfficeFlowTests(TestCase):
    def setUp(self):
        self.rt = RoomType.objects.create(
            code="DLX", name="Deluxe", base_rate=Decimal("6500"), gst_slab=Decimal("12")
        )
        self.room = Room.objects.create(
            number="201", room_type=self.rt, floor=2, status=Room.VACANT_CLEAN
        )
        self.resv = Reservation.objects.create(
            guest_name="Test Guest", room_type=self.rt,
            checkin_date=date.today(), checkout_date=date.today() + timedelta(days=1),
            nights=1, rate=Decimal("6500"),
        )

    def test_check_in_opens_folio_and_occupies_room(self):
        folio = services.check_in(self.resv, self.room)
        self.room.refresh_from_db()
        self.resv.refresh_from_db()
        self.assertEqual(self.room.status, Room.OCCUPIED)
        self.assertEqual(self.resv.status, Reservation.IN_HOUSE)
        self.assertEqual(folio.status, Folio.OPEN)

    def test_checkin_stores_id_scan_and_signature(self):
        """Registration-card evidence: the wizard's ID scan + signature land on
        the folio, list payloads carry only presence flags, and the images come
        back from the audited /registration/ endpoint."""
        desk = APIClient()
        desk.force_authenticate(User.objects.create_user(
            username="fo", password="Tk9$mZ2pQw!7", role="Front Office"))
        r = desk.post("/api/checkin/", {
            "reservation": self.resv.id, "room": self.room.id,
            "id_type": "Passport", "id_number": "P1234567", "mobile": "9876543210",
            "id_scan": PIXEL, "signature": PIXEL,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        # Folio payload: presence flags only, no blobs.
        self.assertTrue(r.data["has_id_scan"] and r.data["has_signature"])
        self.assertNotIn("id_scan", r.data)
        # The registration endpoint returns the evidence…
        reg = desk.get(f"/api/folios/{r.data['id']}/registration/")
        self.assertEqual(reg.status_code, 200)
        self.assertEqual(reg.data["id_scan"], PIXEL)
        self.assertEqual(reg.data["signature"], PIXEL)
        # …and reading it left an audit entry.
        from apps.accounts.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(
            action="registration_viewed", entity_id=str(r.data["id"])).exists())

    def test_checkin_rejects_non_image_scan(self):
        desk = APIClient()
        desk.force_authenticate(User.objects.create_user(
            username="fo2", password="Tk9$mZ2pQw!7", role="Front Office"))
        r = desk.post("/api/checkin/", {
            "reservation": self.resv.id, "room": self.room.id,
            "id_type": "Passport", "id_number": "P1234567", "mobile": "9876543210",
            "id_scan": "data:text/html,<script>alert(1)</script>",
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("must be an image", r.data["detail"])

    def test_post_charge_applies_gst(self):
        folio = services.check_in(self.resv, self.room)
        line = services.post_charge(
            folio, kind=FolioLine.KIND_FNB, description="Dinner",
            amount=Decimal("1000"), gst_rate=Decimal("5"),
        )
        self.assertEqual(line.total, Decimal("1050.00"))
        self.assertEqual(folio.balance, Decimal("1050.00"))

    def test_pending_room_charges_preview_before_audit(self):
        """Pre-audit the folio reads ₹0 — the preview must show the stay's room
        nights so the check-out screen isn't blank (user-reported)."""
        folio = services.check_in(self.resv, self.room)
        pending = services.pending_room_charges(folio)
        self.assertEqual(len(pending), 1)  # 1 night, none posted yet
        self.assertIn("night 1", pending[0]["description"])
        # 6500 @ 12% GST = 7280, matching what check-out will actually post
        self.assertEqual(pending[0]["total"], Decimal("7280.00"))
        # Nothing was written by the preview…
        self.assertEqual(folio.lines.count(), 0)
        # …and once charges post, the preview empties out.
        services.post_stay_room_charges(folio)
        self.assertEqual(services.pending_room_charges(folio), [])

    def test_checkout_requires_zero_balance(self):
        folio = services.check_in(self.resv, self.room)
        services.post_charge(
            folio, kind=FolioLine.KIND_ROOM, description="Room",
            amount=Decimal("6500"), gst_rate=Decimal("12"),
        )
        with self.assertRaises(ValueError):
            services.check_out(folio)
        services.check_out(folio, payments=[{"tender": "Card", "amount": folio.balance}])
        folio.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(folio.status, Folio.SETTLED)
        self.assertTrue(folio.invoice_no)
        self.assertEqual(self.room.status, Room.VACANT_DIRTY)

    def test_front_desk_room_service_fires_kot_and_posts_to_folio(self):
        """Option-3 room service: Front Office (no POS module) orders food for a
        guest — KOT fires to the kitchen, stock deducts, bill lands on the folio."""
        from django.urls import reverse

        from rest_framework.test import APIClient

        from apps.accounts.models import User
        from apps.inventory.models import Ingredient
        from apps.pos.models import Category, Kot, MenuItem, Order
        from apps.recipes.models import Recipe, RecipeLine

        cat = Category.objects.create(name="Room Service")
        paneer = Ingredient.objects.create(name="Paneer", unit="kg",
                                           current_stock=Decimal("5"), unit_cost=Decimal("320"))
        dish = MenuItem.objects.create(name="Paneer Tikka", category=cat,
                                       price=Decimal("400"), gst_rate=Decimal("5"))
        RecipeLine.objects.create(recipe=Recipe.objects.create(menu_item=dish),
                                  ingredient=paneer, qty=Decimal("0.2"))
        folio = services.check_in(self.resv, self.room)

        client = APIClient()
        client.force_authenticate(User.objects.create_user(
            username="fo", password="Tk9$mZ2pQw!7", role="Front Office"))
        # Segregation still holds: no POS access…
        self.assertEqual(client.get("/api/pos/orders/").status_code, 403)
        # …but the folio-scoped room-service flow works.
        r = client.get(reverse("folio-room-service-menu"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Paneer Tikka", [m["name"] for m in r.data])
        r = client.post(reverse("folio-room-service", args=[folio.id]),
                        {"items": [{"menu_item": dish.id, "qty": 2}]}, format="json")
        self.assertEqual(r.status_code, 201)
        # Bill on the folio: 2 × 400 @ 5% GST = 840
        folio.refresh_from_db()
        line = folio.lines.get()
        self.assertEqual(line.total, Decimal("840.00"))
        self.assertIn("Room service", line.description)
        # Kitchen got a KOT round and the order is closed to the room.
        order = Order.objects.get(external_ref=f"folio:{folio.id}")
        self.assertEqual(order.status, Order.POSTED_TO_ROOM)
        self.assertEqual(order.kitchen_status, "cooking")
        self.assertEqual(Kot.objects.filter(order=order).count(), 1)
        self.assertEqual(order.captain, "Room 201")
        # Stock deducted via the recipe seam: 5 − 2×0.2 = 4.6
        paneer.refresh_from_db()
        self.assertEqual(paneer.current_stock, Decimal("4.600"))
        # Kitchen marks it ready → the front desk gets a notification.
        kot = Kot.objects.get(order=order)
        kot.status = "ready"
        kot.save(update_fields=["status"])
        r = client.get("/api/notifications/")
        titles = [a["title"] for a in r.data["alerts"]]
        self.assertTrue(any("Room 201" in t and "food ready" in t for t in titles), titles)

        # Wrong order? Cancel before it's served: bill reversed, stock returned,
        # KDS ticket pulled, order closed as cancelled.
        r = client.get(reverse("folio-room-service-orders", args=[folio.id]))
        self.assertTrue(r.data[0]["cancellable"])
        r = client.post(reverse("folio-room-service-cancel", args=[folio.id]),
                        {"order": order.id, "reason": "wrong room"}, format="json")
        self.assertEqual(r.status_code, 200)
        folio.refresh_from_db()
        self.assertEqual(folio.lines.count(), 0)          # charge off the bill
        self.assertEqual(folio.balance, Decimal("0.00"))
        paneer.refresh_from_db()
        self.assertEqual(paneer.current_stock, Decimal("5.000"))  # stock returned
        self.assertEqual(Kot.objects.filter(order=order).count(), 0)  # ticket pulled
        order.refresh_from_db()
        self.assertIn("CANCELLED", order.discount_reason)
        # A cancelled order can't be cancelled twice.
        r = client.post(reverse("folio-room-service-cancel", args=[folio.id]),
                        {"order": order.id, "reason": "again"}, format="json")
        self.assertEqual(r.status_code, 400)

        # Settled folios refuse new room-service orders.
        services.check_out(folio, payments=[{"tender": "Card", "amount": folio.balance}])
        r = client.post(reverse("folio-room-service", args=[folio.id]),
                        {"items": [{"menu_item": dish.id, "qty": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_front_desk_confirms_room_service_delivered(self):
        """Tweak #5: front desk taps 'Delivered' once the kitchen marks ready —
        closes the ticket (clears the alert), can't be confirmed twice, and
        can't be confirmed before the kitchen is actually ready."""
        from django.urls import reverse

        from rest_framework.test import APIClient

        from apps.accounts.models import User
        from apps.inventory.models import Ingredient
        from apps.pos.models import Category, Kot, MenuItem
        from apps.recipes.models import Recipe, RecipeLine

        cat = Category.objects.create(name="Room Service")
        paneer = Ingredient.objects.create(name="Paneer2", unit="kg",
                                           current_stock=Decimal("5"), unit_cost=Decimal("320"))
        dish = MenuItem.objects.create(name="Paneer Tikka 2", category=cat,
                                       price=Decimal("400"), gst_rate=Decimal("5"))
        RecipeLine.objects.create(recipe=Recipe.objects.create(menu_item=dish),
                                  ingredient=paneer, qty=Decimal("0.2"))
        folio = services.check_in(self.resv, self.room)
        client = APIClient()
        client.force_authenticate(User.objects.create_user(
            username="fo2", password="Tk9$mZ2pQw!7", role="Front Office"))
        r = client.post(reverse("folio-room-service", args=[folio.id]),
                        {"items": [{"menu_item": dish.id, "qty": 1}]}, format="json")
        self.assertEqual(r.status_code, 201)
        from apps.pos.models import Order
        order = Order.objects.get(external_ref=f"folio:{folio.id}")
        kot = Kot.objects.get(order=order)

        # Can't confirm delivery before the kitchen has marked it ready.
        r = client.post(reverse("folio-room-service-delivered", args=[folio.id]),
                        {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 400)

        kot.status = "ready"
        kot.save(update_fields=["status"])
        titles = [a["title"] for a in client.get("/api/notifications/").data["alerts"]]
        self.assertTrue(any("food ready" in t for t in titles))

        r = client.post(reverse("folio-room-service-delivered", args=[folio.id]),
                        {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["delivered"])
        kot.refresh_from_db()
        self.assertEqual(kot.status, "served")
        order.refresh_from_db()
        self.assertEqual(order.kitchen_status, "served")
        titles = [a["title"] for a in client.get("/api/notifications/").data["alerts"]]
        self.assertFalse(any("food ready" in t for t in titles))

        # Can't confirm delivery twice.
        r = client.post(reverse("folio-room-service-delivered", args=[folio.id]),
                        {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_corporate_checkout_posts_to_company_ar(self):
        from apps.crm.models import Customer
        folio = services.check_in(self.resv, self.room)
        folio.routing = "city_ledger"
        folio.company_name = "Globex Ltd"
        folio.save(update_fields=["routing", "company_name"])
        services.post_charge(folio, kind=FolioLine.KIND_ROOM, description="Room",
                             amount=Decimal("6500"), gst_rate=Decimal("12"))
        bal = folio.balance
        # No tender supplied — BTC folio must still check out (bill to company).
        services.check_out(folio)
        folio.refresh_from_db()
        self.assertEqual(folio.status, Folio.SETTLED)
        self.assertEqual(folio.balance, Decimal("0.00"))
        company = Customer.objects.get(name="Globex Ltd", customer_type=Customer.TYPE_CORPORATE)
        self.assertEqual(company.outstanding, bal)

    def test_night_audit_is_idempotent(self):
        services.check_in(self.resv, self.room)
        run1 = services.run_night_audit(date.today())
        first_count = FolioLine.objects.filter(kind=FolioLine.KIND_ROOM).count()
        run2 = services.run_night_audit(date.today())
        second_count = FolioLine.objects.filter(kind=FolioLine.KIND_ROOM).count()
        self.assertEqual(run1.id, run2.id)
        self.assertEqual(first_count, second_count)  # no double-posting
        self.assertEqual(run1.rooms_posted, 1)


class DeskChargeAndPaymentTests(TestCase):
    """Manual incidentals + part-payments from the Folios screen."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="fo9", password="Tk9$mZ2pQw!7", role="Front Office"))
        rt = RoomType.objects.create(code="STD9", name="Standard",
                                     base_rate=Decimal("4000"), gst_slab=Decimal("12"))
        room = Room.objects.create(number="901", room_type=rt, status=Room.VACANT_CLEAN)
        self.folio = services.check_in(Reservation.objects.create(
            guest_name="Desk Guest", room_type=rt, checkin_date=date.today(),
            checkout_date=date.today() + timedelta(days=1), nights=1,
            rate=Decimal("4000")), room)

    def test_add_charge_posts_incidental_with_gst(self):
        r = self.client.post(f"/api/folios/{self.folio.id}/add_charge/",
                             {"description": "Laundry — 3 shirts", "amount": "300",
                              "gst_rate": "18"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        line = self.folio.lines.get()
        self.assertEqual(line.kind, FolioLine.KIND_INCIDENTAL)
        self.assertEqual(line.total, Decimal("354.00"))   # 300 + 18%
        self.assertEqual(r.data["balance"], "354.00")     # response carries the new line
        # Junk is refused.
        self.assertEqual(self.client.post(f"/api/folios/{self.folio.id}/add_charge/",
                         {"description": "", "amount": "10"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"/api/folios/{self.folio.id}/add_charge/",
                         {"description": "x", "amount": "-5"}, format="json").status_code, 400)

    def test_part_payment_keeps_folio_open_until_cleared(self):
        services.post_charge(self.folio, kind=FolioLine.KIND_INCIDENTAL,
                             description="Airport pickup", amount=Decimal("1000"),
                             gst_rate=Decimal("18"))
        r = self.client.post(f"/api/folios/{self.folio.id}/settle/",
                             {"payments": [{"tender": "UPI", "amount": "500",
                                            "reference": "adv-1"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "open")        # deposit, not closure
        self.assertEqual(r.data["balance"], "680.00")     # 1180 − 500
        r = self.client.post(f"/api/folios/{self.folio.id}/settle/",
                             {"payments": [{"tender": "Cash", "amount": "680"}]}, format="json")
        self.assertEqual(r.data["status"], "settled")
        self.assertTrue(r.data["invoice_no"])


class ChargeTransferTests(TestCase):
    """Move a mis-posted F&B/incidental charge to another open folio —
    the companion's dinner on the wrong room, split bills (FR-PMS-006)."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="fo3", password="Tk9$mZ2pQw!7", role="Front Office"))
        rt = RoomType.objects.create(code="STD2", name="Standard",
                                     base_rate=Decimal("4000"), gst_slab=Decimal("12"))
        r1 = Room.objects.create(number="301", room_type=rt, status=Room.VACANT_CLEAN)
        r2 = Room.objects.create(number="302", room_type=rt, status=Room.VACANT_CLEAN)

        def mk(name, room):
            return services.check_in(Reservation.objects.create(
                guest_name=name, room_type=rt, checkin_date=date.today(),
                checkout_date=date.today() + timedelta(days=1), nights=1,
                rate=Decimal("4000")), room)
        self.src = mk("Wrong Room", r1)
        self.dst = mk("Right Room", r2)
        self.dinner = services.post_charge(
            self.src, kind=FolioLine.KIND_FNB, description="Dinner",
            amount=Decimal("1000"), gst_rate=Decimal("5"))

    def _move(self, line_id, to_folio):
        return self.client.post(f"/api/folios/{self.src.id}/transfer_charge/",
                                {"line": line_id, "to_folio": to_folio}, format="json")

    def test_fnb_charge_moves_to_other_open_folio(self):
        r = self._move(self.dinner.id, self.dst.id)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["to_guest"], "Right Room")
        self.dinner.refresh_from_db()
        self.assertEqual(self.dinner.folio_id, self.dst.id)
        self.assertIn("moved from folio", self.dinner.source)
        self.assertEqual(self.src.balance, Decimal("0"))
        self.assertEqual(self.dst.balance, Decimal("1050.00"))
        from apps.accounts.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(
            action="charge_transfer", entity_id=str(self.dinner.id)).exists())

    def test_room_and_tax_lines_stay_put(self):
        room_line = services.post_charge(
            self.src, kind=FolioLine.KIND_ROOM, description="Room night",
            amount=Decimal("4000"), gst_rate=Decimal("12"))
        r = self._move(room_line.id, self.dst.id)
        self.assertEqual(r.status_code, 400)
        self.assertIn("belong to the stay", r.data["detail"])

    def test_target_must_be_a_different_open_folio(self):
        r = self._move(self.dinner.id, self.src.id)
        self.assertEqual(r.status_code, 400)
        self.dst.status = Folio.SETTLED
        self.dst.save()
        r = self._move(self.dinner.id, self.dst.id)
        self.assertEqual(r.status_code, 400)
        self.dinner.refresh_from_db()
        self.assertEqual(self.dinner.folio_id, self.src.id)  # nothing moved

    def test_line_must_belong_to_source_folio(self):
        stray = services.post_charge(
            self.dst, kind=FolioLine.KIND_FNB, description="Their own snack",
            amount=Decimal("100"), gst_rate=Decimal("5"))
        r = self._move(stray.id, self.dst.id)
        self.assertEqual(r.status_code, 404)


class BillingModeTests(TestCase):
    """Room bill with or without GST (BRD 5.23): per-folio override + recompute."""

    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gmb", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.folio = Folio.objects.create(guest_name="Guest X")

    def test_without_gst_posts_zero_tax(self):
        from decimal import Decimal

        from django.urls import reverse

        from . import services
        # Charge under with_gst: 4000 @ 12% → 4480.
        line = services.post_charge(self.folio, kind=FolioLine.KIND_ROOM,
                                    description="Room", amount=Decimal("4000"),
                                    gst_rate=Decimal("12"))
        self.assertEqual(line.total, Decimal("4480.00"))
        # Switch the bill to without_gst → lines recomputed to taxable only.
        r = self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                             {"mode": "without_gst"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["effective_billing_mode"], "without_gst")
        line.refresh_from_db()
        self.assertEqual(line.cgst, Decimal("0"))
        self.assertEqual(line.total, Decimal("4000.00"))
        # F&B always keeps GST — bill of supply covers the room only.
        self.folio.refresh_from_db()
        line2 = services.post_charge(self.folio, kind=FolioLine.KIND_FNB,
                                     description="Dinner", amount=Decimal("500"),
                                     gst_rate=Decimal("5"))
        self.assertEqual(line2.total, Decimal("525.00"))
        # New ROOM charges still post untaxed in this mode.
        line3 = services.post_charge(self.folio, kind=FolioLine.KIND_ROOM,
                                     description="Extra night", amount=Decimal("1000"),
                                     gst_rate=Decimal("12"))
        self.assertEqual(line3.total, Decimal("1000.00"))
        # Switch back → tax re-applied from each line's own rate.
        self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                         {"mode": "with_gst"}, format="json")
        line.refresh_from_db(); line2.refresh_from_db(); line3.refresh_from_db()
        self.assertEqual(line.total, Decimal("4480.00"))
        self.assertEqual(line2.total, Decimal("525.00"))
        self.assertEqual(line3.total, Decimal("1120.00"))
        # And toggling to without_gst again leaves the food taxed.
        self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                         {"mode": "without_gst"}, format="json")
        line2.refresh_from_db(); line3.refresh_from_db()
        self.assertEqual(line2.total, Decimal("525.00"))   # food unchanged
        self.assertEqual(line3.total, Decimal("1000.00"))  # room untaxed

    def test_invoice_pdf_renders_both_modes(self):
        from django.urls import reverse
        r = self.client.get(reverse("folio-invoice-pdf", args=[self.folio.id]))
        self.assertEqual(r.status_code, 200)  # tax invoice
        self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                         {"mode": "without_gst"}, format="json")
        r = self.client.get(reverse("folio-invoice-pdf", args=[self.folio.id]))
        self.assertEqual(r.status_code, 200)  # bill of supply
