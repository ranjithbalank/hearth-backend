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

    def test_housekeeping_sees_cleaning_alert_after_checkout(self):
        # A vacated (dirty) room surfaces a housekeeping cleaning alert.
        from apps.rooms.models import Room, RoomType
        rt = RoomType.objects.create(code="STD", name="Standard", base_rate=Decimal("2000"))
        Room.objects.create(number="101", room_type=rt, status=Room.VACANT_DIRTY)
        self._login("Housekeeping")
        resp = self.client.get(reverse("notifications"))
        titles = [a["title"] for a in resp.data["alerts"]]
        self.assertTrue(any("awaiting cleaning" in t for t in titles))
