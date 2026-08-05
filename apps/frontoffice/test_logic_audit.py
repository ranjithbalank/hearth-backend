"""Logic-layer audit — money math, state machines and cross-field invariants.

The Aug-2026 field-coverage sweep proved every writable field has a guard. That
says nothing about whether the arithmetic on the other side is right, which is
where the expensive defects live: a negative price is embarrassing, a folio that
quietly loses four thousand rupees on a charge transfer costs a customer.

These tests assert what *should* be true of the money. A failure here is a real
defect, not a style disagreement.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer
from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType

from . import services
from .models import Folio, FolioLine


class FolioMoneyTests(TestCase):
    def setUp(self):
        self.rt = RoomType.objects.create(
            code="AUD", name="Audit", base_rate=Decimal("5000"), gst_slab=Decimal("12"))
        self.room = Room.objects.create(
            number="901", room_type=self.rt, floor=9, status=Room.VACANT_CLEAN)
        self.user = User.objects.create_user(
            username="auditor", password="Tk9$mZ2pQw!7", role="General Manager")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _folio(self, nights=2, rate=Decimal("5000")):
        resv = Reservation.objects.create(
            guest_name="Audit Guest", room_type=self.rt,
            checkin_date=date.today(), checkout_date=date.today() + timedelta(days=nights),
            nights=nights, rate=rate, status=Reservation.BOOKED)
        return services.check_in(resv, self.room, user=self.user)

    # -- payments ------------------------------------------------------
    def test_a_negative_payment_cannot_be_recorded(self):
        """A negative settlement is a payment that increases what is owed.

        Nothing in the product needs one: a refund is its own flow with its own
        audit trail. Left open, it is a way to inflate a folio balance (or, on a
        settled folio, to reopen it) with a line that reads as a payment."""
        folio = self._folio()
        services.post_charge(folio, kind=FolioLine.KIND_INCIDENTAL,
                             description="Laundry", amount=Decimal("500"),
                             gst_rate=Decimal("18"))
        before = folio.balance
        r = self.client.post(f"/api/folios/{folio.id}/settle/",
                             {"payments": [{"tender": "Cash", "amount": "-1000"}]},
                             format="json")
        self.assertEqual(r.status_code, 400, "a negative payment must be refused")
        folio.refresh_from_db()
        self.assertEqual(folio.balance, before)

    def test_a_zero_payment_cannot_be_recorded(self):
        folio = self._folio()
        services.post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description="Minibar",
                             amount=Decimal("200"), gst_rate=Decimal("18"))
        r = self.client.post(f"/api/folios/{folio.id}/settle/",
                             {"payments": [{"tender": "Cash", "amount": "0"}]},
                             format="json")
        self.assertEqual(r.status_code, 400)

    def test_a_settled_folio_cannot_be_settled_again(self):
        """Double-settle is the classic till-shrinkage bug: the second payment
        is real money taken from a guest against a bill already paid."""
        folio = self._folio()
        services.post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description="Spa",
                             amount=Decimal("1000"), gst_rate=Decimal("18"))
        due = folio.balance
        first = self.client.post(f"/api/folios/{folio.id}/settle/",
                                 {"payments": [{"tender": "Cash", "amount": str(due)}]},
                                 format="json")
        self.assertEqual(first.status_code, 200)
        folio.refresh_from_db()
        self.assertEqual(folio.status, Folio.SETTLED)
        second = self.client.post(f"/api/folios/{folio.id}/settle/",
                                  {"payments": [{"tender": "Cash", "amount": "500"}]},
                                  format="json")
        self.assertEqual(second.status_code, 400, "a settled folio must not take another payment")

    def test_a_charge_cannot_be_added_to_a_settled_folio(self):
        """Otherwise the bill the guest signed and the bill on file diverge."""
        folio = self._folio()
        services.post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description="Spa",
                             amount=Decimal("1000"), gst_rate=Decimal("18"))
        self.client.post(f"/api/folios/{folio.id}/settle/",
                         {"payments": [{"tender": "Cash", "amount": str(folio.balance)}]},
                         format="json")
        folio.refresh_from_db()
        r = self.client.post(f"/api/folios/{folio.id}/add_charge/",
                             {"description": "Late minibar", "amount": "300"}, format="json")
        self.assertEqual(r.status_code, 400)

    # -- room nights ---------------------------------------------------
    def test_room_nights_are_not_double_posted_on_repeat_calls(self):
        folio = self._folio(nights=2)
        services.post_stay_room_charges(folio, user=self.user)
        services.post_stay_room_charges(folio, user=self.user)
        self.assertEqual(folio.lines.filter(kind=FolioLine.KIND_ROOM).count(), 2)

    def test_a_transferred_room_night_is_not_reposted(self):
        """post_stay_room_charges decides what to post by COUNTING the room
        lines already on the folio. Move one to another folio — which the
        charge-transfer flow exists to do — and the count drops, so the night
        is posted a second time and the guest is billed twice for it."""
        folio = self._folio(nights=2)
        services.post_stay_room_charges(folio, user=self.user)
        other = self._folio(nights=1)
        moved = folio.lines.filter(kind=FolioLine.KIND_ROOM).first()
        moved.folio = other
        moved.save(update_fields=["folio"])
        services.post_stay_room_charges(folio, user=self.user)
        # The moved night stays where it was moved to; what must NOT happen is a
        # replacement being minted for it here.
        self.assertEqual(folio.lines.filter(kind=FolioLine.KIND_ROOM).count(), 1)
        # Across every folio, each night of this stay is billed exactly once.
        sources = list(FolioLine.objects
                       .filter(source__startswith=f"stay:{folio.id}:")
                       .values_list("source", flat=True))
        self.assertEqual(sorted(sources),
                         [f"stay:{folio.id}:1", f"stay:{folio.id}:2"],
                         f"a room night was billed twice: {sources}")

    def test_night_audit_then_checkout_does_not_bill_the_night_twice(self):
        """The two paths that post room nights use different source keys —
        night audit writes "night-audit:<date>", the check-out path writes
        "stay:<folio>:<n>". Check-out must still recognise a night the audit
        already billed, or every guest who stays over a night audit pays for
        that night twice."""
        folio = self._folio(nights=1)
        services.run_night_audit(date.today(), user=self.user)
        after_audit = folio.lines.filter(kind=FolioLine.KIND_ROOM).count()
        self.assertEqual(after_audit, 1, "night audit did not post the room night")
        services.post_stay_room_charges(folio, user=self.user)
        self.assertEqual(folio.lines.filter(kind=FolioLine.KIND_ROOM).count(), 1,
                         "check-out re-billed a night the audit had already posted")

    # -- city ledger ---------------------------------------------------
    def test_two_companies_with_similar_names_keep_separate_ledgers(self):
        """company_account() keys a company on ("CO:" + name)[:20]. Two real
        customers whose names agree for the first 17 characters collapse onto
        one AR row, so one company's folio lands on the other's receivables."""
        a = services.company_account("Infosys Technologies Ltd")
        b = services.company_account("Infosys Technologies Pvt")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(a.id, b.id,
                            "two different companies were merged into one AR ledger")
        self.assertEqual(b.name, "Infosys Technologies Pvt")

    def test_city_ledger_checkout_bills_the_right_company(self):
        folio = self._folio(nights=1)
        folio.routing = "city_ledger"
        folio.company_name = "Wipro Technologies Limited"
        folio.save(update_fields=["routing", "company_name"])
        decoy = services.company_account("Wipro Technologies Private")
        decoy_before = decoy.outstanding
        services.check_out(folio, user=self.user)
        decoy.refresh_from_db()
        self.assertEqual(decoy.outstanding, decoy_before,
                         "an unrelated company's receivables moved")

    # -- checkout ------------------------------------------------------
    def test_checkout_twice_does_not_take_payment_twice(self):
        folio = self._folio(nights=1)
        services.check_out(folio, tender="Cash", user=self.user)
        folio.refresh_from_db()
        paid_once = folio.paid_total
        r = self.client.post(f"/api/folios/{folio.id}/check_out/", {"tender": "Cash"},
                             format="json")
        folio.refresh_from_db()
        self.assertEqual(folio.paid_total, paid_once,
                         f"check-out ran twice and took payment twice (HTTP {r.status_code})")

    def test_a_folio_balance_always_equals_charges_minus_payments(self):
        folio = self._folio(nights=2)
        services.post_stay_room_charges(folio, user=self.user)
        services.post_charge(folio, kind=FolioLine.KIND_FNB, description="Dinner",
                             amount=Decimal("1250.50"), gst_rate=Decimal("5"))
        self.client.post(f"/api/folios/{folio.id}/settle/",
                         {"payments": [{"tender": "Card", "amount": "1000"}]}, format="json")
        folio.refresh_from_db()
        self.assertEqual(folio.balance, folio.charges_total - folio.paid_total)
        self.assertGreater(folio.balance, 0)


class LoyaltyAndCustomerTests(TestCase):
    """Points are money in every way that matters — they discount a bill."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="auditor2", password="Tk9$mZ2pQw!7", role="General Manager")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_loyalty_points_cannot_be_set_directly(self):
        c = Customer.objects.create(mobile="+919000000001", name="Points Guest",
                                    loyalty_points=10)
        r = self.client.patch(f"/api/customers/{c.id}/", {"loyalty_points": 99999},
                              format="json")
        c.refresh_from_db()
        self.assertEqual(c.loyalty_points, 10,
                         f"points were mintable through a PATCH (HTTP {r.status_code})")

    def test_outstanding_cannot_be_cleared_directly(self):
        c = Customer.objects.create(mobile="+919000000002", name="Debtor Co",
                                    customer_type=Customer.TYPE_CORPORATE,
                                    outstanding=Decimal("50000"))
        self.client.patch(f"/api/customers/{c.id}/", {"outstanding": "0"}, format="json")
        c.refresh_from_db()
        self.assertEqual(c.outstanding, Decimal("50000"),
                         "a company's debt was cleared with an ordinary PATCH")
