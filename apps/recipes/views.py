from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin

from .models import ProductionBatch, Recipe


class RecipeViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "recipes"

    def list(self, request):
        out = []
        for r in Recipe.objects.select_related("menu_item").prefetch_related(
                "lines__ingredient", "lines__sub_recipe"):
            cost = r.plate_cost
            price = r.menu_item.price
            margin = round(float((price - cost) / price * 100), 1) if price else 0
            out.append({
                "id": r.id,
                "menu_item": r.menu_item_id,
                "item": r.menu_item.name,
                "price": str(price),
                "plate_cost": str(cost),
                "margin_pct": margin,
                "ingredients": [
                    {"name": l.ingredient.name if l.ingredient_id else f"[prep] {l.sub_recipe.name}",
                     "qty": str(l.qty),
                     "unit": (l.unit or l.ingredient.unit) if l.ingredient_id else "portion"}
                    for l in r.lines.all()
                ],
            })
        return Response(out)

    @action(detail=True, methods=["post"])
    def produce(self, request, pk=None):
        """Batch-prep N portions of this recipe (spec §4 Production/Preparation).

        Consumes the BOM as 'production' movements and credits an auto-created
        prep ingredient ("Prep: <item>") so dish recipes can draw prepped stock.
        """
        from apps.inventory.models import Ingredient, apply_movement
        recipe = (Recipe.objects.select_related("menu_item")
                  .prefetch_related("lines__ingredient").filter(pk=pk).first())
        if not recipe:
            return Response({"detail": "recipe not found"}, status=404)
        try:
            portions = Decimal(str(request.data.get("portions", 0)))
        except Exception:
            return Response({"detail": "invalid portions"}, status=400)
        if portions <= 0:
            return Response({"detail": "portions must be positive"}, status=400)
        consumed = []
        for line in recipe.lines.all():
            if not line.ingredient_id:
                continue  # nested sub-recipes: prep them as their own batches
            qty = line.base_qty() * portions
            apply_movement(line.ingredient, "production", -qty,
                           reason=f"prep {portions}× {recipe.menu_item.name}",
                           source=f"batch:{recipe.menu_item.name}", user=request.user)
            consumed.append({"ingredient": line.ingredient.name, "qty": str(qty),
                             "unit": line.ingredient.unit})
        # Credit the prepped stock so recipes can consume "Prep: X" directly.
        prep, _ = Ingredient.objects.get_or_create(
            name=f"Prep: {recipe.menu_item.name}",
            defaults={"unit": "pc", "category": "Prepared"})
        apply_movement(prep, "production", portions,
                       reason=f"batch {portions}× {recipe.menu_item.name}",
                       source=f"batch:{recipe.menu_item.name}", user=request.user)
        batch = ProductionBatch.objects.create(menu_item=recipe.menu_item, portions=portions,
                                               produced_by=request.user.username)
        log_action(request.user, "production_batch", entity="ProductionBatch",
                   entity_id=batch.id, after={"portions": str(portions)})
        return Response({"batch": batch.id, "portions": str(portions),
                         "prep_ingredient": prep.name, "consumed": consumed}, status=201)
