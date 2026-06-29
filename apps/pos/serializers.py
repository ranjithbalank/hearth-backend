from rest_framework import serializers

from .models import AddOn, AddOnGroup, Category, MenuItem, Order, OrderLine, Table, Variant


class TableSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Table
        fields = ["id", "name", "section", "seats", "shape", "status", "status_label"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "sort_order"]


class VariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variant
        fields = ["id", "name", "price", "short_code"]


class AddOnSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddOn
        fields = ["id", "name", "price"]


class AddOnGroupSerializer(serializers.ModelSerializer):
    options = AddOnSerializer(many=True, read_only=True)

    class Meta:
        model = AddOnGroup
        fields = ["id", "name", "min_select", "max_select", "options"]


class MenuItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    variants = VariantSerializer(many=True, read_only=True)
    addon_groups = AddOnGroupSerializer(many=True, read_only=True)

    class Meta:
        model = MenuItem
        fields = [
            "id", "name", "short_code", "category", "category_name", "price",
            "gst_rate", "diet", "station", "available", "variants", "addon_groups",
        ]


class OrderLineSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="display_name", read_only=True)
    gst_rate = serializers.DecimalField(
        source="menu_item.gst_rate", max_digits=4, decimal_places=1, read_only=True
    )

    class Meta:
        model = OrderLine
        fields = ["id", "menu_item", "name", "variant", "addons", "qty",
                  "unit_price", "note", "kot_fired", "gst_rate"]


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()
    table_name = serializers.CharField(source="table.name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    coupon_code = serializers.CharField(source="coupon.code", read_only=True, default=None)

    class Meta:
        model = Order
        fields = [
            "id", "mode", "table", "table_name", "customer", "covers", "captain",
            "status", "status_label", "folio", "kot_no", "lines", "totals", "created_at",
            "discount_kind", "discount_value", "discount_reason", "coupon_code",
            "loyalty_redeemed",
        ]

    def get_totals(self, obj):
        t = obj.totals()
        return {k: str(v) for k, v in t.items()}
