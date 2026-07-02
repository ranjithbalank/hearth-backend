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
