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

    def test_aggregator_report_and_records(self):
        """Zomato/Swiggy cumulative report + order-level records export."""
        z = Order.objects.create(mode=Order.DELIVERY, status=Order.SETTLED,
                                 source_platform="zomato", external_ref="Z-1001")
        OrderLine.objects.create(order=z, menu_item=self.item, qty=1,
                                 unit_price=Decimal("200"), kot_fired=True)
        s = Order.objects.create(mode=Order.DELIVERY, status=Order.SETTLED,
                                 source_platform="swiggy", external_ref="S-2002")
        OrderLine.objects.create(order=s, menu_item=self.item, qty=2,
                                 unit_price=Decimal("200"), kot_fired=True)
        # A received-but-unsettled order counts as received, not as sales.
        Order.objects.create(mode=Order.DELIVERY, status=Order.KOT_FIRED,
                             source_platform="zomato", external_ref="Z-1002")
        data = self._view("aggregator")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(Decimal(kpis["Online sales (settled)"]), Decimal("600"))
        self.assertEqual(kpis["Orders received"], 3)
        bars = {b["name"]: b["value"] for b in data["bars"]}
        self.assertEqual(bars["Swiggy"], 400.0)
        self.assertEqual(bars["Zomato"], 200.0)
        # Records export: one row per order with its reference.
        r = self.client.get("/api/reports/export/?report=aggregator&fmt=csv")
        body = r.content.decode()
        self.assertIn("Z-1001", body)
        self.assertIn("S-2002", body)
        self.assertIn("Z-1002", body)   # unsettled orders appear in records too

    def test_export_csv_for_restaurant_report(self):
        r = self.client.get("/api/reports/export/?report=food_cost&fmt=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Food cost", r.content.decode())
