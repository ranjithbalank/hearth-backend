from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType


class BookingEngineTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4500"))
        Room.objects.create(number="101", room_type=self.rt, status=Room.VACANT_CLEAN)

    def test_direct_booking_creates_reservation(self):
        r = self.client.post(reverse("booking-list"),
                             {"room_type": "STD", "guest_name": "Web Guest", "nights": 2},
                             format="json")
        self.assertEqual(r.status_code, 201)
        resv = Reservation.objects.get(pk=r.data["id"])
        self.assertEqual(resv.source, Reservation.SOURCE_BOOKING)
        self.assertEqual(resv.nights, 2)

    def test_stats_reflect_direct_share(self):
        self.client.post(reverse("booking-list"), {"room_type": "STD"}, format="json")
        r = self.client.get(reverse("booking-stats"))
        self.assertGreaterEqual(r.data["direct_bookings"], 1)


class BookingEngineGuardTests(TestCase):
    """The direct-booking engine writes real reservations from data a guest
    controls, so what it takes from the request — and what it refuses to —
    is the part worth pinning down."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="be_gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4500"))
        Room.objects.create(number="101", room_type=self.rt, status=Room.VACANT_CLEAN)

    def test_an_unknown_room_type_is_refused(self):
        r = self.client.post(reverse("booking-list"),
                             {"room_type": "NOPE", "guest_name": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_the_rate_comes_from_the_room_type_not_the_request(self):
        """A booking engine is public-facing. If the posted body could set the
        rate, anyone could book a suite for ₹1."""
        r = self.client.post(reverse("booking-list"),
                             {"room_type": "STD", "rate": "1", "deposit": "1"}, format="json")
        self.assertEqual(r.status_code, 201)
        resv = Reservation.objects.get(pk=r.data["id"])
        self.assertEqual(resv.rate, Decimal("4500"))

    def test_dates_follow_the_requested_stay(self):
        from datetime import date, timedelta
        r = self.client.post(reverse("booking-list"),
                             {"room_type": "STD", "nights": 3, "in_days": 5}, format="json")
        resv = Reservation.objects.get(pk=r.data["id"])
        self.assertEqual(resv.checkin_date, date.today() + timedelta(days=5))
        self.assertEqual(resv.checkout_date, resv.checkin_date + timedelta(days=3))
        self.assertEqual(resv.nights, 3)

    def test_a_prepaid_booking_carries_a_deposit_and_an_unpaid_one_does_not(self):
        paid = self.client.post(reverse("booking-list"),
                                {"room_type": "STD", "prepaid": True}, format="json")
        self.assertEqual(Reservation.objects.get(pk=paid.data["id"]).deposit, Decimal("4500"))
        unpaid = self.client.post(reverse("booking-list"),
                                  {"room_type": "STD", "prepaid": False}, format="json")
        self.assertEqual(Reservation.objects.get(pk=unpaid.data["id"]).deposit, Decimal("0"))

    def test_availability_reflects_only_sellable_rooms(self):
        Room.objects.create(number="102", room_type=self.rt, status=Room.OOO)
        Room.objects.create(number="103", room_type=self.rt, status=Room.VACANT_DIRTY)
        row = next(r for r in self.client.get(reverse("booking-list")).data
                   if r["room_type"] == "STD")
        # Only 101 is clean; the out-of-order and dirty rooms are not sellable.
        self.assertEqual(row["available"], 1)

    def test_a_direct_booking_is_attributed_as_direct(self):
        """The commission-saved figure on the stats card is derived from this
        attribution — mislabel the source and the whole number is wrong."""
        self.client.post(reverse("booking-list"), {"room_type": "STD"}, format="json")
        stats = self.client.get(reverse("booking-stats")).data
        self.assertEqual(stats["ota_bookings"], 0)
        self.assertEqual(Decimal(stats["commission_saved"]), Decimal("675.00"))  # 4500 x 15%
