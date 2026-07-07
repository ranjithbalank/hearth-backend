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
        """Zomato/Swiggy cumulative report shows GROSS, commission taken and
        NET realization — the number owners actually care about."""
        from apps.accounts.views import get_property
        prop = get_property()
        prop.zomato_commission_pct = Decimal("20")
        prop.swiggy_commission_pct = Decimal("25")
        prop.save()
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
        # Gross: 200 (zomato) + 400 (swiggy) = 600
        self.assertEqual(Decimal(kpis["Gross sales (settled)"]), Decimal("600"))
        # Net: 200×0.80 + 400×0.75 = 160 + 300 = 460; commission = 140
        self.assertEqual(Decimal(kpis["Net realization"]), Decimal("460.00"))
        self.assertEqual(Decimal(kpis["Commission taken"]), Decimal("140.00"))
        self.assertEqual(kpis["Orders received"], 3)
        bars = {b["name"]: b["value"] for b in data["bars"]}
        self.assertEqual(bars["Swiggy"], 300.0)   # net, not gross
        self.assertEqual(bars["Zomato"], 160.0)
        # Records carry gross/commission/net per order.
        row = next(r for r in data["records"]["rows"] if r[2] == "Z-1001")
        self.assertEqual(row[4], "200.00")   # gross
        self.assertEqual(row[5], "20.00")    # commission %
        self.assertEqual(row[6], "160.00")   # net
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


class ReportsRbacTests(TestCase):
    """Having the 'reports' module only gates opening the Reports screen —
    these confirm WHICH report each non-universal role may actually pull is
    scoped too (the gap where Restaurant Manager could previously export
    Guest KYC, GST and accounting data it has no other access to)."""

    def _client(self, role, username):
        c = APIClient()
        c.force_authenticate(User.objects.create_user(
            username=username, password="Tk9$mZ2pQw!7", role=role))
        return c

    def test_restaurant_manager_scoped_to_restaurant_reports(self):
        rm = self._client("Restaurant Manager", "rmrpt")
        self.assertEqual(rm.get("/api/reports/view/?report=food_cost").status_code, 200)
        for report in ["guests", "tax", "accounting", "source", "occupancy"]:
            self.assertEqual(rm.get(f"/api/reports/view/?report={report}").status_code, 403)
            self.assertEqual(rm.get(f"/api/reports/export/?report={report}").status_code, 403)

    def test_hotel_manager_scoped_to_hotel_reports(self):
        hm = self._client("Hotel Manager", "hmrpt")
        for report in ["source", "occupancy"]:
            self.assertEqual(hm.get(f"/api/reports/view/?report={report}").status_code, 200)
        for report in ["guests", "tax", "accounting", "food_cost", "recipe_consumption"]:
            self.assertEqual(hm.get(f"/api/reports/view/?report={report}").status_code, 403)

    def test_finance_scoped_to_money_reports(self):
        fin = self._client("Finance", "finrpt")
        for report in ["tax", "accounting"]:
            self.assertEqual(fin.get(f"/api/reports/view/?report={report}").status_code, 200)
        for report in ["guests", "source", "occupancy"]:
            self.assertEqual(fin.get(f"/api/reports/view/?report={report}").status_code, 403)

    def test_guest_kyc_is_full_access_only(self):
        """Guest KYC (raw ID numbers) — the tightest gate: not even CEO/Finance."""
        for role, uname in [("CEO", "ceorpt"), ("Finance", "finrpt2"),
                            ("Restaurant Manager", "rmrpt2"), ("Hotel Manager", "hmrpt2")]:
            c = self._client(role, uname)
            self.assertEqual(c.get("/api/reports/view/?report=guests").status_code, 403)
            self.assertEqual(c.get("/api/reports/export/?report=guests").status_code, 403)
        gm = self._client("General Manager", "gmrpt2")
        self.assertEqual(gm.get("/api/reports/export/?report=guests").status_code, 200)

    def test_sales_summary_scoped_like_dashboard(self):
        """Restaurant Manager's Sales Summary shows F&B only, no room KPIs —
        and vice versa for Hotel Manager. Same split as the Dashboard."""
        rm = self._client("Restaurant Manager", "rmsales")
        data = rm.get("/api/reports/view/?report=sales").data
        labels = {k["label"] for k in data["kpis"]}
        self.assertIn("F&B sales", labels)
        self.assertNotIn("Occupancy", labels)

        hm = self._client("Hotel Manager", "hmsales")
        data = hm.get("/api/reports/view/?report=sales").data
        labels = {k["label"] for k in data["kpis"]}
        self.assertIn("Occupancy", labels)
        self.assertNotIn("F&B sales", labels)

    def test_catalogue_hides_groups_the_role_cant_open(self):
        rm = self._client("Restaurant Manager", "rmcat")
        groups = {g["group"] for g in rm.get("/api/reports/catalogue/").data["groups"]}
        self.assertIn("Restaurant Inventory", groups)
        self.assertNotIn("Guests", groups)
        self.assertNotIn("Finance", groups)
        self.assertNotIn("Rooms", groups)

        hm = self._client("Hotel Manager", "hmcat")
        groups = {g["group"] for g in hm.get("/api/reports/catalogue/").data["groups"]}
        self.assertIn("Rooms", groups)
        self.assertNotIn("Guests", groups)
        self.assertNotIn("Restaurant Inventory", groups)
