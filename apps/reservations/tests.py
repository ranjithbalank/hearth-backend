from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.frontoffice import services as fo
from apps.rooms.models import Room, RoomType

from .models import Reservation


class ReservationOpsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.mgr = User.objects.create_user(username="m1", password="Tk9$mZ2pQw!7",
                                             role="General Manager")
        self.client.force_authenticate(self.mgr)
        self.rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("6500"))
        self.r1 = Room.objects.create(number="201", room_type=self.rt, status=Room.VACANT_CLEAN)
        self.r2 = Room.objects.create(number="202", room_type=self.rt, status=Room.VACANT_CLEAN)
        self.resv = Reservation.objects.create(
            guest_name="Tester", room_type=self.rt, checkin_date=date.today(),
            checkout_date=date.today() + timedelta(days=2), nights=2, rate=Decimal("6500"))

    def test_availability_decrements_with_holds(self):
        r = self.client.get(reverse("reservation-availability"))
        dlx = [x for x in r.data if x["room_type"] == "DLX"][0]
        self.assertEqual(dlx["physical"], 2)
        self.assertEqual(dlx["held"], 1)        # the booked reservation
        self.assertEqual(dlx["available"], 1)

    def test_room_move_after_checkin(self):
        fo.check_in(self.resv, self.r1)
        r = self.client.post(reverse("reservation-room-move", args=[self.resv.id]),
                             {"room": self.r2.id}, format="json")
        self.assertEqual(r.status_code, 200)
        self.r1.refresh_from_db(); self.r2.refresh_from_db()
        self.assertEqual(self.r1.status, Room.VACANT_DIRTY)
        self.assertEqual(self.r2.status, Room.OCCUPIED)

    def test_no_show_posts_penalty(self):
        r = self.client.post(reverse("reservation-no-show", args=[self.resv.id]), {}, format="json")
        self.assertEqual(r.status_code, 200)
        self.resv.refresh_from_db()
        self.assertEqual(self.resv.status, Reservation.NO_SHOW)
        self.assertTrue(self.resv.folio.lines.exists())
