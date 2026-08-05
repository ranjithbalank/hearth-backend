"""Logic-layer audit — POS money, discounts, loyalty and stock.

Companion to apps/frontoffice/test_logic_audit.py. Same standard: these assert
what should be true of the money, so a failure is a real defect.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer

from .models import Category, Coupon, MenuItem, Order, OrderLine, Table


class PosMoneyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = Category.objects.create(name="Audit Main")
        self.item = MenuItem.objects.create(name="Audit Dosa", category=self.cat,
                                            price=Decimal("100"), gst_rate=Decimal("5"))
        self.mgr = User.objects.create_user(
            username="posaudit", password="Tk9$mZ2pQw!7", role="General Manager",
            passcode="4321")
        self.client.force_authenticate(self.mgr)
        self.table = Table.objects.create(name="AUD1", section="AC", seats=4)

    def _order(self, qty=2):
        o = Order.objects.create(mode=Order.DINEIN, table=self.table,
                                 status=Order.KOT_FIRED)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=qty,
                                 unit_price=Decimal("100"), kot_fired=True)
        return o

    # -- settle --------------------------------------------------------
    def test_an_order_cannot_be_settled_twice(self):
        """The bill is already paid; a second settle takes the guest's money
        again and books a second sale that never happened."""
        o = self._order()
        first = self.client.post(reverse("order-settle", args=[o.id]),
                                 {"tender": "Cash"}, format="json")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(reverse("order-settle", args=[o.id]),
                                  {"tender": "Cash"}, format="json")
        self.assertIn(second.status_code, (400, 409),
                      "a settled order accepted a second payment")

    # -- discounts -----------------------------------------------------
    def test_a_discount_cannot_exceed_the_bill(self):
        """A discount larger than the subtotal must not produce a negative
        bill — the guest would be owed money by the restaurant."""
        o = self._order()
        o.discount_kind = Order.DISC_PERCENT
        o.discount_value = Decimal("500")
        o.save(update_fields=["discount_kind", "discount_value"])
        totals = o.totals()
        self.assertGreaterEqual(totals["total"], Decimal("0"),
                                f"bill went negative: {totals}")

    def test_a_fixed_discount_cannot_exceed_the_bill(self):
        o = self._order()
        o.discount_kind = Order.DISC_FIXED
        o.discount_value = Decimal("99999")
        o.save(update_fields=["discount_kind", "discount_value"])
        self.assertGreaterEqual(o.totals()["total"], Decimal("0"))

    def test_a_percent_discount_over_100_is_refused_at_the_api(self):
        """min(disc, subtotal) stops the bill going negative, but a 500% line
        should never have been accepted in the first place — it hides a typo
        that reads as a full comp."""
        o = self._order()
        r = self.client.post(reverse("order-apply-discount", args=[o.id]),
                             {"kind": "percent", "value": "500"}, format="json")
        self.assertEqual(r.status_code, 400,
                         "a percentage discount above 100 was accepted")

    def test_a_negative_discount_is_refused(self):
        """A negative discount is a surcharge the guest never agreed to."""
        o = self._order()
        r = self.client.post(reverse("order-apply-discount", args=[o.id]),
                             {"kind": "fixed", "value": "-500"}, format="json")
        self.assertEqual(r.status_code, 400)

    # -- loyalty -------------------------------------------------------
    def test_a_guest_cannot_redeem_more_points_than_they_hold(self):
        """Points are money — one point discounts one rupee. Redeeming a
        balance the guest doesn't have is the restaurant paying for it."""
        cust = Customer.objects.create(mobile="+919000009001", name="Points Guest",
                                       loyalty_points=50)
        o = self._order()
        o.customer = cust
        o.save(update_fields=["customer"])
        r = self.client.post(reverse("order-redeem-loyalty", args=[o.id]),
                             {"points": 5000}, format="json")
        o.refresh_from_db()
        self.assertLessEqual(o.loyalty_redeemed, 50,
                             f"redeemed {o.loyalty_redeemed} against a 50-point balance "
                             f"(HTTP {r.status_code})")

    # -- coupons -------------------------------------------------------
    def test_a_coupon_past_its_usage_limit_is_refused(self):
        coupon = Coupon.objects.create(code="AUDIT10", kind="fixed", value=Decimal("50"),
                                       usage_limit=1, used_count=1, active=True)
        o = self._order()
        r = self.client.post(reverse("order-apply-coupon", args=[o.id]),
                             {"code": coupon.code}, format="json")
        self.assertEqual(r.status_code, 400,
                         "an exhausted coupon was applied again")

    # -- arithmetic ----------------------------------------------------
    def test_totals_reconcile_to_taxable_plus_tax(self):
        o = self._order(qty=3)
        t = o.totals()
        self.assertEqual(t["total"], t["taxable"] + t["cgst"] + t["sgst"],
                         f"total does not equal taxable + tax: {t}")

    def test_a_discounted_bill_still_reconciles(self):
        o = self._order(qty=3)
        o.discount_kind = Order.DISC_PERCENT
        o.discount_value = Decimal("10")
        o.save(update_fields=["discount_kind", "discount_value"])
        t = o.totals()
        self.assertEqual(t["total"], t["taxable"] + t["cgst"] + t["sgst"])
        # 10% off 300 = 270 net of tax
        self.assertEqual(t["taxable"], Decimal("270.00"))
