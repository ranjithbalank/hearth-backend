"""Recipe → inventory consumption seam (BRD FR-INV-003)."""
from decimal import Decimal

from apps.inventory.models import apply_movement

from .models import Recipe


def _consume_recipe(menu_item, mult, order, deductions, depth=0):
    """Draw down a menu item's recipe; expand sub-recipes recursively (FR-RCP-003)."""
    if depth > 5:
        return
    recipe = (Recipe.objects.filter(menu_item=menu_item)
              .prefetch_related("lines__ingredient", "lines__sub_recipe").first())
    if not recipe:
        return
    for rl in recipe.lines.all():
        if rl.sub_recipe_id:
            _consume_recipe(rl.sub_recipe, rl.qty * mult, order, deductions, depth + 1)
        elif rl.ingredient_id:
            consumed = rl.qty * mult
            apply_movement(rl.ingredient, "consumption", -consumed,
                           reason=f"{mult}× {menu_item.name}", source=f"order:{order.id}")
            deductions.append((rl.ingredient.name, consumed))


def deduct_for_newly_fired(order, lines):
    """Deduct recipe ingredients for the given (newly fired) order lines.

    Passing only the freshly-fired lines makes incremental KOTs correct — a
    second round consumes only its own items, never re-deducting earlier rounds.
    """
    deductions: list = []
    for line in lines:
        # A combo deducts each component's recipe (BRD FR-MNU-006); a plain item
        # deducts its own recipe. Build the list of (menu_item, multiplier) to draw.
        combo = list(line.menu_item.combo_components.select_related("component").all())
        targets = ([(c.component, Decimal(c.qty * line.qty)) for c in combo]
                   if combo else [(line.menu_item, Decimal(line.qty))])
        for item, mult in targets:
            _consume_recipe(item, mult, order, deductions)
    return deductions
