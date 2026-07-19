from django.test import TestCase


class WorkOrderRoomStateTests(TestCase):
    """A repair ticket must never corrupt the occupancy state: vacant rooms
    go out of order, occupied rooms stay with their guest."""

    def setUp(self):
        from apps.accounts.models import User
        from apps.rooms.models import Room, RoomType
        from rest_framework.test import APIClient
        rt = RoomType.objects.create(code="WO", name="Std", base_rate=3000)
        self.vacant = Room.objects.create(number="601", room_type=rt, status=Room.VACANT_CLEAN)
        self.occupied = Room.objects.create(number="602", room_type=rt, status=Room.OCCUPIED)
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hkwo", password="Tk9$mZ2pQw!7", role="Housekeeping"))

    def test_vacant_room_goes_ooo_and_returns_dirty(self):
        from apps.rooms.models import Room
        r = self.client.post("/api/work-orders/", {"room": self.vacant.id,
                                                   "title": "AC leaking"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["raised_by"], "hkwo")
        self.vacant.refresh_from_db()
        self.assertEqual(self.vacant.status, Room.OOO)
        self.assertEqual(self.vacant.ooo_reason, "AC leaking")
        # start → done returns it to service, dirty for housekeeping.
        wo = r.data["id"]
        self.client.patch(f"/api/work-orders/{wo}/advance/")
        self.client.patch(f"/api/work-orders/{wo}/advance/")
        self.vacant.refresh_from_db()
        self.assertEqual(self.vacant.status, Room.VACANT_DIRTY)

    def test_occupied_room_keeps_its_guest(self):
        from apps.rooms.models import Room
        r = self.client.post("/api/work-orders/", {"room": self.occupied.id,
                                                   "title": "TV remote broken"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.occupied.refresh_from_db()
        self.assertEqual(self.occupied.status, Room.OCCUPIED)   # never clobbered
        # Completing the job leaves the occupied room alone too.
        wo = r.data["id"]
        self.client.patch(f"/api/work-orders/{wo}/advance/")
        self.client.patch(f"/api/work-orders/{wo}/advance/")
        self.occupied.refresh_from_db()
        self.assertEqual(self.occupied.status, Room.OCCUPIED)


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
