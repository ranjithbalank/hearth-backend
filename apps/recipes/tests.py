from decimal import Decimal

from django.test import TestCase

from apps.inventory.models import Ingredient, apply_movement
from apps.pos.models import Category, MenuItem, Order, OrderLine
from apps.procurement.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from apps.rooms.models import RoomType  # noqa: F401 (app load)

from .models import Recipe, RecipeLine
from .services import deduct_for_newly_fired


class RecipeDeductionTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Main")
        self.paneer = Ingredient.objects.create(name="Paneer", unit="kg", current_stock=Decimal("10"), unit_cost=Decimal("320"))
        self.item = MenuItem.objects.create(name="Paneer Tikka", category=self.cat, price=Decimal("320"))
        recipe = Recipe.objects.create(menu_item=self.item)
        RecipeLine.objects.create(recipe=recipe, ingredient=self.paneer, qty=Decimal("0.2"))

    def test_kot_deducts_recipe_ingredients(self):
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=self.item, qty=3, unit_price=Decimal("320"))
        deduct_for_newly_fired(order, [line])
        self.paneer.refresh_from_db()
        # 10 - (0.2 * 3) = 9.4
        self.assertEqual(self.paneer.current_stock, Decimal("9.400"))

    def test_plate_cost_rollup(self):
        self.assertEqual(self.item.recipe.plate_cost, Decimal("64.000"))  # 0.2 * 320


class GoodsReceiptTests(TestCase):
    def test_grn_posts_stock(self):
        ing = Ingredient.objects.create(name="Butter", unit="kg", current_stock=Decimal("3"), unit_cost=Decimal("480"))
        sup = Supplier.objects.create(name="Fresh Farms")
        po = PurchaseOrder.objects.create(supplier=sup, status=PurchaseOrder.APPROVED)
        PurchaseOrderLine.objects.create(purchase_order=po, ingredient=ing, qty=Decimal("10"), rate=Decimal("480"))
        # Simulate the receive action's effect
        apply_movement(ing, "receipt", Decimal("10"), source=f"PO:{po.id}")
        GoodsReceipt.objects.create(purchase_order=po)
        ing.refresh_from_db()
        self.assertEqual(ing.current_stock, Decimal("13.000"))
