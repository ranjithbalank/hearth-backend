from decimal import Decimal

from django.test import TestCase

from apps.channel.models import Channel, ChannelRate, ChannelPush
from apps.rooms.models import RoomType

from . import views  # noqa: F401 (ensure import works)
from .models import RateRecommendation


class RmsChannelSeamTests(TestCase):
    def setUp(self):
        self.rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("6500"))
        Channel.objects.create(name="Booking.com")
        Channel.objects.create(name="Expedia")
        self.rec = RateRecommendation.objects.create(
            room_type=self.rt, current_rate=Decimal("6500"),
            recommended_rate=Decimal("7200"), reason="demand",
        )

    def test_accept_pushes_rate_to_all_connected_channels(self):
        from apps.channel import services
        pushed = services.push_rate(self.rt, Decimal("7200"))
        self.assertEqual(pushed, 2)
        rates = set(ChannelRate.objects.values_list("rate", flat=True))
        self.assertEqual(rates, {Decimal("7200.00")})
        self.assertTrue(ChannelPush.objects.exists())

    def test_parity_detection_and_fix(self):
        from apps.channel import services
        b = Channel.objects.get(name="Booking.com")
        e = Channel.objects.get(name="Expedia")
        ChannelRate.objects.create(channel=b, room_type=self.rt, rate=Decimal("6500"))
        ChannelRate.objects.create(channel=e, room_type=self.rt, rate=Decimal("6700"))
        self.assertIn("DLX", services.parity_breaches())
        services.fix_parity()
        self.assertEqual(services.parity_breaches(), [])
