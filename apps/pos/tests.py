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

    def _order(self, qty=2):
        o = Order.objects.create(mode=Order.DINEIN)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=qty, unit_price=Decimal("400"))
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
