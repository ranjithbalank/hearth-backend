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


class RateRecommendationApiTests(TestCase):
    """The Revenue Manager screen's own endpoints. Accepting a recommendation
    changes what every OTA sells the room at, so the guards around it matter
    more than the arithmetic."""

    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="rms_gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("6500"))
        Channel.objects.create(name="Booking.com")
        self.rec = RateRecommendation.objects.create(
            room_type=self.rt, current_rate=Decimal("6500"),
            recommended_rate=Decimal("7200"), reason="demand",
        )

    def test_only_open_recommendations_are_offered(self):
        RateRecommendation.objects.create(
            room_type=self.rt, current_rate=Decimal("6500"),
            recommended_rate=Decimal("5000"), reason="stale",
            status=RateRecommendation.DISMISSED)
        rows = self.client.get("/api/revenue/").data
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(str(rows[0]["recommended_rate"])), Decimal("7200"))

    def test_accepting_pushes_the_rate_and_closes_the_recommendation(self):
        r = self.client.post(f"/api/revenue/{self.rec.id}/accept/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["channels_pushed"], 1)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, RateRecommendation.ACCEPTED)
        self.assertEqual(
            ChannelRate.objects.get(room_type=self.rt).rate, Decimal("7200.00"))

    def test_the_same_recommendation_cannot_be_accepted_twice(self):
        """Otherwise a double-click pushes the rate again and writes a second
        audit entry for one decision."""
        self.client.post(f"/api/revenue/{self.rec.id}/accept/")
        again = self.client.post(f"/api/revenue/{self.rec.id}/accept/")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(ChannelPush.objects.count(), 1)

    def test_a_dismissed_recommendation_can_no_longer_be_accepted(self):
        self.client.post(f"/api/revenue/{self.rec.id}/dismiss/")
        r = self.client.post(f"/api/revenue/{self.rec.id}/accept/")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ChannelRate.objects.exists())


class ForecastTests(TestCase):
    """The 14-day demand curve the Revenue Manager plans against."""

    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="fc_gm", password="Tk9$mZ2pQw!7", role="General Manager"))

    def test_covers_exactly_a_fortnight_starting_today(self):
        from datetime import date
        rows = self.client.get("/api/revenue/forecast/").data
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0]["date"], date.today().isoformat())
        self.assertEqual(len({r["date"] for r in rows}), 14, "no repeated days")

    def test_demand_index_stays_within_bounds(self):
        """It is rendered as a percentage bar, so anything outside 10-100
        draws off the end of the chart."""
        for row in self.client.get("/api/revenue/forecast/").data:
            self.assertGreaterEqual(row["demand_index"], 10)
            self.assertLessEqual(row["demand_index"], 100)

    def test_weekends_are_flagged(self):
        rows = self.client.get("/api/revenue/forecast/").data
        from datetime import date as _d
        for row in rows:
            expected = _d.fromisoformat(row["date"]).weekday() >= 4
            self.assertEqual(row["weekend"], expected, row["date"])
