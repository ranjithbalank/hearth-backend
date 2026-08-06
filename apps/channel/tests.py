from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.rooms.models import RoomType

from . import services
from .models import Channel, ChannelPush, ChannelRate


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


class AriGridTests(TestCase):
    """The ARI grid is the one screen showing what every OTA is currently
    selling at. A wrong cell here is a rate mismatch nobody notices until a
    guest quotes it back at the desk."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="ari_gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("6500"))
        self.bdc = Channel.objects.create(name="Booking.com")
        self.exp = Channel.objects.create(name="Expedia")

    def test_grid_has_a_cell_for_every_room_type_and_connected_channel(self):
        body = self.client.get("/api/channel/ari/").data
        self.assertEqual(body["channels"], ["Booking.com", "Expedia"])
        row = body["grid"][0]
        self.assertEqual(row["room_type"], "DLX")
        self.assertEqual([c["channel"] for c in row["cells"]], ["Booking.com", "Expedia"])

    def test_a_channel_with_no_rate_yet_reads_empty_not_zero(self):
        """Null means "we have never pushed here". Rendering it as 0 would say
        the room is being sold for nothing."""
        cell = self.client.get("/api/channel/ari/").data["grid"][0]["cells"][0]
        self.assertIsNone(cell["rate"])

    def test_a_disconnected_channel_leaves_the_grid(self):
        self.exp.connected = False
        self.exp.save()
        body = self.client.get("/api/channel/ari/").data
        self.assertEqual(body["channels"], ["Booking.com"])

    def test_the_grid_flags_the_room_type_that_is_out_of_parity(self):
        ChannelRate.objects.create(channel=self.bdc, room_type=self.rt, rate=Decimal("6500"))
        ChannelRate.objects.create(channel=self.exp, room_type=self.rt, rate=Decimal("6700"))
        body = self.client.get("/api/channel/ari/").data
        self.assertFalse(body["parity_ok"])
        self.assertTrue(body["grid"][0]["parity_breach"])

    def test_parity_is_ok_when_every_channel_agrees(self):
        services.push_rate(self.rt, Decimal("7000"))
        body = self.client.get("/api/channel/ari/").data
        self.assertTrue(body["parity_ok"])
        self.assertFalse(body["grid"][0]["parity_breach"])


class PushRateTests(TestCase):
    """push_rate is the single control point every rate change goes through —
    the RMS seam, the parity fix and the manual push all call it."""

    def setUp(self):
        self.rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4000"))
        self.live = Channel.objects.create(name="Booking.com")
        self.dark = Channel.objects.create(name="Agoda", connected=False)

    def test_pushes_only_to_connected_channels(self):
        """A disconnected channel is one we are not selling on. Writing a rate
        there would resurface as a parity breach the moment it reconnects."""
        self.assertEqual(services.push_rate(self.rt, Decimal("4500")), 1)
        self.assertFalse(ChannelRate.objects.filter(channel=self.dark).exists())

    def test_pushing_twice_updates_rather_than_duplicates(self):
        services.push_rate(self.rt, Decimal("4500"))
        services.push_rate(self.rt, Decimal("4800"))
        rates = ChannelRate.objects.filter(channel=self.live, room_type=self.rt)
        self.assertEqual(rates.count(), 1)
        self.assertEqual(rates.first().rate, Decimal("4800.00"))

    def test_every_push_leaves_an_audit_trail(self):
        services.push_rate(self.rt, Decimal("4500"))
        services.push_rate(self.rt, Decimal("4800"))
        self.assertEqual(ChannelPush.objects.count(), 2)
        self.assertIn("STD", ChannelPush.objects.last().detail)

    def test_accepts_a_rate_given_as_a_string(self):
        # Callers hand it whatever the request body carried; float/str must not
        # end up stored as a float-rounded rate.
        services.push_rate(self.rt, "4999.50")
        self.assertEqual(
            ChannelRate.objects.get(channel=self.live).rate, Decimal("4999.50"))
