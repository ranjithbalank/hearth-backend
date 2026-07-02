from django.test import TestCase


class CleaningRequestTests(TestCase):
    """Front desk requests cleaning → housekeeping board + notification → serviced."""

    def setUp(self):
        from apps.accounts.models import User
        from apps.rooms.models import Room, RoomType
        from rest_framework.test import APIClient
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=3000)
        self.room = Room.objects.create(number="101", room_type=rt, status=Room.OCCUPIED)
        self.frontdesk = APIClient()
        self.frontdesk.force_authenticate(User.objects.create_user(
            username="fo1", password="Tk9$mZ2pQw!7", role="Front Office"))
        self.hk = APIClient()
        self.hk.force_authenticate(User.objects.create_user(
            username="hk1", password="Tk9$mZ2pQw!7", role="Housekeeping"))

    def test_request_flag_notify_and_service(self):
        from django.urls import reverse
        # Front desk raises the request (occupied room = make-up-room).
        r = self.frontdesk.post(reverse("housekeeping-request-cleaning", args=[self.room.id]),
                                {"note": "guest asked for turndown"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["cleaning_requested"])
        # Housekeeping sees it in their notifications.
        r = self.hk.get("/api/notifications/")
        titles = [a["title"] for a in r.data["alerts"]]
        self.assertTrue(any("cleaning request" in t for t in titles))
        # Servicing the occupied room clears the flag, status stays occupied.
        r = self.hk.patch(reverse("housekeeping-advance", args=[self.room.id]))
        self.assertFalse(r.data["cleaning_requested"])
        self.assertEqual(r.data["status"], "occupied")
