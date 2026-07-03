from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient, apply_movement
from apps.pos.models import Category, MenuItem, Order, OrderLine
from apps.recipes.models import Recipe, RecipeLine


class RestaurantReportsTests(TestCase):
    """§7 analytics: recipe consumption, sales vs consumption, purchase vs
    consumption, food cost, menu item profitability."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gmr", password="Tk9$mZ2pQw!7", role="General Manager"))
        cat = Category.objects.create(name="Main")
        self.rice = Ingredient.objects.create(name="Rice", unit="kg",
                                              current_stock=Decimal("20"),
                                              unit_cost=Decimal("60"))
        self.item = MenuItem.objects.create(name="Fried Rice", category=cat,
                                            price=Decimal("200"), gst_rate=Decimal("0"))
        rec = Recipe.objects.create(menu_item=self.item)
        RecipeLine.objects.create(recipe=rec, ingredient=self.rice, qty=Decimal("0.25"))
        # One settled order of 2 plates; matching purchase + consumption ledger.
        order = Order.objects.create(mode=Order.TAKEAWAY, status=Order.SETTLED)
        OrderLine.objects.create(order=order, menu_item=self.item, qty=2,
                                 unit_price=Decimal("200"), kot_fired=True)
        apply_movement(self.rice, "receipt", Decimal("10"), source="PO:1")
        apply_movement(self.rice, "consumption", Decimal("-0.5"), source=f"order:{order.id}")

    def _view(self, report):
        r = self.client.get(f"/api/reports/view/?report={report}")
        self.assertEqual(r.status_code, 200, report)
        return r.data

    def test_recipe_wise_consumption(self):
        data = self._view("recipe_consumption")
        self.assertEqual(data["title"], "Recipe-wise Consumption")
        row = next(b for b in data["bars"] if b["name"] == "Fried Rice")
        self.assertEqual(row["value"], 30.0)  # 2 plates × 0.25 kg × ₹60

    def test_daily_sales_vs_consumption(self):
        data = self._view("sales_vs_consumption")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(Decimal(kpis["F&B sales (14d)"]), Decimal("400"))
        self.assertEqual(Decimal(kpis["Consumption cost (14d)"]), Decimal("30"))
        self.assertEqual(kpis["Food cost"], "7.5%")
        self.assertEqual(len(data["bars"]), 1)

    def test_purchase_vs_consumption(self):
        data = self._view("purchase_vs_consumption")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(Decimal(kpis["Purchased value"]), Decimal("600"))  # 10 kg × 60
        self.assertEqual(Decimal(kpis["Consumed value"]), Decimal("30"))

    def test_food_cost(self):
        data = self._view("food_cost")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(Decimal(kpis["Ingredient cost"]), Decimal("30"))
        self.assertEqual(kpis["Food cost"], "7.5%")

    def test_item_profitability(self):
        data = self._view("item_profitability")
        row = next(b for b in data["bars"] if b["name"] == "Fried Rice")
        self.assertEqual(row["value"], 370.0)  # 400 revenue − 30 recipe cost
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(Decimal(kpis["Gross profit (F&B)"]), Decimal("370"))

    def test_export_csv_for_restaurant_report(self):
        r = self.client.get("/api/reports/export/?report=food_cost&fmt=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Food cost", r.content.decode())
