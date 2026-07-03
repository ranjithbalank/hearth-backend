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

    def test_unit_conversion_g_to_kg_on_deduction(self):
        """Spec §2: recipe says 250 g, ingredient stocked in kg → deduct 0.25 kg."""
        rice = Ingredient.objects.create(name="Rice", unit="kg",
                                         current_stock=Decimal("10"), unit_cost=Decimal("60"))
        dish = MenuItem.objects.create(name="Fried Rice", category=self.cat, price=Decimal("200"))
        rec = Recipe.objects.create(menu_item=dish)
        RecipeLine.objects.create(recipe=rec, ingredient=rice, qty=Decimal("250"), unit="g")
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=dish, qty=2, unit_price=Decimal("200"))
        deduct_for_newly_fired(order, [line])
        rice.refresh_from_db()
        self.assertEqual(rice.current_stock, Decimal("9.500"))  # 10 - 2×0.25 kg
        # Plate cost converts too: 0.25 kg × ₹60 = ₹15
        self.assertEqual(rec.plate_cost, Decimal("15.00000"))

    def test_wastage_pct_inflates_consumption(self):
        """Spec §2: 10% wastage on 0.2 kg → 0.22 kg drawn per plate."""
        oil = Ingredient.objects.create(name="Oil", unit="l", current_stock=Decimal("5"))
        dish = MenuItem.objects.create(name="Poori", category=self.cat, price=Decimal("80"))
        rec = Recipe.objects.create(menu_item=dish)
        RecipeLine.objects.create(recipe=rec, ingredient=oil, qty=Decimal("0.2"),
                                  wastage_pct=Decimal("10"))
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=dish, qty=1, unit_price=Decimal("80"))
        deduct_for_newly_fired(order, [line])
        oil.refresh_from_db()
        self.assertEqual(oil.current_stock, Decimal("4.780"))  # 5 - 0.2×1.1


class SubRecipeTests(TestCase):
    def test_sub_recipe_expands_on_deduction(self):
        from apps.pos.models import Category, MenuItem, Order, OrderLine
        cat = Category.objects.create(name="Main")
        tomato = Ingredient.objects.create(name="Tomato", unit="kg", current_stock=Decimal("10"))
        # Sub-recipe: "Gravy" prep made from tomato.
        gravy = MenuItem.objects.create(name="Gravy", category=cat, price=Decimal("0"))
        RecipeLine.objects.create(recipe=Recipe.objects.create(menu_item=gravy),
                                  ingredient=tomato, qty=Decimal("0.3"))
        # Dish consumes 2 units of the gravy sub-recipe.
        dish = MenuItem.objects.create(name="Curry", category=cat, price=Decimal("300"))
        RecipeLine.objects.create(recipe=Recipe.objects.create(menu_item=dish),
                                  sub_recipe=gravy, qty=Decimal("2"))
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=dish, qty=1, unit_price=Decimal("300"))
        deduct_for_newly_fired(order, [line])
        tomato.refresh_from_db()
        # 10 - (0.3 tomato * 2 gravy * 1 dish) = 9.4
        self.assertEqual(tomato.current_stock, Decimal("9.400"))


class ProductionBatchTests(TestCase):
    def test_produce_batch_consumes_bom_and_credits_prep_stock(self):
        from django.urls import reverse

        from apps.accounts.models import User
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(User.objects.create_user(
            username="gmp", password="Tk9$mZ2pQw!7", role="General Manager"))
        cat = Category.objects.create(name="Preps")
        tomato = Ingredient.objects.create(name="Tomato", unit="kg", current_stock=Decimal("10"))
        gravy = MenuItem.objects.create(name="Gravy", category=cat, price=Decimal("0"))
        rec = Recipe.objects.create(menu_item=gravy)
        RecipeLine.objects.create(recipe=rec, ingredient=tomato, qty=Decimal("0.3"))
        r = client.post(reverse("recipe-produce", args=[rec.id]), {"portions": "10"}, format="json")
        self.assertEqual(r.status_code, 201)
        tomato.refresh_from_db()
        self.assertEqual(tomato.current_stock, Decimal("7.000"))  # 10 − 0.3×10
        prep = Ingredient.objects.get(name="Prep: Gravy")
        self.assertEqual(prep.current_stock, Decimal("10.000"))
        # Movements carry the 'production' kind (spec §4).
        self.assertEqual(tomato.movements.first().kind, "production")
        self.assertEqual(prep.movements.first().kind, "production")


class MenuItemMappingTests(TestCase):
    """Menu Item Mapping tab (spec §6): list, map (create/replace), unmap."""

    def setUp(self):
        from apps.accounts.models import User
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gm2", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.cat = Category.objects.create(name="Main")
        self.rice = Ingredient.objects.create(name="Rice", unit="kg",
                                              current_stock=Decimal("20"), unit_cost=Decimal("60"))
        self.mapped = MenuItem.objects.create(name="Dal", category=self.cat, price=Decimal("120"))
        rec = Recipe.objects.create(menu_item=self.mapped)
        RecipeLine.objects.create(recipe=rec, ingredient=self.rice, qty=Decimal("0.1"))
        self.unmapped = MenuItem.objects.create(name="Fried Rice", category=self.cat,
                                                price=Decimal("200"))

    def test_mapping_lists_unmapped_items_first(self):
        from django.urls import reverse
        r = self.client.get(reverse("recipe-mapping"))
        self.assertEqual(r.status_code, 200)
        rows = {x["name"]: x for x in r.data}
        self.assertIsNone(rows["Fried Rice"]["recipe_id"])
        self.assertIsNotNone(rows["Dal"]["recipe_id"])
        self.assertEqual(r.data[0]["name"], "Fried Rice")  # unmapped sort first

    def test_map_creates_recipe_and_replace_updates_lines(self):
        r = self.client.post("/api/recipes/", {
            "menu_item": self.unmapped.id,
            "lines": [{"ingredient": self.rice.id, "qty": "250", "unit": "g",
                       "wastage_pct": "10"}],
        }, format="json")
        self.assertEqual(r.status_code, 201)
        recipe = Recipe.objects.get(menu_item=self.unmapped)
        line = recipe.lines.get()
        self.assertEqual(line.unit, "g")
        # 0.25 kg × 1.1 wastage × ₹60 = ₹16.50 plate cost
        self.assertEqual(recipe.plate_cost, Decimal("16.5000000"))
        # Replacing swaps the BOM, not append
        r = self.client.post("/api/recipes/", {
            "menu_item": self.unmapped.id,
            "lines": [{"ingredient": self.rice.id, "qty": "0.3"}],
        }, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(recipe.lines.count(), 1)
        self.assertEqual(recipe.lines.get().qty, Decimal("0.300"))

    def test_map_validation(self):
        r = self.client.post("/api/recipes/", {"menu_item": self.unmapped.id, "lines": []},
                             format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/recipes/", {
            "menu_item": self.unmapped.id, "lines": [{"qty": "1"}]}, format="json")
        self.assertEqual(r.status_code, 400)  # no ingredient or sub-recipe
        r = self.client.post("/api/recipes/", {
            "menu_item": self.unmapped.id,
            "lines": [{"ingredient": self.rice.id, "qty": "-1"}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_create_recipe_with_new_dish_puts_it_on_menu_and_deducts(self):
        """One flow: recipe from raw materials creates the POS dish; selling it
        auto-deducts the raw materials (spec §2/§3/§5)."""
        r = self.client.post("/api/recipes/", {
            "new_item": {"name": "Veg Fried Rice", "price": "180",
                         "category": "Rice Bowls", "gst_rate": "5", "diet": "veg"},
            "lines": [{"ingredient": self.rice.id, "qty": "250", "unit": "g"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["menu_item_created"])
        dish = MenuItem.objects.get(name="Veg Fried Rice")
        self.assertTrue(dish.available)                       # sellable immediately
        self.assertEqual(dish.category.name, "Rice Bowls")    # category auto-created
        self.assertEqual(dish.recipe.plate_cost, Decimal("15.0000"))  # 0.25 kg × ₹60
        # Selling 2 plates draws 0.5 kg of rice from the store.
        order = Order.objects.create(mode=Order.DINEIN)
        line = OrderLine.objects.create(order=order, menu_item=dish, qty=2,
                                        unit_price=dish.price)
        from .services import deduct_for_newly_fired
        deduct_for_newly_fired(order, [line])
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.current_stock, Decimal("19.500"))
        # Duplicate dish names are rejected with a helpful message.
        r = self.client.post("/api/recipes/", {
            "new_item": {"name": "veg fried rice", "price": "180"},
            "lines": [{"ingredient": self.rice.id, "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_menu_categories_listing(self):
        from django.urls import reverse
        r = self.client.get(reverse("recipe-menu-categories"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Main", [c["name"] for c in r.data])

    def test_unmap_deletes_recipe(self):
        rid = Recipe.objects.get(menu_item=self.mapped).id
        r = self.client.delete(f"/api/recipes/{rid}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Recipe.objects.filter(menu_item=self.mapped).exists())


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
