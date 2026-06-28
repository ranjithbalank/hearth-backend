from rest_framework import serializers

from .models import Ingredient


class IngredientSerializer(serializers.ModelSerializer):
    below_par = serializers.BooleanField(read_only=True)

    class Meta:
        model = Ingredient
        fields = [
            "id", "name", "unit", "category", "current_stock",
            "reorder_level", "unit_cost", "below_par",
        ]
