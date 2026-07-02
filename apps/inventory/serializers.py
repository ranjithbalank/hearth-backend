from rest_framework import serializers

from .models import Ingredient, StockMovement


class IngredientSerializer(serializers.ModelSerializer):
    below_par = serializers.BooleanField(read_only=True)
    below_min = serializers.BooleanField(read_only=True)

    class Meta:
        model = Ingredient
        fields = [
            "id", "code", "name", "unit", "category", "current_stock",
            "min_stock_level", "reorder_level", "unit_cost",
            "storage_location", "expiry_date", "below_par", "below_min",
        ]
        read_only_fields = ["code"]


class StockMovementSerializer(serializers.ModelSerializer):
    ingredient_name = serializers.CharField(source="ingredient.name", read_only=True)
    unit = serializers.CharField(source="ingredient.unit", read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id", "ingredient", "ingredient_name", "unit", "kind", "kind_label",
            "qty", "balance", "reason", "source", "created_by", "created_at",
        ]
