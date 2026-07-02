from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType

from . import services
from .models import Folio, FolioLine


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

    def test_post_charge_applies_gst(self):
        folio = services.check_in(self.resv, self.room)
        line = services.post_charge(
            folio, kind=FolioLine.KIND_FNB, description="Dinner",
            amount=Decimal("1000"), gst_rate=Decimal("5"),
        )
        self.assertEqual(line.total, Decimal("1050.00"))
        self.assertEqual(folio.balance, Decimal("1050.00"))

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
        # New charges on this folio post untaxed.
        self.folio.refresh_from_db()
        line2 = services.post_charge(self.folio, kind=FolioLine.KIND_FNB,
                                     description="Dinner", amount=Decimal("500"),
                                     gst_rate=Decimal("5"))
        self.assertEqual(line2.total, Decimal("500.00"))
        # Switch back → tax re-applied from each line's own rate.
        self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                         {"mode": "with_gst"}, format="json")
        line.refresh_from_db(); line2.refresh_from_db()
        self.assertEqual(line.total, Decimal("4480.00"))
        self.assertEqual(line2.total, Decimal("525.00"))

    def test_invoice_pdf_renders_both_modes(self):
        from django.urls import reverse
        r = self.client.get(reverse("folio-invoice-pdf", args=[self.folio.id]))
        self.assertEqual(r.status_code, 200)  # tax invoice
        self.client.post(reverse("folio-billing-mode", args=[self.folio.id]),
                         {"mode": "without_gst"}, format="json")
        r = self.client.get(reverse("folio-invoice-pdf", args=[self.folio.id]))
        self.assertEqual(r.status_code, 200)  # bill of supply
