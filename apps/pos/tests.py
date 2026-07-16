from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer

from .models import (
    AddOn, AddOnGroup, Category, ChannelPrice, Coupon, MenuItem, Order, OrderLine, Variant,
)


class DiscountLoyaltyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = Category.objects.create(name="Main")
        self.item = MenuItem.objects.create(name="Biryani", category=self.cat,
                                            price=Decimal("400"), gst_rate=Decimal("5"))
        self.cashier = User.objects.create_user(
            username="c1", password="Tk9$mZ2pQw!7", role="F&B Cashier",
            discount_cap_type="percent", discount_cap_value=Decimal("10"))
        self.mgr = User.objects.create_user(
            username="m1", password="Tk9$mZ2pQw!7", role="General Manager", passcode="4321")

    def _order(self, qty=2, fired=True):
        o = Order.objects.create(mode=Order.DINEIN)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=qty,
                                 unit_price=Decimal("400"), kot_fired=fired)
        return o

    def test_discount_within_cap_ok(self):
        self.client.force_authenticate(self.cashier)
        o = self._order()
        r = self.client.post(reverse("order-apply-discount", args=[o.id]),
                             {"kind": "percent", "value": 10, "reason": "regular"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_discount_over_cap_blocked(self):
        self.client.force_authenticate(self.cashier)
        o = self._order()
        r = self.client.post(reverse("order-apply-discount", args=[o.id]),
                             {"kind": "percent", "value": 15, "reason": "too much"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.data.get("cap_exceeded"))

    def test_over_cap_allowed_with_manager_override(self):
        self.client.force_authenticate(self.cashier)
        o = self._order()
        r = self.client.post(reverse("order-apply-discount", args=[o.id]),
                             {"kind": "percent", "value": 15, "reason": "VIP",
                              "override": "4321"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_discount_recomputes_gst(self):
        o = self._order()  # subtotal 800
        o.discount_kind = Order.DISC_PERCENT
        o.discount_value = Decimal("10")
        o.save()
        t = o.totals()
        self.assertEqual(t["discount"], Decimal("80.00"))
        self.assertEqual(t["taxable"], Decimal("720.00"))
        self.assertEqual(t["tax"], Decimal("36.00"))  # 5% of 720
        self.assertEqual(t["total"], Decimal("756.00"))

    def test_coupon_min_bill_enforced(self):
        Coupon.objects.create(code="FLAT100", kind="fixed", value=Decimal("100"),
                              min_bill=Decimal("1000"))
        self.client.force_authenticate(self.mgr)
        o = self._order(qty=1)  # subtotal 400 < 1000
        r = self.client.post(reverse("order-apply-coupon", args=[o.id]),
                             {"code": "FLAT100"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_variant_and_addon_pricing(self):
        self.client.force_authenticate(self.mgr)
        full = Variant.objects.create(menu_item=self.item, name="Full", price=Decimal("500"))
        grp = AddOnGroup.objects.create(menu_item=self.item, name="Extras", min_select=0, max_select=2)
        egg = AddOn.objects.create(group=grp, name="Egg", price=Decimal("25"))
        o = Order.objects.create(mode=Order.DINEIN)
        r = self.client.post(reverse("order-add-item", args=[o.id]),
                             {"menu_item": self.item.id, "qty": 1,
                              "variant": full.id, "addons": [egg.id]}, format="json")
        self.assertEqual(r.status_code, 200)
        line = o.lines.first()
        self.assertEqual(line.unit_price, Decimal("525.00"))  # 500 variant + 25 add-on
        self.assertEqual(line.addons[0]["name"], "Egg")

    def test_channel_price_applies_per_mode(self):
        self.client.force_authenticate(self.mgr)
        ChannelPrice.objects.create(menu_item=self.item, channel="delivery", price=Decimal("450"))
        dinein = Order.objects.create(mode=Order.DINEIN)
        delivery = Order.objects.create(mode=Order.DELIVERY)
        self.client.post(reverse("order-add-item", args=[dinein.id]),
                         {"menu_item": self.item.id, "qty": 1}, format="json")
        self.client.post(reverse("order-add-item", args=[delivery.id]),
                         {"menu_item": self.item.id, "qty": 1}, format="json")
        self.assertEqual(dinein.lines.first().unit_price, Decimal("400.00"))   # base
        self.assertEqual(delivery.lines.first().unit_price, Decimal("450.00"))  # channel price

    def test_combo_deducts_each_component_recipe(self):
        from apps.inventory.models import Ingredient
        from apps.recipes.models import Recipe, RecipeLine
        from apps.recipes.services import deduct_for_newly_fired
        from .models import ComboComponent
        flour = Ingredient.objects.create(name="Flour", unit="kg", current_stock=Decimal("10"))
        comp = MenuItem.objects.create(name="Naan", category=self.cat, price=Decimal("40"))
        RecipeLine.objects.create(recipe=Recipe.objects.create(menu_item=comp),
                                  ingredient=flour, qty=Decimal("0.1"))
        combo = MenuItem.objects.create(name="Combo", category=self.cat, price=Decimal("200"))
        ComboComponent.objects.create(combo=combo, component=comp, qty=2)
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=combo, qty=1, unit_price=Decimal("200"))
        deduct_for_newly_fired(order, [line])
        flour.refresh_from_db()
        self.assertEqual(flour.current_stock, Decimal("9.800"))  # 10 - (0.1 * 2 naans)

    def test_happy_hour_price_applies_when_active(self):
        from datetime import time
        from .models import MenuSchedule
        MenuSchedule.objects.create(menu_item=self.item, name="HH",
                                    start_time=time(0, 0), end_time=time(23, 59),
                                    price=Decimal("250"))
        self.client.force_authenticate(self.mgr)
        o = Order.objects.create(mode=Order.DINEIN)
        self.client.post(reverse("order-add-item", args=[o.id]),
                         {"menu_item": self.item.id, "qty": 1}, format="json")
        self.assertEqual(o.lines.first().unit_price, Decimal("250.00"))

    def test_required_addon_group_enforced(self):
        self.client.force_authenticate(self.mgr)
        grp = AddOnGroup.objects.create(menu_item=self.item, name="Spice", min_select=1, max_select=1)
        AddOn.objects.create(group=grp, name="Mild", price=Decimal("0"))
        o = Order.objects.create(mode=Order.DINEIN)
        r = self.client.post(reverse("order-add-item", args=[o.id]),
                             {"menu_item": self.item.id, "qty": 1}, format="json")
        self.assertEqual(r.status_code, 400)  # required group not satisfied

    def test_move_merge_split(self):
        from .models import Table
        self.client.force_authenticate(self.mgr)
        t1 = Table.objects.create(name="A1", section="AC", seats=4)
        t2 = Table.objects.create(name="A2", section="AC", seats=2)
        o = self._order(qty=1)
        o.table = t1; o.save()
        OrderLine.objects.create(order=o, menu_item=self.item, qty=1, unit_price=Decimal("400"))
        # move
        r = self.client.post(reverse("order-move", args=[o.id]), {"table": t2.id}, format="json")
        self.assertEqual(r.status_code, 200)
        o.refresh_from_db(); self.assertEqual(o.table_id, t2.id)
        # split one of the two lines
        first_line = o.lines.first()
        r = self.client.post(reverse("order-split", args=[o.id]), {"lines": [first_line.id]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(o.lines.count(), 1)

    def test_void_requires_manager_override(self):
        self.client.force_authenticate(self.cashier)
        o = self._order()
        r = self.client.post(reverse("order-void", args=[o.id]), {"reason": "mistake"}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.client.post(reverse("order-void", args=[o.id]),
                             {"reason": "mistake", "override": "4321"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_aggregator_ingest_idempotent_and_prepaid(self):
        self.client.force_authenticate(self.mgr)
        body = {"platform": "swiggy", "external_id": "SWG-77", "prepaid": True,
                "customer": {"mobile": "9000011111", "name": "Online Guy"},
                "items": [{"menu_item": self.item.id, "qty": 2}]}
        r1 = self.client.post(reverse("order-aggregator"), body, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r1.data["source_platform"], "swiggy")
        self.assertEqual(r1.data["online_status"], "received")
        from apps.frontoffice.models import Settlement
        self.assertTrue(Settlement.objects.filter(reference="swiggy:SWG-77").exists())
        # Replay -> same order, no duplicate.
        r2 = self.client.post(reverse("order-aggregator"), body, format="json")
        self.assertEqual(r2.data["id"], r1.data["id"])
        self.assertEqual(Order.objects.filter(external_ref="SWG-77").count(), 1)

    def test_online_status_flow(self):
        """The counter accepts and dispatches; 'ready' is the kitchen's alone
        (KDS bump), and dispatch waits for it."""
        self.client.force_authenticate(self.mgr)
        o = Order.objects.create(mode=Order.DELIVERY, source_platform="zomato",
                                 external_ref="Z1", online_status="received")
        r = self.client.post(reverse("order-online-status", args=[o.id]),
                             {"status": "ready"}, format="json")
        self.assertEqual(r.status_code, 403)          # counter can't mark ready
        r = self.client.post(reverse("order-online-status", args=[o.id]),
                             {"status": "accepted"}, format="json")
        self.assertEqual(r.data["online_status"], "accepted")
        r = self.client.post(reverse("order-online-status", args=[o.id]),
                             {"status": "dispatched"}, format="json")
        self.assertEqual(r.status_code, 400)          # kitchen hasn't finished
        o.kitchen_status = "ready"                    # (KDS bump does this)
        o.save(update_fields=["kitchen_status"])
        r = self.client.post(reverse("order-online-status", args=[o.id]),
                             {"status": "dispatched"}, format="json")
        self.assertEqual(r.data["online_status"], "dispatched")

    def test_qr_order_public(self):
        from .models import Table
        from rest_framework.test import APIClient
        t = Table.objects.create(name="Q1", section="AC", qr_token="QRTEST")
        anon = APIClient()  # no auth — guest scanning the QR
        r = anon.post(reverse("qr-order"),
                      {"token": "QRTEST", "items": [{"menu_item": self.item.id, "qty": 1}]},
                      format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["table"], "Q1")
        t.refresh_from_db()
        self.assertEqual(t.status, Table.RUNNING)

    def test_offline_sync_is_idempotent(self):
        self.client.force_authenticate(self.mgr)
        batch = {"orders": [{
            "client_uuid": "abc-123", "mode": "dinein",
            "lines": [{"menu_item": self.item.id, "qty": 2, "unit_price": "400"}],
            "tender": "Cash", "settled": True,
        }]}
        r1 = self.client.post(reverse("order-sync"), batch, format="json")
        self.assertTrue(r1.data["results"][0]["created"])
        # Replay the same batch — must not duplicate.
        r2 = self.client.post(reverse("order-sync"), batch, format="json")
        self.assertFalse(r2.data["results"][0]["created"])
        self.assertEqual(r1.data["results"][0]["id"], r2.data["results"][0]["id"])
        self.assertEqual(Order.objects.filter(client_uuid="abc-123").count(), 1)

    def test_loyalty_accrues_on_settle(self):
        cust = Customer.objects.create(name="Reg", mobile="9000000000", loyalty_points=0)
        self.client.force_authenticate(self.mgr)
        o = self._order()  # 800 + 5% = 840 total -> 8 points
        o.customer = cust
        o.save()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        cust.refresh_from_db()
        self.assertEqual(cust.loyalty_points, 8)


class KotBillFlowTests(TestCase):
    """Guards on the KOT → final bill → settle lifecycle."""

    def setUp(self):
        from .models import Table
        self.client = APIClient()
        self.cat = Category.objects.create(name="Main")
        self.item = MenuItem.objects.create(name="Dosa", category=self.cat,
                                            price=Decimal("100"), gst_rate=Decimal("5"))
        self.mgr = User.objects.create_user(
            username="mgr", password="Tk9$mZ2pQw!7", role="General Manager", passcode="4321")
        self.client.force_authenticate(self.mgr)
        self.table = Table.objects.create(name="T1", section="AC", seats=4)

    def _order(self, fired=True, table=None):
        o = Order.objects.create(mode=Order.DINEIN, table=table or self.table,
                                 status=Order.KOT_FIRED if fired else Order.OPEN)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=2,
                                 unit_price=Decimal("100"), kot_fired=fired)
        return o

    def test_settle_rejects_unfired_lines(self):
        o = self._order(fired=False)
        r = self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("fire the KOT", r.data["detail"])

    def test_settle_rejects_empty_order(self):
        o = Order.objects.create(mode=Order.DINEIN, table=self.table)
        r = self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_double_settle_blocked(self):
        from apps.frontoffice.models import Settlement
        o = self._order()
        r1 = self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(Settlement.objects.count(), 1)  # no duplicate payment row

    def test_refund_settled_bill_with_override(self):
        from apps.frontoffice.models import Settlement
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        o.refresh_from_db()
        total = Settlement.objects.filter(amount__gt=0).first().amount
        # No reason → 400
        r = self.client.post(reverse("order-refund", args=[o.id]),
                             {"override": "4321"}, format="json")
        self.assertEqual(r.status_code, 400)
        # No manager override → 403
        r = self.client.post(reverse("order-refund", args=[o.id]),
                             {"reason": "wrong item"}, format="json")
        self.assertEqual(r.status_code, 403)
        # Full refund posts a reversing (negative) settlement
        r = self.client.post(reverse("order-refund", args=[o.id]),
                             {"reason": "wrong item", "override": "4321"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Settlement.objects.filter(amount__lt=0).count(), 1)
        # Day-end nets to zero for this order (settle + refund)
        net = sum(s.amount for s in Settlement.objects.all())
        self.assertEqual(net, 0)

    def test_refund_rejects_unsettled_order(self):
        o = self._order()
        r = self.client.post(reverse("order-refund", args=[o.id]),
                             {"reason": "x", "override": "4321"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_bill_requires_all_lines_fired(self):
        o = self._order(fired=False)
        r = self.client.post(reverse("order-bill", args=[o.id]), format="json")
        self.assertEqual(r.status_code, 400)

    def test_bill_locks_edits_until_reopened(self):
        o = self._order()
        self.client.post(reverse("order-bill", args=[o.id]), format="json")
        o.refresh_from_db(); self.table.refresh_from_db()
        self.assertEqual(o.status, Order.BILLED)
        self.assertEqual(self.table.status, "printed")
        # Adding an item to a printed bill is blocked…
        r = self.client.post(reverse("order-add-item", args=[o.id]),
                             {"menu_item": self.item.id, "qty": 1}, format="json")
        self.assertEqual(r.status_code, 409)
        # …until the bill is reopened.
        self.client.post(reverse("order-reopen", args=[o.id]), format="json")
        r = self.client.post(reverse("order-add-item", args=[o.id]),
                             {"menu_item": self.item.id, "qty": 1}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_reduce_fired_line_needs_manager_override(self):
        o = self._order()
        line = o.lines.first()
        r = self.client.post(reverse("order-set-qty", args=[o.id]),
                             {"line": line.id, "qty": 1}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.data.get("override_required"))
        r = self.client.post(reverse("order-set-qty", args=[o.id]),
                             {"line": line.id, "qty": 1, "override": "4321"}, format="json")
        self.assertEqual(r.status_code, 200)
        line.refresh_from_db(); self.assertEqual(line.qty, 1)

    def test_settle_keeps_table_running_for_split_bill(self):
        from .models import Table
        self.table.status = Table.RUNNING; self.table.save()
        o1 = self._order()
        self._order()  # second unpaid order on the same table (split bill)
        self.client.post(reverse("order-settle", args=[o1.id]), {"tender": "Cash"}, format="json")
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.RUNNING)  # still one unpaid bill

    def test_settle_frees_table_when_last_bill_paid(self):
        from .models import Table
        self.table.status = Table.RUNNING; self.table.save()
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, Table.FREE)

    def test_void_of_settled_order_blocked(self):
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        r = self.client.post(reverse("order-void", args=[o.id]),
                             {"override": "4321", "reason": "oops"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_fire_kot_blocked_after_settle(self):
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        OrderLine.objects.create(order=o, menu_item=self.item, qty=1, unit_price=Decimal("100"))
        r = self.client.post(reverse("order-fire-kot", args=[o.id]), format="json")
        self.assertEqual(r.status_code, 409)

    def test_ready_board_and_serve_loop(self):
        """Kitchen marks ready → captain's board lists it (mine=1) → Delivered."""
        from .models import Kot
        captain = User.objects.create_user(
            username="cap2", password="Tk9$mZ2pQw!7", role="Captain",
            first_name="Vijay", last_name="Menon")
        cap = APIClient(); cap.force_authenticate(captain)
        # Captain takes the order — their name is stamped on it.
        r = cap.post("/api/pos/orders/", {"mode": "dinein", "table": self.table.id}, format="json")
        oid = r.data["id"]
        self.assertEqual(r.data["captain"], "Vijay Menon")
        cap.post(reverse("order-add-item", args=[oid]), {"menu_item": self.item.id, "qty": 2}, format="json")
        cap.post(reverse("order-fire-kot", args=[oid]), format="json")
        kot = Kot.objects.get(order_id=oid)
        # Nothing on the board until the kitchen bumps it ready.
        self.assertEqual(cap.get("/api/pos/orders/ready/?mine=1").data, [])
        self.client.post(reverse("kds-bump", args=[kot.id]))  # kitchen: cooking → ready
        board = cap.get("/api/pos/orders/ready/?mine=1").data
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["table"], "T1")
        # Another captain's board stays empty.
        other = User.objects.create_user(username="cap3", password="Tk9$mZ2pQw!7", role="Captain")
        oc = APIClient(); oc.force_authenticate(other)
        self.assertEqual(oc.get("/api/pos/orders/ready/?mine=1").data, [])
        # Delivered → off the board, order marked served.
        r = cap.post("/api/pos/orders/serve/", {"kot": kot.id}, format="json")
        self.assertEqual(r.data["status"], "served")
        self.assertEqual(cap.get("/api/pos/orders/ready/?mine=1").data, [])
        Order.objects.get(pk=oid)  # sanity
        self.assertEqual(Order.objects.get(pk=oid).kitchen_status, "served")

    def test_captain_tender_mapping(self):
        """Captains settle UPI/Gateway tableside; cash only at the counter (BRD 5.10)."""
        captain = User.objects.create_user(
            username="cap1", password="Tk9$mZ2pQw!7", role="Captain")
        client = APIClient(); client.force_authenticate(captain)
        o = self._order()
        r = client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("cashier counter", r.data["detail"])
        r = client.post(reverse("order-settle", args=[o.id]), {"tender": "UPI"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_captain_blocked_from_till_and_recon(self):
        """Cash controls are counter-only — captains get 403 server-side."""
        captain = User.objects.create_user(
            username="capx", password="Tk9$mZ2pQw!7", role="Captain")
        cap = APIClient(); cap.force_authenticate(captain)
        self.assertEqual(cap.get(reverse("till-current")).status_code, 403)
        self.assertEqual(cap.post(reverse("till-open"), {"opening_float": "100"},
                                  format="json").status_code, 403)
        self.assertEqual(cap.get(reverse("recon-list")).status_code, 403)
        # Cashier still fine.
        self.assertEqual(self.client.get(reverse("till-current")).status_code, 200)

    def test_qr_order_returns_tracking_ref(self):
        from .models import Table
        t = Table.objects.create(name="Q9", section="AC", qr_token="QREF")
        anon = APIClient()
        r = anon.post(reverse("qr-order"),
                      {"token": "QREF", "items": [{"menu_item": self.item.id, "qty": 1}]},
                      format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["ref"])
        # The ref works on the public status page.
        s = anon.get(reverse("order-status-public") + f"?ref={r.data['ref']}")
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.data["kitchen_status"], "cooking")

    def test_kds_shows_only_fired_lines(self):
        o = Order.objects.create(mode=Order.DINEIN, table=self.table)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=2, unit_price=Decimal("100"))
        self.client.post(reverse("order-fire-kot", args=[o.id]), format="json")
        OrderLine.objects.create(order=o, menu_item=self.item, qty=5,
                                 unit_price=Decimal("100"))  # added later, not fired
        r = self.client.get(reverse("kds-list"))
        tickets = [t for t in r.data if t["type"] == "order"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(len(tickets[0]["items"]), 1)       # un-fired line hidden
        self.assertEqual(tickets[0]["items"][0]["qty"], 2)  # only the fired round

    def test_each_fire_is_a_separate_kot_round(self):
        """2 items fired, then 3 more fired → two tickets; serving round 1
        never hides or re-shows round 2 (FR-POS-004)."""
        o = Order.objects.create(mode=Order.DINEIN, table=self.table)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=2, unit_price=Decimal("100"))
        self.client.post(reverse("order-fire-kot", args=[o.id]), format="json")
        OrderLine.objects.create(order=o, menu_item=self.item, qty=3, unit_price=Decimal("100"))
        self.client.post(reverse("order-fire-kot", args=[o.id]), format="json")
        self.assertEqual(o.kots.count(), 2)
        k1, k2 = list(o.kots.all())
        self.assertTrue(k1.number.endswith("/1"))
        self.assertTrue(k2.number.endswith("/2"))
        self.assertEqual(k1.lines.get().qty, 2)  # each round carries only its items
        self.assertEqual(k2.lines.get().qty, 3)
        # Both rounds are separate tickets on the KDS.
        r = self.client.get(reverse("kds-list"))
        self.assertEqual(len([t for t in r.data if t["type"] == "order"]), 2)
        # Serve round 1 (cooking → ready → served): round 2 stays on the board.
        self.client.post(reverse("kds-bump", args=[k1.id]))
        self.client.post(reverse("kds-bump", args=[k1.id]))
        r = self.client.get(reverse("kds-list"))
        nums = [t["kot_no"] for t in r.data if t["type"] == "order"]
        self.assertEqual(nums, [k2.number])
        o.refresh_from_db()
        self.assertEqual(o.kitchen_status, "cooking")  # round 2 still cooking

    def test_till_session_lifecycle_and_variance(self):
        """Open float → cash out → cash settle → close: variance surfaces shortfalls."""
        from .models import TillSession
        r = self.client.post(reverse("till-open"), {"opening_float": "2000"}, format="json")
        self.assertEqual(r.status_code, 201)
        till = r.data["id"]
        # Second open blocked while one is running.
        self.assertEqual(self.client.post(reverse("till-open"), {}, format="json").status_code, 400)
        # Petty cash out 300.
        r = self.client.post(reverse("till-entry", args=[till]),
                             {"kind": "out", "amount": "300", "reason": "vegetables"}, format="json")
        self.assertEqual(r.status_code, 200)
        # A cash settlement of 210 lands in the drawer (2× Dosa + 5% GST).
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        # Close counting 1800 — expected 2000 − 300 + 210 = 1910 → variance −110.
        r = self.client.post(reverse("till-close", args=[till]),
                             {"counted_cash": "1800", "denominations": {"500": 3, "100": 3}},
                             format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["expected_cash"], "1910.00")
        self.assertEqual(r.data["variance"], "-110.00")
        self.assertEqual(TillSession.objects.get(pk=till).status, "closed")
        # Tender totals include the cash settlement.
        cash_row = next(t for t in r.data["tender_totals"] if t["tender"] == "Cash")
        self.assertEqual(cash_row["amount"], "210.00")

    def test_feedback_flow_public_submit_and_summary(self):
        """Bill creates a pending feedback token; guest submits; CRM summary rolls up."""
        from .models import Feedback
        o = self._order()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        fb = Feedback.objects.get(order=o)
        anon = APIClient()
        # Guest opens the link…
        r = anon.get(reverse("feedback-public") + f"?t={fb.token}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["submitted"])
        # …and submits a rating.
        r = anon.post(reverse("feedback-public"),
                      {"t": fb.token, "rating": 4, "nps": 9, "comment": "great dosa"}, format="json")
        self.assertEqual(r.status_code, 201)
        # Double submission blocked.
        r = anon.post(reverse("feedback-public"), {"t": fb.token, "rating": 5}, format="json")
        self.assertEqual(r.status_code, 400)
        # Bad token 404s.
        self.assertEqual(anon.get(reverse("feedback-public") + "?t=nope").status_code, 404)
        # CRM summary rolls up.
        r = self.client.get(reverse("feedback-list"))
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["avg_rating"], 4)
        self.assertEqual(r.data["nps"], 100)  # single promoter
        self.assertEqual(r.data["recent"][0]["comment"], "great dosa")

    def test_takeaway_token_and_public_status(self):
        """Takeaway orders get a daily pickup token; guest tracks via public status."""
        r = self.client.post("/api/pos/orders/", {"mode": "takeaway"}, format="json")
        oid, ref = r.data["id"], r.data["client_uuid"]
        self.assertTrue(ref)
        self.client.post(reverse("order-add-item", args=[oid]),
                         {"menu_item": self.item.id, "qty": 1}, format="json")
        self.client.post(reverse("order-fire-kot", args=[oid]), format="json")
        o = Order.objects.get(pk=oid)
        self.assertEqual(o.token_no, 1)  # first token of the day
        # Token board lists it as preparing.
        r = self.client.get(reverse("order-tokens"))
        self.assertEqual(r.data[0]["token_no"], 1)
        self.assertEqual(r.data[0]["kitchen_status"], "cooking")
        # Public status page by ref (no auth).
        anon = APIClient()
        r = anon.get(reverse("order-status-public") + f"?ref={ref}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["token_no"], 1)
        # Dine-in orders get no token.
        r2 = self.client.post("/api/pos/orders/", {"mode": "dinein", "table": self.table.id},
                              format="json")
        self.client.post(reverse("order-add-item", args=[r2.data["id"]]),
                         {"menu_item": self.item.id, "qty": 1}, format="json")
        self.client.post(reverse("order-fire-kot", args=[r2.data["id"]]), format="json")
        self.assertIsNone(Order.objects.get(pk=r2.data["id"]).token_no)

    def test_table_reservation_holds_and_seats(self):
        """A booking inside the 15-min window holds the table; seating releases it."""
        from datetime import timedelta
        from django.utils import timezone
        soon = (timezone.now() + timedelta(minutes=10)).isoformat()
        r = self.client.post(reverse("tablereservation-list"),
                             {"kind": "reservation", "table": self.table.id, "name": "Ramesh",
                              "party_size": 4, "reserved_for": soon},
                             format="json")
        self.assertEqual(r.status_code, 201)
        rid = r.data["id"]
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "reserved")
        # Seat the party → hold released, table back to free for the POS to open.
        r = self.client.post(reverse("tablereservation-seat", args=[rid]), format="json")
        self.assertEqual(r.data["status"], "seated")
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "free")

    def test_waitlist_seats_with_table_choice(self):
        r = self.client.post(reverse("tablereservation-list"),
                             {"kind": "waitlist", "name": "Walk-in", "party_size": 2},
                             format="json")
        rid = r.data["id"]
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "free")  # waitlist holds nothing
        # Seating without a table is rejected; with one, it works.
        self.assertEqual(self.client.post(reverse("tablereservation-seat", args=[rid]),
                                          format="json").status_code, 400)
        r = self.client.post(reverse("tablereservation-seat", args=[rid]),
                             {"table": self.table.id}, format="json")
        self.assertEqual(r.data["status"], "seated")
        self.assertEqual(r.data["table_name"], "T1")

    def test_reservation_cancel_releases_hold(self):
        from datetime import timedelta
        from django.utils import timezone
        soon = (timezone.now() + timedelta(minutes=5)).isoformat()
        r = self.client.post(reverse("tablereservation-list"),
                             {"kind": "reservation", "table": self.table.id, "name": "NoCome",
                              "party_size": 2, "reserved_for": soon},
                             format="json")
        self.client.post(reverse("tablereservation-cancel", args=[r.data["id"]]), format="json")
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "free")

    def test_reconciliation_report_and_payout_import(self):
        """POS aggregator settlements vs imported payouts → variance flagged."""
        from django.utils import timezone
        # A prepaid swiggy order books a "swiggy (prepaid)" settlement (210).
        self.client.post(reverse("order-aggregator"),
                         {"platform": "swiggy", "external_id": "SW-1", "prepaid": True,
                          "items": [{"menu_item": self.item.id, "qty": 2}]}, format="json")
        # Platform payout report says only 195 was paid out.
        r = self.client.post(reverse("recon-import-payouts"),
                             {"rows": [{"platform": "swiggy", "date": str(timezone.localdate()),
                                        "amount": "195", "reference": "PAYOUT-1"}]}, format="json")
        self.assertEqual(r.data["created"], 1)
        # Replay is idempotent.
        r = self.client.post(reverse("recon-import-payouts"),
                             {"rows": [{"platform": "swiggy", "date": str(timezone.localdate()),
                                        "amount": "195", "reference": "PAYOUT-1"}]}, format="json")
        self.assertEqual(r.data["created"], 0)
        rep = self.client.get(reverse("recon-list")).data
        agg = rep["rows"][0]["aggregators"][0]
        self.assertEqual(agg["platform"], "swiggy")
        self.assertEqual(agg["pos_amount"], "210.00")
        self.assertEqual(agg["payout_amount"], "195.00")
        self.assertEqual(agg["variance"], "-15.00")

    def test_kitchen_performance_report(self):
        from .models import Kot
        o = self._order()
        kot = Kot.objects.create(order=o, number="KOT-X/1")
        self.client.post(reverse("kds-bump", args=[kot.id]))  # cooking → ready (stamps ready_at)
        r = self.client.get(reverse("kds-performance"))
        self.assertEqual(r.data["tickets"], 1)
        self.assertGreaterEqual(r.data["avg_prep_minutes"], 0)
        self.assertEqual(len(r.data["by_hour"]), 1)

    def test_open_orders_filter_resumes_table(self):
        o = self._order()
        r = self.client.get(f"/api/pos/orders/?table={self.table.id}&open=1")
        self.assertEqual([x["id"] for x in r.data], [o.id])
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        r = self.client.get(f"/api/pos/orders/?table={self.table.id}&open=1")
        self.assertEqual(len(r.data), 0)
