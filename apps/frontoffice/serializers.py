from rest_framework import serializers

from .models import Folio, FolioLine, NightAuditRun, Settlement


class FolioLineSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = FolioLine
        fields = [
            "id", "kind", "kind_label", "description", "source", "taxable",
            "cgst", "sgst", "total", "gst_rate", "created_at",
        ]


class SettlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Settlement
        fields = ["id", "folio", "tender", "amount", "reference", "tip", "created_at"]


class FolioSerializer(serializers.ModelSerializer):
    lines = FolioLineSerializer(many=True, read_only=True)
    settlements = SettlementSerializer(many=True, read_only=True)
    room_number = serializers.CharField(source="room.number", read_only=True, default=None)
    charges_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    effective_billing_mode = serializers.SerializerMethodField()

    class Meta:
        model = Folio
        fields = [
            "id", "reservation", "guest_name", "room", "room_number", "status",
            "routing", "opened_at", "settled_at", "invoice_no", "lines",
            "settlements", "charges_total", "paid_total", "balance",
            "guest_type", "company_name", "billing_mode", "effective_billing_mode",
        ]

    def get_effective_billing_mode(self, obj):
        from . import services
        return services.effective_billing_mode(obj)


class NightAuditRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = NightAuditRun
        fields = [
            "id", "business_date", "rooms_posted", "room_revenue", "tax_posted",
            "completed", "created_at",
        ]
