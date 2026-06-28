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
        recipe = (
            Recipe.objects.filter(menu_item=line.menu_item)
            .prefetch_related("lines__ingredient")
            .first()
        )
        if not recipe:
            continue
        for rl in recipe.lines.all():
            consumed = rl.qty * line.qty
            apply_movement(
                rl.ingredient, "consumption", -consumed,
                reason=f"{line.qty}× {line.menu_item.name}",
                source=f"order:{order.id}",
            )
            deductions.append((rl.ingredient.name, consumed))
    return deductions
