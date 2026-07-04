"""Restaurant module end-to-end: stock IN (purchase/GRN) → recipes → orders OUT
(KOT → consumption) → billing/settlement → ledgers, alerts and controls.

Every scenario drives the real API the way the floor would use it.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.inventory.models import Ingredient, StockMovement
from apps.pos.models import Category, MenuItem, Order, Table
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

    def _inhouse_folio(self, room_no="101"):
        from datetime import date, timedelta

        from apps.frontoffice import services as fo
        from apps.reservations.models import Reservation
        from apps.rooms.models import Room, RoomType
        rt = RoomType.objects.create(code="STD", name="Std", base_rate=Decimal("4000"),
                                     gst_slab=Decimal("12"))
        room = Room.objects.create(number=room_no, room_type=rt, floor=1,
                                   status=Room.VACANT_CLEAN)
        resv = Reservation.objects.create(
            guest_name="In House Guest", room_type=rt, checkin_date=date.today(),
            checkout_date=date.today() + timedelta(days=1), nights=1, rate=Decimal("4000"))
        return fo.check_in(resv, room)

    def test_room_channel_replaces_table_post_to_room(self):
        """Loophole fix: a table bill can never land on a guest room — in-house
        orders go through the Room channel, folio chosen at order start."""
        folio = self._inhouse_folio()
        rice = self._material("Rice", stock="5")
        dish = self._dish("Curd Rice", "120", [{"ingredient": rice.id, "qty": "0.2"}])
        # A table (dine-in) order can't post to a room at all.
        t_order = self._order(dish, 1)
        r = self.client.post(f"/api/pos/orders/{t_order.id}/post_to_room/",
                             {"folio": folio.id}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Room channel", r.data["detail"])
        # A room order must name an open in-house folio up front…
        r = self.client.post("/api/pos/orders/", {"mode": "room"}, format="json")
        self.assertEqual(r.status_code, 400)
        # …picked from the POS's in-house list.
        r = self.client.get("/api/pos/orders/room_folios/")
        self.assertIn("101", [x["room"] for x in r.data])
        r = self.client.post("/api/pos/orders/", {"mode": "room", "folio": folio.id},
                             format="json")
        self.assertEqual(r.status_code, 201, r.data)
        oid = r.data["id"]
        self.client.post(f"/api/pos/orders/{oid}/add_item/",
                         {"menu_item": dish.id, "qty": 2}, format="json")
        self.client.post(f"/api/pos/orders/{oid}/fire_kot/", {}, format="json")
        order = Order.objects.get(pk=oid)
        self.assertEqual(order.captain, "Room 101")   # kitchen sees the destination
        self.assertIsNone(order.token_no)             # no pickup token for rooms
        # Posting needs no folio argument — it can only go to the preset one.
        r = self.client.post(f"/api/pos/orders/{oid}/post_to_room/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        folio.refresh_from_db()
        self.assertEqual(folio.lines.count(), 1)
        self.assertIn("Curd Rice", folio.lines.get().description)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.POSTED_TO_ROOM)

    def test_two_parties_share_a_table_and_bill_separately(self):
        """A1 seats two separate parties: each has its own order and bill; the
        table frees only when the last one closes."""
        rice = self._material("Rice", stock="10")
        dish = self._dish("Thali", "220", [{"ingredient": rice.id, "qty": "0.2"}], gst="0")
        table = Table.objects.create(name="A1", seats=4)

        def open_on_table(qty):
            r = self.client.post("/api/pos/orders/",
                                 {"mode": "dinein", "table": table.id}, format="json")
            oid = r.data["id"]
            self.client.post(f"/api/pos/orders/{oid}/add_item/",
                             {"menu_item": dish.id, "qty": qty}, format="json")
            self.client.post(f"/api/pos/orders/{oid}/fire_kot/", {}, format="json")
            return oid

        a = open_on_table(2)   # party 1
        b = open_on_table(1)   # party 2
        # Both live on the same table at once.
        r = self.client.get(f"/api/pos/orders/?table={table.id}&open=1")
        self.assertEqual(len(r.data), 2)
        # Party 1 pays and leaves — the table is still running for party 2.
        self.client.post(f"/api/pos/orders/{a}/settle/", {"tender": "Cash"}, format="json")
        table.refresh_from_db()
        self.assertEqual(table.status, Table.RUNNING)
        self.assertEqual(Order.objects.get(pk=a).totals()["total"], Decimal("440.00"))
        # Party 2 pays — now the table frees.
        self.client.post(f"/api/pos/orders/{b}/settle/", {"tender": "UPI"}, format="json")
        table.refresh_from_db()
        self.assertEqual(table.status, Table.FREE)
        self.assertEqual(Order.objects.get(pk=b).totals()["total"], Decimal("220.00"))
        # Kitchen-side: both parties drew stock correctly.
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.400"))   # 10 − 3×0.2

    def test_reserved_table_cannot_be_occupied(self):
        """A booking with a time holds the table: no POS order, no QR order,
        no overlapping second booking — until the party is seated."""
        from rest_framework.test import APIClient
        rice = self._material("Rice", stock="5")
        dish = self._dish("Pongal", "110", [{"ingredient": rice.id, "qty": "0.1"}])
        from datetime import timedelta

        from django.utils import timezone
        table = Table.objects.create(name="R1", seats=4, qr_token="QR-R1")
        now = timezone.now()
        # A booking for TOMORROW doesn't block walk-ins today…
        r = self.client.post("/api/pos/table-reservations/", {
            "name": "Tomorrow Guy", "kind": "reservation", "party_size": 2,
            "table": table.id, "reserved_for": (now + timedelta(days=1)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        table.refresh_from_db()
        self.assertEqual(table.status, Table.FREE)      # held only 15 min before
        self.client.post(f"/api/pos/table-reservations/{r.data['id']}/cancel/")
        # …but a booking 10 minutes out holds the table.
        r = self.client.post("/api/pos/table-reservations/", {
            "name": "Sharma", "kind": "reservation", "party_size": 4,
            "table": table.id, "reserved_for": (now + timedelta(minutes=10)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        res_id = r.data["id"]
        table.refresh_from_db()
        self.assertEqual(table.status, Table.RESERVED)
        # 1. A walk-in can't just open an order on it…
        r = self.client.post("/api/pos/orders/", {"mode": "dinein", "table": table.id},
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("reserved", r.data["detail"])
        # 2. …nor can a guest scanning the table QR.
        anon = APIClient()
        r = anon.post("/api/pos/qr-order/",
                      {"token": "QR-R1", "items": [{"menu_item": dish.id, "qty": 1}]},
                      format="json")
        self.assertEqual(r.status_code, 403)
        # 3. …nor can the desk double-book the same slot (±90 min window)…
        r = self.client.post("/api/pos/table-reservations/", {
            "name": "Verma", "kind": "reservation", "party_size": 2,
            "table": table.id, "reserved_for": (now + timedelta(minutes=70)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already booked", str(r.data))
        # …though a clearly later slot on the same table is fine (and doesn't hold yet).
        r = self.client.post("/api/pos/table-reservations/", {
            "name": "Verma", "kind": "reservation", "party_size": 2,
            "table": table.id, "reserved_for": (now + timedelta(hours=4)).isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        # A booking 40 minutes past its slot auto-no-shows and frees the table.
        ghost_table = Table.objects.create(name="R2", seats=2)
        gr = self.client.post("/api/pos/table-reservations/", {
            "name": "Ghost", "kind": "reservation", "party_size": 2,
            "table": ghost_table.id, "reserved_for": (now - timedelta(minutes=40)).isoformat(),
        }, format="json")
        self.client.get("/api/pos/tables/")             # floor read runs the sweep
        ghost_table.refresh_from_db()
        self.assertEqual(ghost_table.status, Table.FREE)
        gres = self.client.get(f"/api/pos/table-reservations/{gr.data['id']}/").data
        self.assertEqual(gres["status"], "no_show")
        # Seat the party → the hold releases and ordering works normally.
        self.client.post(f"/api/pos/table-reservations/{res_id}/seat/", {}, format="json")
        r = self.client.post("/api/pos/orders/", {"mode": "dinein", "table": table.id},
                             format="json")
        self.assertEqual(r.status_code, 201)

    def test_online_order_ready_belongs_to_the_kitchen(self):
        """Zomato flow: counter accepts + dispatches; ONLY the KDS bump marks
        ready, and dispatch is blocked until the kitchen has done so."""
        rice = self._material("Rice", stock="5")
        dish = self._dish("Veg Biryani", "220", [{"ingredient": rice.id, "qty": "0.2"}])
        r = self.client.post("/api/pos/orders/aggregator/", {
            "platform": "zomato", "external_id": "Z-9001", "prepaid": True,
            "items": [{"menu_item": dish.id, "qty": 1}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        oid = r.data["id"]
        # Counter can't mark ready…
        r = self.client.post(f"/api/pos/orders/{oid}/online_status/", {"status": "ready"},
                             format="json")
        self.assertEqual(r.status_code, 403)
        # …and can't dispatch food the kitchen hasn't finished.
        self.client.post(f"/api/pos/orders/{oid}/online_status/", {"status": "accepted"},
                         format="json")
        r = self.client.post(f"/api/pos/orders/{oid}/online_status/", {"status": "dispatched"},
                             format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("kitchen", r.data["detail"])
        # The chef bumps the ticket on the KDS → the board turns ready by itself.
        kds = self.client.get("/api/kds/").data
        ticket = next(t for t in kds if t["kot_no"].startswith("AGG-Z-9001"[:10]))
        self.client.post(f"/api/kds/{ticket['id']}/bump/")
        order = Order.objects.get(pk=oid)
        self.assertEqual(order.online_status, "ready")
        # The ready strip in the POS offers the dispatch…
        strip = self.client.get("/api/pos/orders/ready/").data
        entry = next(x for x in strip if x["order"] == oid)
        self.assertTrue(entry["online"])
        self.assertEqual(entry["platform"], "zomato")
        # …and dispatching closes the order AND its kitchen ticket.
        r = self.client.post(f"/api/pos/orders/{oid}/online_status/", {"status": "dispatched"},
                             format="json")
        self.assertEqual(r.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SETTLED)   # prepaid closes on dispatch
        self.assertEqual(order.kitchen_status, "served")
        self.assertNotIn(oid, [x["order"] for x in self.client.get("/api/pos/orders/ready/").data])

    def test_only_kitchen_roles_bump_ready(self):
        """The cashier can WATCH the KDS but never mark food ready; the chef can."""
        rice = self._material("Rice", stock="5")
        dish = self._dish("Upma", "90", [{"ingredient": rice.id, "qty": "0.1"}])
        order = self._order(dish, 1, mode="takeaway")
        kds = self.client.get("/api/kds/").data
        ticket = next(t for t in kds if t["kot_no"] == order.kot_no)
        cashier = APIClient()
        cashier.force_authenticate(User.objects.create_user(
            username="cashk", password="Tk9$mZ2pQw!7", role="F&B Cashier"))
        self.assertEqual(cashier.get("/api/kds/").status_code, 200)      # can watch
        r = cashier.post(f"/api/kds/{ticket['id']}/bump/")
        self.assertEqual(r.status_code, 403)                             # can't mark ready
        chef = APIClient()
        chef.force_authenticate(User.objects.create_user(
            username="chefk", password="Tk9$mZ2pQw!7", role="Chef / Kitchen"))
        self.assertEqual(chef.post(f"/api/kds/{ticket['id']}/bump/").status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.kitchen_status, "ready")

    def test_captain_takes_table_orders_only(self):
        captain = APIClient()
        captain.force_authenticate(User.objects.create_user(
            username="capm", password="Tk9$mZ2pQw!7", role="Captain"))
        table = Table.objects.create(name="B1", seats=2)
        for mode in ("takeaway", "delivery"):
            r = captain.post("/api/pos/orders/", {"mode": mode}, format="json")
            self.assertEqual(r.status_code, 400, mode)
        r = captain.post("/api/pos/orders/", {"mode": "dinein", "table": table.id},
                         format="json")
        self.assertEqual(r.status_code, 201)

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
