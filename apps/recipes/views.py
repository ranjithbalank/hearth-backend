from rest_framework import viewsets
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin

from .models import Recipe


class RecipeViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "recipes"

    def list(self, request):
        out = []
        for r in Recipe.objects.select_related("menu_item").prefetch_related("lines__ingredient"):
            cost = r.plate_cost
            price = r.menu_item.price
            margin = round(float((price - cost) / price * 100), 1) if price else 0
            out.append({
                "id": r.id,
                "item": r.menu_item.name,
                "price": str(price),
                "plate_cost": str(cost),
                "margin_pct": margin,
                "ingredients": [
                    {"name": l.ingredient.name, "qty": str(l.qty), "unit": l.ingredient.unit}
                    for l in r.lines.all()
                ],
            })
        return Response(out)
