from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient


class NotificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Ingredient.objects.create(name="Butter", unit="kg", current_stock=Decimal("1"),
                                  reorder_level=Decimal("5"))

    def _login(self, role):
        u = User.objects.create_user(username=f"u_{role}", password="Tk9$mZ2pQw!7", role=role)
        self.client.force_authenticate(u)

    def test_manager_sees_inventory_alert(self):
        # Stores/inventory is management-scoped; the GM sees the low-stock alert.
        self._login("General Manager")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertTrue(any("Low stock: Butter" in t for t in titles))

    def test_cashier_does_not_see_inventory_alert(self):
        # Cashier is scoped to POS only -> inventory alert is filtered out (RBAC).
        self._login("F&B Cashier")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertFalse(any("Low stock" in t for t in titles))

    def test_housekeeping_does_not_see_inventory_alert(self):
        # Housekeeping has no 'inventory' module access -> alert filtered out (RBAC-scoped).
        self._login("Housekeeping")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertFalse(any("Low stock" in t for t in titles))

    def test_store_keeper_notified_when_indent_approved_chef_is_not(self):
        """Chef requests chicken → Restaurant Manager approves → the Store
        Keeper gets pinged to issue it. The chef who raised it, and other
        matreq-holders with no issuing authority, don't need this alert."""
        chicken = Ingredient.objects.create(name="Chicken", unit="kg",
                                            current_stock=Decimal("10"))
        chef = User.objects.create_user(username="chefnotif", password="Tk9$mZ2pQw!7",
                                        role="Chef / Kitchen")
        chef_client = APIClient()
        chef_client.force_authenticate(chef)
        r = chef_client.post("/api/material-requests/", {
            "department": "Kitchen", "lines": [{"ingredient": chicken.id, "qty": "2"}],
        }, format="json")
        req_id = r.data["id"]

        rm = User.objects.create_user(username="rmnotif", password="Tk9$mZ2pQw!7",
                                      role="Restaurant Manager")
        rm_client = APIClient()
        rm_client.force_authenticate(rm)
        rm_client.post(f"/api/material-requests/{req_id}/advance/")

        self._login("Store Keeper")
        titles = [a["title"] for a in self.client.get(reverse("notifications")).data["alerts"]]
        self.assertTrue(any("approved — ready to issue" in t for t in titles), titles)

        # The chef who raised it doesn't need the "go issue it" alert.
        titles_chef = [a["title"] for a in chef_client.get("/api/notifications/").data["alerts"]]
        self.assertFalse(any("ready to issue" in t for t in titles_chef))

        # Front Office has matreq access too, but issues nothing.
        self._login("Front Office")
        titles_fo = [a["title"] for a in self.client.get(reverse("notifications")).data["alerts"]]
        self.assertFalse(any("ready to issue" in t for t in titles_fo))

    def test_housekeeping_sees_cleaning_alert_after_checkout(self):
        # A vacated (dirty) room surfaces a housekeeping cleaning alert.
        from apps.rooms.models import Room, RoomType
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("2000"))
        Room.objects.create(number="101", room_type=rt, status=Room.VACANT_DIRTY)
        self._login("Housekeeping")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertTrue(any("awaiting cleaning" in t for t in titles))
