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

    def test_event_timing_round_trips(self):
        r = self._create(start_time="19:30", end_time="23:45")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["start_time"], "19:30")
        self.assertEqual(r.data["end_time"], "23:45")
        e = Event.objects.get(pk=r.data["id"])
        self.assertEqual(e.start_time.strftime("%H:%M"), "19:30")
        # BEO PDF renders with the timing row.
        pdf = self.client.get(reverse("banquet-beo-pdf", args=[e.id]))
        self.assertEqual(pdf.status_code, 200)

    def test_double_booking_blocked(self):
        e = self._create().data
        self.client.post(reverse("banquet-confirm", args=[e["id"]]))
        r = self._create(title="Clash")
        self.assertEqual(r.status_code, 409)

    def test_same_hall_same_day_non_overlapping_allowed(self):
        # A lunch and an evening reception in the same hall don't clash.
        e = self._create(start_time="10:00", end_time="14:00").data
        self.client.post(reverse("banquet-confirm", args=[e["id"]]))
        r = self._create(title="Evening", start_time="18:00", end_time="23:00")
        self.assertEqual(r.status_code, 201)

    def test_same_hall_overlapping_time_blocked(self):
        e = self._create(start_time="10:00", end_time="14:00").data
        self.client.post(reverse("banquet-confirm", args=[e["id"]]))
        r = self._create(title="Overlap", start_time="13:00", end_time="17:00")
        self.assertEqual(r.status_code, 409)

    def test_confirm_blocked_when_slot_taken(self):
        # Two tentative enquiries may overlap; confirming the second is blocked.
        e1 = self._create(start_time="10:00", end_time="14:00").data
        e2 = self._create(title="B", start_time="12:00", end_time="16:00").data
        self.assertEqual(self.client.post(reverse("banquet-confirm", args=[e1["id"]])).status_code, 200)
        r = self.client.post(reverse("banquet-confirm", args=[e2["id"]]))
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

    def test_adjust_enquiry_before_billing(self):
        e = self._create(covers=100).data
        r = self.client.patch(reverse("banquet-detail", args=[e["id"]]),
                              {"covers": 150, "package_amount": 250000,
                               "food_pref": "veg", "food_veg": 150, "veg_rate": 900},
                              format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["covers"], 150)
        self.assertEqual(r.data["catering_amount"], "135000.00")  # 150 × 900
        self.assertEqual(r.data["bill_subtotal"], "385000.00")    # 250000 + 135000

    def test_cannot_adjust_billed_event(self):
        e = self._create().data
        self.client.post(reverse("banquet-bill", args=[e["id"]]))
        r = self.client.patch(reverse("banquet-detail", args=[e["id"]]), {"covers": 10}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_bill_applies_18pct_gst(self):
        e = self._create(package_amount=100000).data
        r = self.client.post(reverse("banquet-bill", args=[e["id"]]))
        self.assertEqual(r.data["tax"]["tax"], "18000.00")

    def test_bill_includes_catering_and_balance(self):
        # Package 100000 + catering (80×850 + 40×1050 = 110000) = 210000 subtotal.
        e = self._create(package_amount=100000, deposit=50000, food_pref="both",
                         food_veg=80, food_nonveg=40, veg_rate=850, nonveg_rate=1050).data
        self.assertEqual(e["catering_amount"], "110000.00")
        self.assertEqual(e["bill_subtotal"], "210000.00")
        r = self.client.post(reverse("banquet-bill", args=[e["id"]])).data
        self.assertEqual(r["subtotal"], "210000.00")
        self.assertEqual(r["tax"]["tax"], "37800.00")          # 18% of 210000
        self.assertEqual(r["tax"]["total"], "247800.00")
        self.assertEqual(r["balance"], "197800.00")            # total − 50000 deposit
