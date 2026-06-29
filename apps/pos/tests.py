from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer

from .models import Category, Coupon, MenuItem, Order, OrderLine


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

    def test_loyalty_accrues_on_settle(self):
        cust = Customer.objects.create(name="Reg", mobile="9000000000", loyalty_points=0)
        self.client.force_authenticate(self.mgr)
        o = self._order()  # 800 + 5% = 840 total -> 8 points
        o.customer = cust
        o.save()
        self.client.post(reverse("order-settle", args=[o.id]), {"tender": "Cash"}, format="json")
        cust.refresh_from_db()
        self.assertEqual(cust.loyalty_points, 8)
