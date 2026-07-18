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


class OperationalReportsTests(TestCase):
    """Arrivals & departures, night audit history, no-show & cancellation,
    discounts & voids — the four front-office/floor operational reports."""

    def setUp(self):
        from django.utils import timezone
        from apps.frontoffice.models import NightAuditRun
        from apps.reservations.models import Reservation
        from apps.rooms.models import RoomType
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gmops", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.today = timezone.localdate()
        rt = RoomType.objects.create(code="DLX", name="Deluxe", base_rate=Decimal("5000"))
        Reservation.objects.create(guest_name="Arriving Guest", room_type=rt,
                                   checkin_date=self.today, checkout_date=self.today,
                                   nights=2, rate=Decimal("5000"))
        Reservation.objects.create(guest_name="Ghost Guest", room_type=rt,
                                   checkin_date=self.today, checkout_date=self.today,
                                   nights=3, rate=Decimal("4000"),
                                   status=Reservation.NO_SHOW)
        Reservation.objects.create(guest_name="Backed Out", room_type=rt,
                                   checkin_date=self.today, checkout_date=self.today,
                                   nights=1, rate=Decimal("6000"), source="ota",
                                   status=Reservation.CANCELLED)
        NightAuditRun.objects.create(business_date=self.today, rooms_posted=3,
                                     room_revenue=Decimal("15000"),
                                     tax_posted=Decimal("1800"), completed=True)
        # One settled bill with a manual 10% discount, one voided bill.
        cat = Category.objects.create(name="Ops")
        item = MenuItem.objects.create(name="Thali", category=cat,
                                       price=Decimal("500"), gst_rate=Decimal("0"))
        disc = Order.objects.create(mode=Order.DINEIN, status=Order.SETTLED,
                                    discount_kind=Order.DISC_PERCENT,
                                    discount_value=Decimal("10"),
                                    discount_reason="regular guest")
        OrderLine.objects.create(order=disc, menu_item=item, qty=2,
                                 unit_price=Decimal("500"), kot_fired=True)
        void = Order.objects.create(mode=Order.DINEIN, status=Order.SETTLED,
                                    discount_reason="VOID by cashier (override gm)")
        OrderLine.objects.create(order=void, menu_item=item, qty=1,
                                 unit_price=Decimal("500"), kot_fired=True)

    def _view(self, report):
        r = self.client.get(f"/api/reports/view/?report={report}")
        self.assertEqual(r.status_code, 200, report)
        return r.data

    def test_arrivals_and_departures(self):
        data = self._view("arrivals")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        arrivals_key = next(k for k in kpis if k.startswith("Arrivals"))
        self.assertEqual(kpis[arrivals_key], 1)  # no-show/cancelled don't count
        movements = {r[0] for r in data["records"]["rows"]}
        self.assertIn("Arrival", movements)
        guests = {r[1] for r in data["records"]["rows"]}
        self.assertNotIn("Ghost Guest", guests)

    def test_night_audit_history(self):
        data = self._view("night_audit")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(kpis["Room nights posted"], 3)
        self.assertEqual(Decimal(kpis["Room revenue posted"]), Decimal("15000"))
        self.assertEqual(data["bars"][0]["value"], 15000.0)
        self.assertEqual(data["records"]["rows"][0][4], "done")

    def test_noshow_and_cancellation(self):
        data = self._view("noshow")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(kpis["Cancellations"], 1)
        self.assertEqual(kpis["No-shows"], 1)
        self.assertEqual(kpis["Room nights lost"], 4)  # 3 + 1
        # 3×4000 + 1×6000
        self.assertEqual(Decimal(kpis["Value lost"]), Decimal("18000"))
        bars = {b["name"]: b["value"] for b in data["bars"]}
        self.assertEqual(bars["OTA"], 6000.0)

    def test_discounts_and_voids(self):
        data = self._view("discounts")
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(kpis["Discounted bills"], 1)
        self.assertEqual(Decimal(kpis["Discounts given"]), Decimal("100"))  # 10% of 1000
        self.assertEqual(kpis["Voided bills"], 1)
        bars = {b["name"]: b["value"] for b in data["bars"]}
        self.assertEqual(bars["Manual"], 100.0)
        self.assertEqual(bars["Voids"], 500.0)
        types = {r[2] for r in data["records"]["rows"]}
        self.assertEqual(types, {"Discount", "Void"})

    def test_operational_exports_are_record_level(self):
        for report, needle in [("arrivals", "Arriving Guest"),
                               ("noshow", "Ghost Guest"),
                               ("night_audit", "15000"),
                               ("discounts", "regular guest")]:
            r = self.client.get(f"/api/reports/export/?report={report}&fmt=csv")
            self.assertEqual(r.status_code, 200, report)
            self.assertIn(needle, r.content.decode(), report)

    def test_date_range_filters_reports(self):
        """?from/&to windows the data: a future-only window shows nothing,
        a window covering today shows everything, junk dates are ignored."""
        from datetime import timedelta
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        data = self.client.get(
            f"/api/reports/view/?report=discounts&from={tomorrow}").data
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(kpis["Discounted bills"], 0)
        self.assertEqual(kpis["Voided bills"], 0)

        data = self.client.get(
            f"/api/reports/view/?report=night_audit&from={self.today}&to={self.today}").data
        self.assertEqual(len(data["records"]["rows"]), 1)
        data = self.client.get(
            f"/api/reports/view/?report=night_audit&to={(self.today - timedelta(days=1)).isoformat()}").data
        self.assertEqual(len(data["records"]["rows"]), 0)

        data = self.client.get(
            f"/api/reports/view/?report=noshow&from={tomorrow}").data
        kpis = {k["label"]: k["value"] for k in data["kpis"]}
        self.assertEqual(kpis["No-shows"], 0)

        # Malformed dates fall back to all-time instead of erroring.
        r = self.client.get("/api/reports/view/?report=discounts&from=garbage")
        self.assertEqual(r.status_code, 200)
        kpis = {k["label"]: k["value"] for k in r.data["kpis"]}
        self.assertEqual(kpis["Discounted bills"], 1)

        # Exports take the same window.
        r = self.client.get(
            f"/api/reports/export/?report=noshow&fmt=csv&from={tomorrow}")
        self.assertNotIn("Ghost Guest", r.content.decode())

    def test_every_report_exports_in_both_formats(self):
        """Every report key must export as csv AND xlsx without erroring —
        catches sheet-title characters openpyxl rejects ('/') and stale
        attribute references (Room.room_type_code)."""
        from apps.rooms.models import Room, RoomType
        rt = RoomType.objects.first() or RoomType.objects.create(
            code="STD", name="Standard", base_rate=Decimal("3000"))
        Room.objects.create(number="101", room_type=rt)
        reports = ["sales", "tax", "source", "occupancy", "accounting", "guests",
                   "arrivals", "night_audit", "noshow", "discounts", "aggregator",
                   "recipe_consumption", "sales_vs_consumption",
                   "purchase_vs_consumption", "food_cost", "item_profitability"]
        for report in reports:
            for fmt in ("csv", "xlsx"):
                r = self.client.get(f"/api/reports/export/?report={report}&fmt={fmt}")
                self.assertEqual(r.status_code, 200, f"{report} {fmt}")

    def test_operational_rbac_slices(self):
        hm = APIClient()
        hm.force_authenticate(User.objects.create_user(
            username="hmops", password="Tk9$mZ2pQw!7", role="Hotel Manager"))
        for report in ["arrivals", "night_audit", "noshow"]:
            self.assertEqual(hm.get(f"/api/reports/view/?report={report}").status_code, 200)
        self.assertEqual(hm.get("/api/reports/view/?report=discounts").status_code, 403)

        rm = APIClient()
        rm.force_authenticate(User.objects.create_user(
            username="rmops", password="Tk9$mZ2pQw!7", role="Restaurant Manager"))
        self.assertEqual(rm.get("/api/reports/view/?report=discounts").status_code, 200)
        for report in ["arrivals", "night_audit", "noshow"]:
            self.assertEqual(rm.get(f"/api/reports/view/?report={report}").status_code, 403)


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
