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
        # base_qty converts recipe units (e.g. g) into the ingredient's costed
        # base unit (e.g. kg) and includes wastage, so cost is accurate.
        return sum(
            (line.base_qty() * (line.ingredient.unit_cost or Decimal("0"))
             for line in self.lines.all() if line.ingredient_id),
            start=Decimal("0"),
        )


# Unit conversion factors into an ingredient's base unit (spec §2):
# a recipe can say "250 g" against an ingredient stocked in kg.
UNIT_FACTORS = {
    ("g", "kg"): Decimal("0.001"), ("kg", "g"): Decimal("1000"),
    ("ml", "l"): Decimal("0.001"), ("l", "ml"): Decimal("1000"),
}


def convert_qty(qty, from_unit, to_unit):
    """Convert qty between units; same/unknown pairs pass through unchanged."""
    if not from_unit or from_unit == to_unit:
        return qty
    factor = UNIT_FACTORS.get((from_unit, to_unit))
    return qty * factor if factor else qty


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
    # Recipe-side unit; blank = the ingredient's base unit. Allows "250 g" of
    # an ingredient stocked in kg — converted on deduction (spec §2).
    unit = models.CharField(max_length=12, blank=True, default="")
    # Expected prep wastage: consumption is inflated by this % (spec §2).
    wastage_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    def __str__(self):
        target = self.ingredient.name if self.ingredient_id else f"sub:{self.sub_recipe}"
        return f"{self.qty} {self.unit or ''} {target}".replace("  ", " ")

    def base_qty(self):
        """Per-dish draw in the ingredient's base unit, including wastage."""
        qty = convert_qty(self.qty, self.unit, self.ingredient.unit if self.ingredient_id else "")
        if self.wastage_pct:
            qty = qty * (Decimal("1") + self.wastage_pct / Decimal("100"))
        return qty


class ProductionBatch(models.Model):
    """Batch prep of a sub-recipe (spec §4 Production/Preparation).

    Making N portions consumes the recipe's BOM as 'production' movements and
    credits an auto-created prep ingredient ("Prep: <item>", unit pc) so dish
    recipes can draw from prepped stock instead of raw ingredients.
    """

    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="production_batches")
    portions = models.DecimalField(max_digits=10, decimal_places=2)
    produced_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "production batches"

    def __str__(self):
        return f"{self.portions}× {self.menu_item.name}"
