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
