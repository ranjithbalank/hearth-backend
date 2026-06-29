from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.rooms.models import Room, RoomType


class GroupBlockTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="m", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4500"))
        for n in range(5):
            Room.objects.create(number=f"10{n}", room_type=self.rt, status=Room.VACANT_CLEAN)

    def test_block_reduces_availability(self):
        before = self._avail()
        self.client.post(reverse("groupblock-list"), {
            "name": "Conference", "room_type": "STD", "rooms_blocked": 3,
            "checkin_date": date.today().isoformat(),
            "checkout_date": (date.today() + timedelta(days=2)).isoformat(),
        }, format="json")
        after = self._avail()
        self.assertEqual(before - after, 3)

    def _avail(self):
        r = self.client.get(reverse("reservation-availability"))
        return [x for x in r.data if x["room_type"] == "STD"][0]["available"]
