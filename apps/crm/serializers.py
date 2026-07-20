from rest_framework import serializers

from .models import Customer, LoyaltyLedger, LoyaltyReward, LoyaltyTier


class LoyaltyTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyTier
        fields = ["id", "name", "min_lifetime_points", "earn_multiplier", "active"]


class LoyaltyRewardSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyReward
        fields = ["id", "name", "points_cost", "kind", "value", "active", "redeemed_count"]
        read_only_fields = ["redeemed_count"]


class LoyaltyLedgerSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = LoyaltyLedger
        fields = ["id", "kind", "kind_label", "points", "balance_after", "note", "created_at"]


class CustomerSerializer(serializers.ModelSerializer):
    type_label = serializers.CharField(source="get_customer_type_display", read_only=True)
    stay_count = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    tier_name = serializers.SerializerMethodField()
    earn_multiplier = serializers.SerializerMethodField()

    def get_tier_name(self, obj):
        tier = obj.current_tier()
        return tier.name if tier else None

    def get_earn_multiplier(self, obj):
        tier = obj.current_tier()
        return str(tier.earn_multiplier) if tier else "1.00"

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
            "loyalty_points", "lifetime_points", "tier_name", "earn_multiplier",
            "date_of_birth", "anniversary_date",
            "marketing_consent", "tags", "created_at",
            "stay_count", "order_count",
        ]
