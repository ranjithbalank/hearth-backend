from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.rooms.models import RoomType

from .models import Channel, ChannelRate


class ChannelApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="g", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4500"))
        b = Channel.objects.create(name="Booking.com")
        e = Channel.objects.create(name="Expedia")
        ChannelRate.objects.create(channel=b, room_type=self.rt, rate=Decimal("4500"), availability=4)
        ChannelRate.objects.create(channel=e, room_type=self.rt, rate=Decimal("4700"), availability=4)

    def test_parity_breach_then_fix(self):
        r = self.client.get(reverse("channel-ari"))
        self.assertFalse(r.data["parity_ok"])
        self.assertTrue(r.data["grid"][0]["parity_breach"])
        self.client.post(reverse("channel-fix-parity"))
        r = self.client.get(reverse("channel-ari"))
        self.assertTrue(r.data["parity_ok"])
