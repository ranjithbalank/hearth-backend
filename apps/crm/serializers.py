from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_customer_type_display", read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id", "name", "mobile", "email", "address", "locality", "gstin",
            "customer_type", "type_label", "btc_enabled", "outstanding",
            "loyalty_points", "marketing_consent", "tags", "created_at",
        ]
