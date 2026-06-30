from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Ingredient


class InventoryApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="c", password="Tk9$mZ2pQw!7", role="F&B Cashier"))
        Ingredient.objects.create(name="Butter", unit="kg", current_stock=Decimal("2"),
                                  reorder_level=Decimal("5"))
        Ingredient.objects.create(name="Rice", unit="kg", current_stock=Decimal("40"),
                                  reorder_level=Decimal("10"))

    def test_low_stock_flags_below_par(self):
        r = self.client.get(reverse("ingredient-low-stock"))
        names = [i["name"] for i in r.data["items"]]
        self.assertIn("Butter", names)
        self.assertNotIn("Rice", names)

    def test_adjust_updates_stock(self):
        ing = Ingredient.objects.get(name="Rice")
        r = self.client.post(reverse("ingredient-adjust", args=[ing.id]),
                             {"qty": "-5", "reason": "spoilage"}, format="json")
        self.assertEqual(r.status_code, 200)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal("35.000"))
