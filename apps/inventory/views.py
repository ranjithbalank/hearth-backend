from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin

from .models import Ingredient, apply_movement
from .serializers import IngredientSerializer


class IngredientViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "inventory"
    queryset = Ingredient.objects.all()
    serializer_class = IngredientSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("below_par") == "1":
            # below_par is a property, so resolve to ids the DB can filter on
            ids = [i.id for i in qs if i.below_par]
            return qs.filter(id__in=ids)
        return qs

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        ing = self.get_object()
        qty = request.data.get("qty", 0)
        apply_movement(ing, "adjustment", qty, reason=request.data.get("reason", ""))
        return Response(IngredientSerializer(ing).data)

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        low = [i for i in Ingredient.objects.all() if i.below_par]
        return Response({"count": len(low), "items": IngredientSerializer(low, many=True).data})
