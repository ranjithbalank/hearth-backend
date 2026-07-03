from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin

from .models import ProductionBatch, Recipe, RecipeLine


class RecipeViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "recipes"

    @action(detail=False, methods=["get"])
    def mapping(self, request):
        """Menu Item Mapping (spec §6): every POS item with its recipe, or
        flagged unmapped — unmapped items skip stock deduction silently."""
        from apps.pos.models import MenuItem
        items = (MenuItem.objects.select_related("category")
                 .prefetch_related("recipe__lines__ingredient", "recipe__lines__sub_recipe"))
        out = []
        for m in items:
            recipe = getattr(m, "recipe", None)
            out.append({
                "menu_item": m.id,
                "name": m.name,
                "category": m.category.name,
                "price": str(m.price),
                "available": m.available,
                "recipe_id": recipe.id if recipe else None,
                "plate_cost": str(recipe.plate_cost) if recipe else None,
                "lines": [
                    {"ingredient": l.ingredient_id, "sub_recipe": l.sub_recipe_id,
                     "name": l.ingredient.name if l.ingredient_id else f"[prep] {l.sub_recipe.name}",
                     "qty": str(l.qty), "unit": l.unit, "wastage_pct": str(l.wastage_pct)}
                    for l in recipe.lines.all()
                ] if recipe else [],
            })
        out.sort(key=lambda r: (r["recipe_id"] is not None, r["name"]))
        return Response(out)

    def create(self, request):
        """Create or replace the recipe (BOM) for a menu item (spec §2/§6).
        Body: {menu_item, lines: [{ingredient?|sub_recipe?, qty, unit?, wastage_pct?}]}"""
        from apps.pos.models import MenuItem
        item = MenuItem.objects.filter(pk=request.data.get("menu_item")).first()
        if not item:
            return Response({"detail": "menu item not found"}, status=400)
        lines = request.data.get("lines") or []
        if not lines:
            return Response({"detail": "at least one ingredient line is required"}, status=400)
        parsed = []
        for l in lines:
            try:
                qty = Decimal(str(l.get("qty", 0)))
            except Exception:
                return Response({"detail": "invalid quantity"}, status=400)
            if qty <= 0:
                return Response({"detail": "quantities must be positive"}, status=400)
            if not (l.get("ingredient") or l.get("sub_recipe")):
                return Response({"detail": "each line needs an ingredient or sub-recipe"}, status=400)
            parsed.append(l)
        recipe, created = Recipe.objects.get_or_create(menu_item=item)
        recipe.lines.all().delete()
        for l in parsed:
            RecipeLine.objects.create(
                recipe=recipe,
                ingredient_id=l.get("ingredient") or None,
                sub_recipe_id=l.get("sub_recipe") or None,
                qty=Decimal(str(l["qty"])),
                unit=l.get("unit", "") or "",
                wastage_pct=Decimal(str(l.get("wastage_pct") or 0)),
            )
        log_action(request.user, "recipe_mapped", entity="Recipe", entity_id=recipe.id,
                   after={"menu_item": item.name, "lines": len(parsed)})
        return Response({"id": recipe.id, "menu_item": item.id,
                         "plate_cost": str(recipe.plate_cost)},
                        status=201 if created else 200)

    def destroy(self, request, pk=None):
        """Unmap: delete the recipe so the item no longer draws stock."""
        recipe = Recipe.objects.select_related("menu_item").filter(pk=pk).first()
        if not recipe:
            return Response({"detail": "recipe not found"}, status=404)
        name = recipe.menu_item.name
        recipe.delete()
        log_action(request.user, "recipe_unmapped", entity="Recipe", entity_id=pk,
                   after={"menu_item": name})
        return Response(status=204)

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
