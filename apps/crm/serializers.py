from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_customer_type_display", read_only=True)
    stay_count = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()

    def get_stay_count(self, obj):
        # Annotated on the list queryset; fall back to a count elsewhere.
        # Bill-to-company folios count as hotel activity too.
        stays = getattr(obj, "stay_count", None)
        btc = getattr(obj, "btc_folio_count", None)
        if stays is None:
            stays = obj.reservations.count()
        if btc is None:
            btc = obj.city_ledger_folios.count()
        return stays + btc

    def get_order_count(self, obj):
        n = getattr(obj, "order_count", None)
        return obj.orders.count() if n is None else n

    class Meta:
        model = Customer
        fields = [
            "id", "name", "mobile", "email", "address", "locality", "gstin",
            "customer_type", "type_label", "btc_enabled", "outstanding",
            "loyalty_points", "marketing_consent", "tags", "created_at",
            "stay_count", "order_count",
        ]
