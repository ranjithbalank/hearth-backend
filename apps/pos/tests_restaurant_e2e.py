"""Restaurant module end-to-end: stock IN (purchase/GRN) → recipes → orders OUT
(KOT → consumption) → billing/settlement → ledgers, alerts and controls.

Every scenario drives the real API the way the floor would use it.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient, StockMovement
from apps.pos.models import Category, MenuItem, Order
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier


class RestaurantE2ETests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.gm = User.objects.create_user(username="gme2e", password="Tk9$mZ2pQw!7",
                                           role="General Manager", passcode="4321")
        self.client.force_authenticate(self.gm)
        self.cat = Category.objects.create(name="Mains")

    # ---- helpers -----------------------------------------------------------

    def _material(self, name, unit="kg", stock="0", cost="60", reorder="0"):
        r = self.client.post("/api/inventory/", {
            "name": name, "unit": unit, "unit_cost": cost, "reorder_level": reorder,
        }, format="json")
        assert r.status_code == 201, r.data
        ing = Ingredient.objects.get(pk=r.data["id"])
        if Decimal(stock) > 0:
            self.client.post(f"/api/inventory/{ing.id}/adjust/",
                             {"qty": stock, "reason": "opening stock"}, format="json")
            ing.refresh_from_db()
        return ing

    def _dish(self, name, price, lines, gst="5"):
        r = self.client.post("/api/recipes/", {
            "new_item": {"name": name, "price": price, "category": "Mains", "gst_rate": gst},
            "lines": lines,
        }, format="json")
        assert r.status_code == 201, r.data
        return MenuItem.objects.get(pk=r.data["menu_item"])

    def _order(self, dish, qty, mode="dinein", fire=True):
        r = self.client.post("/api/pos/orders/", {"mode": mode}, format="json")
        order_id = r.data["id"]
        self.client.post(f"/api/pos/orders/{order_id}/add_item/",
                         {"menu_item": dish.id, "qty": qty}, format="json")
        if fire:
            r = self.client.post(f"/api/pos/orders/{order_id}/fire_kot/", {}, format="json")
            assert r.status_code == 200, r.data
        return Order.objects.get(pk=order_id)

    # ---- scenarios ---------------------------------------------------------

    def test_full_cycle_purchase_to_settlement(self):
        """IN via GRN, OUT via KOT, money via settle — the whole §5 loop."""
        rice = self._material("Rice", cost="60", reorder="2")
        # Stock IN: PO approved → goods received.
        po = PurchaseOrder.objects.create(supplier=Supplier.objects.create(name="Farm"))
        PurchaseOrderLine.objects.create(purchase_order=po, ingredient=rice,
                                         qty=Decimal("10"), rate=Decimal("60"))
        self.assertEqual(self.client.post(f"/api/purchase-orders/{po.id}/approve/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/purchase-orders/{po.id}/receive/").status_code, 200)
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("10.000"))
        # Recipe → order → stock OUT.
        dish = self._dish("Fried Rice", "200", [{"ingredient": rice.id, "qty": "250", "unit": "g"}])
        order = self._order(dish, 2)
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.500"))  # 10 − 2×0.25
        # Bill & settle: 2×200 = 400 + 5% GST = 420.
        self.client.post(f"/api/pos/orders/{order.id}/bill/", {}, format="json")
        r = self.client.post(f"/api/pos/orders/{order.id}/settle/", {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SETTLED)
        self.assertEqual(order.totals()["total"], Decimal("420.00"))
        # Ledger shows the IN and the OUT.
        kinds = set(rice.movements.values_list("kind", flat=True))
        self.assertEqual(kinds, {"receipt", "consumption"})

    def test_takeaway_token_and_kds_bump_flow(self):
        rice = self._material("Rice", stock="5")
        dish = self._dish("Lemon Rice", "150", [{"ingredient": rice.id, "qty": "0.2"}])
        order = self._order(dish, 1, mode="takeaway")
        self.assertIsNotNone(order.token_no)          # pickup token for the board
        # Kitchen sees the ticket and bumps it through its life.
        kds = self.client.get("/api/kds/").data
        ticket = next(t for t in kds if t["kot_no"] == order.kot_no)
        self.assertEqual(ticket["kitchen_status"], "cooking")
        self.client.post(f"/api/kds/{ticket['id']}/bump/")
        self.assertEqual(
            next(t for t in self.client.get("/api/kds/").data
                 if t["kot_no"] == order.kot_no)["kitchen_status"], "ready")
        self.client.post(f"/api/kds/{ticket['id']}/bump/")   # ready → served
        self.assertNotIn(order.kot_no,
                         [t["kot_no"] for t in self.client.get("/api/kds/").data])

    def test_multi_ingredient_units_and_wastage_math(self):
        rice = self._material("Rice", stock="10")
        oil = self._material("Oil", unit="l", stock="2", cost="150")
        dish = self._dish("Veg Rice", "180", [
            {"ingredient": rice.id, "qty": "200", "unit": "g"},
            {"ingredient": oil.id, "qty": "30", "unit": "ml", "wastage_pct": "10"},
        ])
        self._order(dish, 5)
        rice.refresh_from_db(); oil.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.000"))   # 5 × 0.2 kg
        self.assertEqual(oil.current_stock, Decimal("1.835"))    # 2 − 5 × 0.03 × 1.1

    def test_unmapped_item_sells_without_deduction(self):
        soda = MenuItem.objects.create(name="Soda", category=self.cat, price=Decimal("60"))
        order = self._order(soda, 3)
        self.assertEqual(StockMovement.objects.count(), 0)       # nothing to draw
        self.assertEqual(order.status, Order.KOT_FIRED)          # sale unaffected

    def test_recipe_replace_changes_future_draw_only(self):
        rice = self._material("Rice", stock="10")
        dish = self._dish("Bowl", "120", [{"ingredient": rice.id, "qty": "0.1"}])
        self._order(dish, 1)
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.900"))
        # Chef revises the BOM: future orders draw the new quantity.
        r = self.client.post("/api/recipes/", {
            "menu_item": dish.id,
            "lines": [{"ingredient": rice.id, "qty": "0.2"}],
        }, format="json")
        self.assertEqual(r.status_code, 200)
        self._order(dish, 1)
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.700"))   # −0.2 this time

    def test_stock_control_ledger_actions(self):
        rice = self._material("Rice", stock="20")
        self.client.post(f"/api/inventory/{rice.id}/waste/",
                         {"qty": "1", "reason": "burnt batch"}, format="json")
        self.client.post(f"/api/inventory/{rice.id}/waste/",
                         {"qty": "0.5", "reason": "fungus", "expired": True}, format="json")
        self.client.post(f"/api/inventory/{rice.id}/transfer/",
                         {"qty": "3", "direction": "out", "location": "Banquet kitchen"}, format="json")
        self.client.post(f"/api/inventory/{rice.id}/count/", {"counted": "15"}, format="json")
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("15.000"))  # count books the truth
        by_kind = {k: 0 for k in ["wastage", "expiry", "transfer", "count", "adjustment"]}
        for m in rice.movements.all():
            by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
        self.assertEqual(by_kind["wastage"], 1)
        self.assertEqual(by_kind["expiry"], 1)
        self.assertEqual(by_kind["transfer"], 1)
        self.assertEqual(by_kind["count"], 1)
        # The register filters by kind over the API too.
        r = self.client.get("/api/inventory/movements/?kind=transfer")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(r.data[0]["reason"], "to Banquet kitchen")

    def test_low_stock_alert_after_consumption(self):
        rice = self._material("Rice", stock="10", reorder="9")
        dish = self._dish("Pulao", "160", [{"ingredient": rice.id, "qty": "0.5"}])
        self._order(dish, 4)                                     # draws 2 kg → 8 < 9
        low = self.client.get("/api/inventory/low_stock/").data
        self.assertIn("Rice", [i["name"] for i in low["items"]])
        alerts = self.client.get("/api/notifications/").data["alerts"]
        self.assertTrue(any("Low stock: Rice" in a["title"] for a in alerts), alerts)

    def test_settle_blocks_unfired_lines(self):
        rice = self._material("Rice", stock="5")
        dish = self._dish("Khichdi", "140", [{"ingredient": rice.id, "qty": "0.2"}])
        order = self._order(dish, 1, fire=False)
        r = self.client.post(f"/api/pos/orders/{order.id}/settle/", {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 400)                     # nothing reaches the bill un-fired
        self.client.post(f"/api/pos/orders/{order.id}/fire_kot/", {}, format="json")
        r = self.client.post(f"/api/pos/orders/{order.id}/settle/", {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_void_needs_manager_override_and_consumed_stock_stays_gone(self):
        User.objects.create_user(username="mde2e", password="Tk9$mZ2pQw!7",
                                 role="Managing Director", passcode="1234")
        rice = self._material("Rice", stock="5")
        dish = self._dish("Thali", "250", [{"ingredient": rice.id, "qty": "0.3"}])
        order = self._order(dish, 1)                             # cooked → 0.3 kg gone
        r = self.client.post(f"/api/pos/orders/{order.id}/void/", {"reason": "guest left"}, format="json")
        self.assertEqual(r.status_code, 403)                     # override required
        r = self.client.post(f"/api/pos/orders/{order.id}/void/",
                             {"reason": "guest left", "override": "1234"}, format="json")
        self.assertEqual(r.status_code, 200)
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("4.700"))   # food was cooked; no restock

    def test_discount_with_reason_flows_into_totals(self):
        rice = self._material("Rice", stock="5")
        dish = self._dish("Meals", "200", [{"ingredient": rice.id, "qty": "0.2"}], gst="0")
        order = self._order(dish, 2)                             # subtotal 400
        r = self.client.post(f"/api/pos/orders/{order.id}/apply_discount/",
                             {"kind": "percent", "value": "10", "reason": "regular guest"},
                             format="json")
        self.assertEqual(r.status_code, 200, r.data)
        order.refresh_from_db()
        self.assertEqual(order.totals()["total"], Decimal("360.00"))  # 400 − 10%
        r = self.client.post(f"/api/pos/orders/{order.id}/settle/", {"tender": "UPI"}, format="json")
        self.assertEqual(r.status_code, 200)
