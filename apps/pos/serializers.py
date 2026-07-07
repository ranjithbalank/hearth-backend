from rest_framework import serializers

from .models import (
    AddOn,
    AddOnGroup,
    BarTable,
    Category,
    MenuItem,
    Order,
    OrderLine,
    Table,
    TableReservation,
    TillEntry,
    TillSession,
    Variant,
)


class TableSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Table
        fields = ["id", "name", "section", "seats", "shape", "status", "status_label", "location"]


class BarTableSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = BarTable
        fields = ["id", "name", "section", "seats", "shape", "status", "status_label", "location"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "sort_order", "is_bar"]


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
    channel_prices = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id", "name", "short_code", "category", "category_name", "price",
            "gst_rate", "diet", "station", "bar_menu", "available", "variants", "addon_groups",
            "channel_prices", "image",
        ]

    def get_channel_prices(self, obj):
        return {cp.channel: str(cp.price) for cp in obj.channel_prices.all()}


class OrderLineSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="display_name", read_only=True)
    gst_rate = serializers.DecimalField(
        source="menu_item.gst_rate", max_digits=4, decimal_places=1, read_only=True
    )
    kot_no = serializers.CharField(source="kot.number", read_only=True, default=None)

    class Meta:
        model = OrderLine
        fields = ["id", "menu_item", "name", "variant", "addons", "qty",
                  "unit_price", "note", "kot_fired", "kot_no", "gst_rate"]


class OrderSerializer(serializers.ModelSerializer):
    lines = OrderLineSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()
    table_name = serializers.CharField(source="table.name", read_only=True, default=None)
    bar_table_name = serializers.CharField(source="bar_table.name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    coupon_code = serializers.CharField(source="coupon.code", read_only=True, default=None)

    class Meta:
        model = Order
        fields = [
            "id", "mode", "department", "table", "table_name", "bar_table", "bar_table_name",
            "customer", "covers", "captain",
            "status", "status_label", "folio", "kot_no", "lines", "totals", "created_at",
            "discount_kind", "discount_value", "discount_reason", "coupon_code",
            "loyalty_redeemed", "source_platform", "external_ref", "online_status", "prepaid",
            "brand", "token_no", "client_uuid",
        ]

    def get_totals(self, obj):
        t = obj.totals()
        return {k: str(v) for k, v in t.items()}


class TillEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = TillEntry
        fields = ["id", "kind", "amount", "reason", "created_by", "created_at"]


class TillSessionSerializer(serializers.ModelSerializer):
    entries = TillEntrySerializer(many=True, read_only=True)
    cash_in = serializers.SerializerMethodField()
    cash_out = serializers.SerializerMethodField()
    tender_totals = serializers.SerializerMethodField()

    class Meta:
        model = TillSession
        fields = ["id", "status", "opened_by", "opening_float", "opened_at",
                  "closed_at", "closed_by", "counted_cash", "expected_cash",
                  "variance", "denominations", "note", "entries",
                  "cash_in", "cash_out", "tender_totals"]

    def get_cash_in(self, obj):
        return str(obj.cash_in_out()[0])

    def get_cash_out(self, obj):
        return str(obj.cash_in_out()[1])

    def get_tender_totals(self, obj):
        from decimal import Decimal
        return [{**t, "amount": str(Decimal(t["amount"]).quantize(Decimal("0.01")))}
                for t in obj.tender_totals()]


class TableReservationSerializer(serializers.ModelSerializer):
    table_name = serializers.CharField(source="table.name", read_only=True, default=None)

    class Meta:
        model = TableReservation
        fields = ["id", "kind", "table", "table_name", "name", "mobile",
                  "party_size", "reserved_for", "status", "note", "created_at"]
        read_only_fields = ["status"]
