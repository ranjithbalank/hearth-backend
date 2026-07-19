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

    def test_room_import_needs_room_master(self):
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="foimp", password="Tk9$mZ2pQw!7", role="Front Office"))
        # Front Office reads the live grid, but can't mass-create rooms.
        self.assertEqual(fo.get("/api/rooms/import/").status_code, 403)
