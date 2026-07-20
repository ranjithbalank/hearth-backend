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


class HousekeepingTaskTests(TestCase):
    """Attendant assignment: one open task per room, role-gated attendants,
    start/complete drive both the task and the room status, and the
    existing advance()-only flow (no task at all) stays fully supported."""

    def setUp(self):
        from apps.accounts.models import User
        from apps.rooms.models import Room, RoomType
        from rest_framework.test import APIClient
        rt = RoomType.objects.create(code="TSK", name="Std", base_rate=3000)
        self.room = Room.objects.create(number="701", room_type=rt, status=Room.VACANT_DIRTY)
        self.hk = APIClient()
        self.attendant = User.objects.create_user(
            username="att1", password="Tk9$mZ2pQw!7", role="Housekeeping")
        self.hk.force_authenticate(User.objects.create_user(
            username="sup1", password="Tk9$mZ2pQw!7", role="Housekeeping"))
        self.non_attendant = User.objects.create_user(
            username="fo2", password="Tk9$mZ2pQw!7", role="Front Office")

    def _create_task(self, assigned_to=None):
        return self.hk.post("/api/housekeeping/tasks/", {
            "room": self.room.id, "assigned_to": (assigned_to or self.attendant).id,
        }, format="json")

    def test_second_open_task_on_same_room_rejected(self):
        r1 = self._create_task()
        self.assertEqual(r1.status_code, 201, r1.data)
        r2 = self._create_task()
        self.assertEqual(r2.status_code, 400)
        self.assertIn("already has an open task", r2.data["detail"])

    def test_create_requires_housekeeping_role_attendant(self):
        r = self._create_task(assigned_to=self.non_attendant)
        self.assertEqual(r.status_code, 400)

    def test_checklist_is_snapshotted_from_active_items(self):
        from apps.housekeeping.models import ChecklistItem
        r = self._create_task()
        self.assertEqual(r.status_code, 201, r.data)
        labels = [c["label"] for c in r.data["checklist"]]
        self.assertEqual(labels, list(ChecklistItem.objects.filter(active=True)
                                      .order_by("sort_order", "label").values_list("label", flat=True)))
        self.assertTrue(all(c["done"] is False for c in r.data["checklist"]))

    def test_attendants_only_returns_housekeeping_role(self):
        r = self.hk.get("/api/housekeeping/tasks/attendants/")
        names = [a["name"] for a in r.data]
        self.assertIn(self.attendant.get_full_name() or self.attendant.username, names)
        self.assertNotIn(self.non_attendant.username, names)

    def test_reassign_validates_role_and_accepts_null(self):
        task_id = self._create_task().data["id"]
        bad = self.hk.post(f"/api/housekeeping/tasks/{task_id}/reassign/",
                           {"assigned_to": self.non_attendant.id}, format="json")
        self.assertEqual(bad.status_code, 400)
        cleared = self.hk.post(f"/api/housekeeping/tasks/{task_id}/reassign/",
                               {"assigned_to": None}, format="json")
        self.assertIsNone(cleared.data["assigned_to"])

    def test_toggle_item_flips_one_checklist_line(self):
        task_id = self._create_task().data["id"]
        r = self.hk.post(f"/api/housekeeping/tasks/{task_id}/toggle_item/",
                         {"index": 0}, format="json")
        self.assertTrue(r.data["checklist"][0]["done"])
        r = self.hk.post(f"/api/housekeeping/tasks/{task_id}/toggle_item/",
                         {"index": 0}, format="json")
        self.assertFalse(r.data["checklist"][0]["done"])

    def test_start_advances_room_and_leaves_cleaning_requested_alone(self):
        from apps.rooms.models import Room
        self.room.cleaning_requested = True
        self.room.cleaning_note = "guest left early"
        self.room.save(update_fields=["cleaning_requested", "cleaning_note"])
        task_id = self._create_task().data["id"]
        r = self.hk.post(f"/api/housekeeping/tasks/{task_id}/start/")
        self.assertEqual(r.data["status"], "in_progress")
        self.assertIsNotNone(r.data["started_at"])
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.CLEANING)
        self.assertTrue(self.room.cleaning_requested)   # start doesn't touch the flag

    def test_complete_advances_room_and_clears_cleaning_requested(self):
        from apps.rooms.models import Room
        self.room.cleaning_requested = True
        self.room.save(update_fields=["cleaning_requested"])
        task_id = self._create_task().data["id"]
        self.hk.post(f"/api/housekeeping/tasks/{task_id}/start/")
        r = self.hk.post(f"/api/housekeeping/tasks/{task_id}/complete/",
                         {"linen_issued": {"Bath towel": 2}, "note": "all good"}, format="json")
        self.assertEqual(r.data["status"], "done")
        self.assertIsNotNone(r.data["completed_at"])
        self.assertEqual(r.data["linen_issued"], {"Bath towel": 2})
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.VACANT_CLEAN)
        self.assertFalse(self.room.cleaning_requested)
        # Done tasks drop out of the open-tasks board.
        open_tasks = self.hk.get("/api/housekeeping/tasks/?open=1")
        self.assertNotIn(task_id, [t["id"] for t in open_tasks.data])

    def test_advance_bridges_assigned_task_forward(self):
        from django.urls import reverse
        task_id = self._create_task().data["id"]
        self.hk.patch(reverse("housekeeping-advance", args=[self.room.id]))   # dirty -> cleaning
        r = self.hk.get(f"/api/housekeeping/tasks/{task_id}/")
        self.assertEqual(r.data["status"], "in_progress")
        self.assertIsNotNone(r.data["started_at"])
        self.hk.patch(reverse("housekeeping-advance", args=[self.room.id]))   # cleaning -> vacant_clean
        r = self.hk.get(f"/api/housekeeping/tasks/{task_id}/")
        self.assertEqual(r.data["status"], "done")

    def test_advance_is_a_no_op_bridge_when_no_task_exists(self):
        from django.urls import reverse
        from apps.housekeeping.models import HousekeepingTask
        from apps.rooms.models import Room
        plain_room = Room.objects.create(number="702", room_type=self.room.room_type,
                                         status=Room.VACANT_DIRTY)
        r = self.hk.patch(reverse("housekeeping-advance", args=[plain_room.id]))
        self.assertEqual(r.data["status"], "cleaning")
        self.assertEqual(HousekeepingTask.objects.filter(room=plain_room).count(), 0)


class HousekeepingMastersTests(TestCase):
    """Cleaning-checklist and linen-item masters: settings-gated writes,
    open reads (same MasterViewSet shape as Kitchen Stations)."""

    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.gm = APIClient()
        self.gm.force_authenticate(User.objects.create_user(
            username="gmhk", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.hk = APIClient()
        self.hk.force_authenticate(User.objects.create_user(
            username="hkuser", password="Tk9$mZ2pQw!7", role="Housekeeping"))

    def test_checklist_item_crud_is_settings_gated_but_readable_by_any_role(self):
        r = self.hk.get("/api/housekeeping/checklist-items/")
        self.assertEqual(r.status_code, 200)
        r = self.hk.post("/api/housekeeping/checklist-items/", {"label": "Test towels"}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.gm.post("/api/housekeeping/checklist-items/", {"label": "Test towels"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_linen_item_crud_is_settings_gated(self):
        r = self.hk.post("/api/housekeeping/linen-items/", {"name": "Bath robe", "par_per_room": 2}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.gm.post("/api/housekeeping/linen-items/", {"name": "Bath robe", "par_per_room": 2}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
