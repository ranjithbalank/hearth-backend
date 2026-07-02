from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Event, FunctionSpace


class BanquetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.hall = FunctionSpace.objects.create(name="Ballroom", capacity=300)

    def _create(self, **over):
        body = {"title": "Wedding", "space": self.hall.id, "event_date": "2026-12-01",
                "covers": 200, "package_amount": 400000}
        body.update(over)
        return self.client.post(reverse("banquet-list"), body, format="json")

    def test_create_event(self):
        r = self._create()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["status"], "tentative")

    def test_double_booking_blocked(self):
        e = self._create().data
        self.client.post(reverse("banquet-confirm", args=[e["id"]]))
        r = self._create(title="Clash")
        self.assertEqual(r.status_code, 409)

    def test_capacity_guard(self):
        r = self._create(covers=9999)
        self.assertEqual(r.status_code, 400)

    def test_confirm_fires_beo_when_catering(self):
        e = self._create(food_pref="both", food_veg=120, food_nonveg=100).data
        self.assertEqual(e["food_covers"], 220)  # derived from the veg/nonveg split
        self.client.post(reverse("banquet-confirm", args=[e["id"]]))
        ev = Event.objects.get(pk=e["id"])
        self.assertEqual(ev.beo_status, "pending")

    def test_bill_applies_18pct_gst(self):
        e = self._create(package_amount=100000).data
        r = self.client.post(reverse("banquet-bill", args=[e["id"]]))
        self.assertEqual(r.data["tax"]["tax"], "18000.00")
        self.assertEqual(r.data["tax"]["total"], "118000.00")
