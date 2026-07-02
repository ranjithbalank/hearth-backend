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

    def _payload(self, **over):
        p = {"channel": "Booking.com", "reservation_id": "BDC-1", "guest_name": "Emma Watson",
             "mobile": "+44 7700900123", "room_type": "STD", "checkin": "2026-10-01",
             "checkout": "2026-10-04", "rate": "4200", "amount_prepaid": "12600"}
        p.update(over)
        return p

    def test_ingest_creates_ota_reservation(self):
        r = self.client.post(reverse("channel-ingest"), self._payload(), format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["created"])
        resv = r.data["reservation"]
        self.assertEqual(resv["source"], "ota")
        self.assertEqual(resv["source_label"], "OTA")
        self.assertEqual(resv["channel_name"], "Booking.com")
        self.assertEqual(resv["nights"], 3)
        self.assertTrue(resv["prepaid"])
        self.assertEqual(resv["deposit"], "12600.00")

    def test_ingest_is_idempotent(self):
        r1 = self.client.post(reverse("channel-ingest"), self._payload(), format="json")
        r2 = self.client.post(reverse("channel-ingest"), self._payload(), format="json")
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.data["created"])
        self.assertEqual(r1.data["reservation"]["id"], r2.data["reservation"]["id"])

    def test_ingest_rejects_unknown_room_type(self):
        r = self.client.post(reverse("channel-ingest"), self._payload(room_type="ZZZ"), format="json")
        self.assertEqual(r.status_code, 400)
