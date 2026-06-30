"""Recipe → inventory consumption seam (BRD FR-INV-003)."""
from apps.inventory.models import apply_movement

from .models import Recipe


def deduct_for_newly_fired(order, lines):
    """Deduct recipe ingredients for the given (newly fired) order lines.

    Passing only the freshly-fired lines makes incremental KOTs correct — a
    second round consumes only its own items, never re-deducting earlier rounds.
    """
    deductions = []
    for line in lines:
        # A combo deducts each component's recipe (BRD FR-MNU-006); a plain item
        # deducts its own recipe. Build the list of (menu_item, multiplier) to draw.
        combo = list(line.menu_item.combo_components.select_related("component").all())
        targets = ([(c.component, c.qty * line.qty) for c in combo]
                   if combo else [(line.menu_item, line.qty)])
        for item, mult in targets:
            recipe = (Recipe.objects.filter(menu_item=item)
                      .prefetch_related("lines__ingredient").first())
            if not recipe:
                continue
            for rl in recipe.lines.all():
                consumed = rl.qty * mult
                apply_movement(
                    rl.ingredient, "consumption", -consumed,
                    reason=f"{mult}× {item.name}", source=f"order:{order.id}",
                )
                deductions.append((rl.ingredient.name, consumed))
    return deductions
