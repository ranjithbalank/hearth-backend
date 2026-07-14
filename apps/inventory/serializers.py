from rest_framework import serializers

from apps.accounts.constants import ROLE_CHEF

from .models import Ingredient, IngredientCategory, StockMovement, Uom


class UomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Uom
        fields = ["id", "code", "name"]


class IngredientCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = IngredientCategory
        fields = ["id", "name"]


class IngredientSerializer(serializers.ModelSerializer):
    below_par = serializers.BooleanField(read_only=True)
    below_min = serializers.BooleanField(read_only=True)

    class Meta:
        model = Ingredient
        fields = [
            "id", "code", "name", "location", "unit", "category", "current_stock",
            "min_stock_level", "reorder_level", "unit_cost",
            "storage_location", "expiry_date", "below_par", "below_min",
        ]
        read_only_fields = ["code"]
        # See Room/RoomSerializer: DRF force-requires every field in a
        # UniqueConstraint, including this conditional one — the DB
        # constraint still enforces it; the viewset turns a genuine
        # duplicate's IntegrityError into a clean 400.
        validators = []

    def to_representation(self, instance):
        """Chef's only reason to browse Inventory is picking ingredients for a
        recipe or checking kitchen stock — not the purchase rate (that's a
        procurement figure for Store Keeper / Restaurant Manager to see)."""
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request is not None and getattr(request.user, "role", "") == ROLE_CHEF:
            data.pop("unit_cost", None)
        return data

    def validate_unit(self, value):
        if not Uom.objects.filter(code=value).exists():
            raise serializers.ValidationError(
                f"unknown unit '{value}' — add it in the Units of Measurement master first")
        return value

    def validate_category(self, value):
        if value and not IngredientCategory.objects.filter(name=value).exists():
            raise serializers.ValidationError(
                f"unknown category '{value}' — add it in the Categories master first")
        return value


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
