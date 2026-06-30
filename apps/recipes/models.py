from decimal import Decimal

from django.db import models

from apps.inventory.models import Ingredient
from apps.pos.models import MenuItem


class Recipe(models.Model):
    """Bill of materials mapping a menu item to its ingredients (BRD 5.19)."""

    menu_item = models.OneToOneField(MenuItem, on_delete=models.CASCADE, related_name="recipe")

    def __str__(self):
        return f"Recipe for {self.menu_item.name}"

    @property
    def plate_cost(self):
        return sum(
            (line.qty * (line.ingredient.unit_cost or Decimal("0")) for line in self.lines.all()),
            start=Decimal("0"),
        )


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="recipe_lines", null=True, blank=True
    )
    # A line may instead consume a sub-recipe / semi-finished prep (BRD FR-RCP-003),
    # which is itself a MenuItem with its own Recipe — expanded on deduction.
    sub_recipe = models.ForeignKey(
        "pos.MenuItem", on_delete=models.PROTECT, null=True, blank=True, related_name="used_in_recipes"
    )
    qty = models.DecimalField(max_digits=10, decimal_places=3, help_text="per single dish")

    def __str__(self):
        target = self.ingredient.name if self.ingredient_id else f"sub:{self.sub_recipe}"
        return f"{self.qty} {target}"
