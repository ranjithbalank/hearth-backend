"""Concurrency audit — the read-modify-write races.

Every guard added so far is check-then-act: read the row, decide it is OK,
write. That is correct with one request in flight and wrong with two, because
both can pass the check before either writes. The whole project contains
exactly one select_for_update (document numbering); nothing else takes a row
lock.

These tests reproduce the interleaving deterministically rather than with
threads — two stale in-memory instances of the same row, which is precisely
what two web workers hold when requests arrive together. SQLite serialises
writes, so real threads would prove nothing here; stale instances prove the
logic, and the logic is what is wrong.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType

from . import services
from .models import Folio, FolioLine


class FolioRaceTests(TestCase):
    def setUp(self):
        self.rt = RoomType.objects.create(
            code="RACE", name="Race", base_rate=Decimal("4000"), gst_slab=Decimal("12"))
        self.room = Room.objects.create(
            number="801", room_type=self.rt, floor=8, status=Room.VACANT_CLEAN)
        self.user = User.objects.create_user(
            username="raceop", password="Tk9$mZ2pQw!7", role="General Manager")

    def _folio(self, nights=1):
        resv = Reservation.objects.create(
            guest_name="Race Guest", room_type=self.rt,
            checkin_date=date.today(), checkout_date=date.today() + timedelta(days=nights),
            nights=nights, rate=Decimal("4000"), status=Reservation.BOOKED)
        return services.check_in(resv, self.room, user=self.user)

    def test_two_cashiers_settling_at_once_take_payment_once(self):
        """Both terminals load the folio, both see it open, both settle. The
        status check passes twice because neither has written yet — the guest
        pays the bill twice and the second payment sits as a credit nobody
        reconciles."""
        folio = self._folio()
        services.post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description="Spa",
                             amount=Decimal("2000"), gst_rate=Decimal("18"))
        due = folio.balance

        # Two workers, each holding their own instance of the same row.
        a = Folio.objects.get(pk=folio.pk)
        b = Folio.objects.get(pk=folio.pk)
        services.settle_folio(a, [{"tender": "Cash", "amount": str(due)}], user=self.user)
        try:
            services.settle_folio(b, [{"tender": "Card", "amount": str(due)}], user=self.user)
        except ValueError:
            pass  # the desired outcome — the second one is refused

        folio.refresh_from_db()
        self.assertEqual(folio.paid_total, due,
                         f"the guest was charged {folio.paid_total} against a bill of {due}")

    def test_two_desks_checking_out_at_once_do_not_double_post(self):
        folio = self._folio(nights=2)
        a = Folio.objects.get(pk=folio.pk)
        b = Folio.objects.get(pk=folio.pk)
        services.check_out(a, tender="Cash", user=self.user)
        try:
            services.check_out(b, tender="Cash", user=self.user)
        except ValueError:
            pass
        folio.refresh_from_db()
        self.assertEqual(folio.lines.filter(kind=FolioLine.KIND_ROOM).count(), 2,
                         "room nights were posted twice by a concurrent check-out")
        self.assertEqual(folio.balance, Decimal("0"))
