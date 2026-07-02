from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin

from .models import Ingredient, StockMovement, apply_movement
from .serializers import IngredientSerializer, StockMovementSerializer


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
        """Manual stock adjustment (spec §4: Stock Adjustment)."""
        ing = self.get_object()
        apply_movement(ing, StockMovement.ADJUSTMENT, request.data.get("qty", 0),
                       reason=request.data.get("reason", ""), user=request.user)
        return Response(IngredientSerializer(ing).data)

    @action(detail=True, methods=["post"])
    def waste(self, request, pk=None):
        """Wastage entry — always stock-out, reason required (spec §4/§6)."""
        ing = self.get_object()
        qty = abs(Decimal(str(request.data.get("qty", 0))))
        reason = request.data.get("reason", "").strip()
        if qty <= 0:
            return Response({"detail": "quantity must be positive"}, status=400)
        if not reason:
            return Response({"detail": "a reason is required for wastage"}, status=400)
        kind = (StockMovement.EXPIRY if request.data.get("expired")
                else StockMovement.WASTAGE)
        apply_movement(ing, kind, -qty, reason=reason, user=request.user)
        return Response(IngredientSerializer(ing).data)

    @action(detail=True, methods=["post"])
    def count(self, request, pk=None):
        """Physical stock count: book the counted quantity, the difference is
        posted as a 'count' movement so the ledger explains the correction (§4/§6)."""
        ing = self.get_object()
        counted = Decimal(str(request.data.get("counted", 0)))
        if counted < 0:
            return Response({"detail": "counted quantity can't be negative"}, status=400)
        diff = counted - (ing.current_stock or Decimal("0"))
        if diff != 0:
            apply_movement(ing, StockMovement.COUNT, diff,
                           reason=f"physical count: booked {counted}", user=request.user)
        return Response(IngredientSerializer(ing).data)

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        low = [i for i in Ingredient.objects.all() if i.below_par]
        return Response({"count": len(low), "items": IngredientSerializer(low, many=True).data})

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Materials expiring within ?days= (default 7) or already expired (§6)."""
        days = int(request.query_params.get("days", 7))
        horizon = timezone.localdate() + timedelta(days=days)
        items = Ingredient.objects.filter(expiry_date__isnull=False,
                                          expiry_date__lte=horizon).order_by("expiry_date")
        return Response(IngredientSerializer(items, many=True).data)

    @action(detail=False, methods=["get"])
    def movements(self, request):
        """Inventory movements / consumption register (spec §4/§6).
        Filters: ?kind=consumption&ingredient=<id>&days=30"""
        qs = StockMovement.objects.select_related("ingredient")
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        ingredient = request.query_params.get("ingredient")
        if ingredient:
            qs = qs.filter(ingredient_id=ingredient)
        days = int(request.query_params.get("days", 30))
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
        return Response(StockMovementSerializer(qs[:500], many=True).data)

    @action(detail=False, methods=["get"])
    def consumption_report(self, request):
        """Raw-material consumption + purchase-vs-consumption over ?days= (spec §7)."""
        days = int(request.query_params.get("days", 30))
        since = timezone.now() - timedelta(days=days)
        rows = []
        for ing in Ingredient.objects.all():
            sums = (ing.movements.filter(created_at__gte=since)
                    .values("kind").annotate(total=Sum("qty")))
            by_kind = {r["kind"]: r["total"] or Decimal("0") for r in sums}
            consumed = -(by_kind.get(StockMovement.CONSUMPTION, Decimal("0")))
            wasted = -(by_kind.get(StockMovement.WASTAGE, Decimal("0"))
                       + by_kind.get(StockMovement.EXPIRY, Decimal("0")))
            purchased = by_kind.get(StockMovement.RECEIPT, Decimal("0"))
            if not (consumed or wasted or purchased):
                continue
            rows.append({
                "ingredient": ing.name, "code": ing.code, "unit": ing.unit,
                "consumed": consumed, "wasted": wasted, "purchased": purchased,
                "consumption_cost": consumed * (ing.unit_cost or Decimal("0")),
                "in_stock": ing.current_stock,
            })
        rows.sort(key=lambda r: -r["consumption_cost"])
        return Response({"days": days, "rows": rows})
