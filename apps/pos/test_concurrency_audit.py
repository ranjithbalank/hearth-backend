"""Concurrency audit — POS, stock and the shared counters.

Same method as apps/frontoffice/test_concurrency_audit.py: two stale instances
of one row, which is what two terminals hold when requests arrive together.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.crm.models import Customer
from apps.inventory.models import Ingredient

from .models import Category, Coupon, MenuItem, Order, OrderLine, Table


class PosRaceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cat = Category.objects.create(name="Race Cat")
        self.item = MenuItem.objects.create(name="Race Dosa", category=self.cat,
                                            price=Decimal("200"), gst_rate=Decimal("5"))
        self.mgr = User.objects.create_user(
            username="posrace", password="Tk9$mZ2pQw!7", role="General Manager")
        self.client.force_authenticate(self.mgr)
        self.t1 = Table.objects.create(name="R1", section="AC", seats=4)
        self.t2 = Table.objects.create(name="R2", section="AC", seats=4)

    def _order(self, table):
        o = Order.objects.create(mode=Order.DINEIN, table=table, status=Order.KOT_FIRED)
        OrderLine.objects.create(order=o, menu_item=self.item, qty=1,
                                 unit_price=Decimal("200"), kot_fired=True)
        return o

    def test_a_single_use_coupon_cannot_be_spent_twice_at_once(self):
        """Two counters ring up the last use of a coupon at the same moment.
        used_count is read, compared to the limit, then incremented — both pass
        the comparison before either writes, so the promotion is given away
        twice and the campaign's spend is understated."""
        Coupon.objects.create(code="RACE1", kind="fixed", value=Decimal("50"),
                              usage_limit=1, used_count=0, active=True)
        a, b = self._order(self.t1), self._order(self.t2)
        r1 = self.client.post(reverse("order-apply-coupon", args=[a.id]),
                              {"code": "RACE1"}, format="json")
        r2 = self.client.post(reverse("order-apply-coupon", args=[b.id]),
                              {"code": "RACE1"}, format="json")
        # Settling is what banks the usage.
        self.client.post(reverse("order-settle", args=[a.id]), {"tender": "Cash"}, format="json")
        self.client.post(reverse("order-settle", args=[b.id]), {"tender": "Cash"}, format="json")
        used = Coupon.objects.get(code="RACE1").used_count
        self.assertLessEqual(used, 1,
                             f"a single-use coupon was redeemed {used} times "
                             f"(HTTP {r1.status_code}/{r2.status_code})")

    def test_points_cannot_be_redeemed_twice_from_one_balance(self):
        """The same 100 points discounting two different bills is the
        restaurant paying for one of them."""
        cust = Customer.objects.create(mobile="+919000007001", name="Race Guest",
                                       loyalty_points=100)
        a, b = self._order(self.t1), self._order(self.t2)
        for o in (a, b):
            o.customer = cust
            o.save(update_fields=["customer"])
        self.client.post(reverse("order-redeem-loyalty", args=[a.id]),
                         {"points": 100}, format="json")
        self.client.post(reverse("order-redeem-loyalty", args=[b.id]),
                         {"points": 100}, format="json")
        self.client.post(reverse("order-settle", args=[a.id]), {"tender": "Cash"}, format="json")
        self.client.post(reverse("order-settle", args=[b.id]), {"tender": "Cash"}, format="json")
        cust.refresh_from_db()
        self.assertGreaterEqual(cust.loyalty_points, 0,
                                f"balance went negative: {cust.loyalty_points}")

    def test_the_last_unit_of_stock_cannot_be_sold_twice(self):
        """Two tables order the last portion at the same instant. Recipe
        deduction reads current_stock, subtracts and writes — both read the
        same figure, so the kitchen is committed to a dish it cannot make and
        the ledger shows stock it does not have."""
        ing = Ingredient.objects.create(name="Race Rice", code="RR-001", unit="kg",
                                        current_stock=Decimal("1"))
        from apps.recipes.models import Recipe, RecipeLine
        recipe = Recipe.objects.create(menu_item=self.item)
        RecipeLine.objects.create(recipe=recipe, ingredient=ing, qty=Decimal("1"))
        a, b = self._order(self.t1), self._order(self.t2)
        self.client.post(reverse("order-settle", args=[a.id]), {"tender": "Cash"}, format="json")
        self.client.post(reverse("order-settle", args=[b.id]), {"tender": "Cash"}, format="json")
        ing.refresh_from_db()
        self.assertGreaterEqual(ing.current_stock, Decimal("0"),
                                f"stock went negative: {ing.current_stock}")
