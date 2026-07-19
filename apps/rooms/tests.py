from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Room, RoomType


class RoomImportTests(TestCase):
    """Bulk room onboarding via CSV — auto-creates room types, skips existing
    numbers, and needs the Room Master screen (not just livegrid read access)."""

    def _csv(self, body):
        from io import BytesIO
        f = BytesIO(body.encode())
        f.name = "rooms.csv"
        return f

    def test_room_import_creates_rooms_and_types(self):
        gm = APIClient()
        gm.force_authenticate(User.objects.create_user(
            username="gmrooms", password="Tk9$mZ2pQw!7", role="General Manager"))
        r = gm.get("/api/rooms/import/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("number,room_type_code", r.content.decode())

        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4000"))
        Room.objects.create(number="101", room_type=rt)
        body = ("number,room_type_code,room_type_name,base_rate,floor\n"
                "101,STD,Standard,4500,1\n"     # exists → skipped
                "102,STD,Standard,4500,1\n"
                "201,DLX,Deluxe,6500,2\n")
        r = gm.post("/api/rooms/import/", {"file": self._csv(body)}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["created"], 2)
        self.assertEqual(r.data["skipped_existing"], ["101"])
        dlx = RoomType.objects.get(code="DLX")
        self.assertEqual(dlx.base_rate, Decimal("6500"))   # created on the fly
        self.assertEqual(Room.objects.get(number="201").floor, 2)
        # Existing STD type keeps its own rate — get_or_create never overwrites.
        rt.refresh_from_db()
        self.assertEqual(rt.base_rate, Decimal("4000"))

    def test_infer_floors_from_room_numbers(self):
        """The one-click floor fixer: 101→1, 204→2, 1203→12; non-numeric
        numbers untouched; room-master gated."""
        gm = APIClient()
        gm.force_authenticate(User.objects.create_user(
            username="gmfloors", password="Tk9$mZ2pQw!7", role="General Manager"))
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4000"))
        r101 = Room.objects.create(number="101", room_type=rt)          # floor default 1
        r204 = Room.objects.create(number="204", room_type=rt)
        r1203 = Room.objects.create(number="1203", room_type=rt)
        cabin = Room.objects.create(number="Cabin A", room_type=rt, floor=1)

        r = gm.post("/api/rooms/infer_floors/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["changed"], 2)   # 204 and 1203; 101 already right
        self.assertEqual(Room.objects.get(pk=r204.pk).floor, 2)
        self.assertEqual(Room.objects.get(pk=r1203.pk).floor, 12)
        self.assertEqual(Room.objects.get(pk=r101.pk).floor, 1)
        self.assertEqual(Room.objects.get(pk=cabin.pk).floor, 1)  # untouched

        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="fofloors", password="Tk9$mZ2pQw!7", role="Front Office"))
        self.assertEqual(fo.post("/api/rooms/infer_floors/").status_code, 403)

    def test_new_room_infers_floor_from_number(self):
        """Creating a room without a floor no longer lands on floor 1 —
        the number decides (500 → 5); an explicit floor still wins."""
        gm = APIClient()
        gm.force_authenticate(User.objects.create_user(
            username="gmnew", password="Tk9$mZ2pQw!7", role="General Manager"))
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("4000"))
        r = gm.post("/api/rooms/", {"number": "500", "room_type": rt.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["floor"], 5)
        r = gm.post("/api/rooms/", {"number": "601", "room_type": rt.id, "floor": 3},
                    format="json")
        self.assertEqual(r.data["floor"], 3)   # explicit floor wins

    def test_room_import_needs_room_master(self):
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="foimp", password="Tk9$mZ2pQw!7", role="Front Office"))
        # Front Office reads the live grid, but can't mass-create rooms.
        self.assertEqual(fo.get("/api/rooms/import/").status_code, 403)
