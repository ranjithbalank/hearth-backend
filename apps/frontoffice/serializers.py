from rest_framework import serializers

from .models import Folio, FolioLine, NightAuditRun, Settlement


class FolioLineSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    # Rule 46(f)'s SAC code, resolved server-side from GST Master. The browser
    # print path and the server PDF are two renderers of the same invoice, so
    # the code they show has to come from one place — deriving it again in
    # TypeScript is how the two drift apart.
    hsn_sac = serializers.SerializerMethodField()

    class Meta:
        model = FolioLine
        fields = [
            "id", "kind", "kind_label", "description", "source", "taxable",
            "cgst", "sgst", "total", "gst_rate", "hsn_sac", "created_at",
        ]

    def get_hsn_sac(self, obj):
        from apps.tax.models import hsn_for_kinds

        # Cached per serialization pass — one query for the whole folio rather
        # than one per line.
        cache = self.context.setdefault("_hsn", {}) if self.context is not None else {}
        if obj.kind not in cache:
            cache.update(hsn_for_kinds({obj.kind}))
        return cache.get(obj.kind, "")


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
    pending_charges = serializers.SerializerMethodField()
    projected_balance = serializers.SerializerMethodField()
    # The scan/signature blobs themselves stay out of folio payloads (PII +
    # size) — only presence flags here; the images come from /registration/.
    has_id_scan = serializers.SerializerMethodField()
    has_signature = serializers.SerializerMethodField()

    class Meta:
        model = Folio
        fields = [
            "id", "reservation", "guest_name", "room", "room_number", "status",
            "routing", "opened_at", "settled_at", "invoice_no", "lines",
            "settlements", "charges_total", "paid_total", "balance",
            "guest_type", "company_name", "billing_mode", "effective_billing_mode",
            "pending_charges", "projected_balance", "location",
            "has_id_scan", "has_signature",
        ]
        read_only_fields = ["location"]  # inherited from the reservation/room at check-in

    def get_has_id_scan(self, obj):
        return bool(obj.id_scan)

    def get_has_signature(self, obj):
        return bool(obj.signature)

    def get_effective_billing_mode(self, obj):
        from . import services
        return services.effective_billing_mode(obj)

    def get_pending_charges(self, obj):
        """Room nights that will post at check-out — shown so the desk sees
        real numbers before collecting (they'd otherwise read ₹0 pre-audit)."""
        from . import services
        return [{"description": c["description"], "total": str(c["total"])}
                for c in services.pending_room_charges(obj)]

    def get_projected_balance(self, obj):
        from . import services
        pending = sum((c["total"] for c in services.pending_room_charges(obj)), start=0)
        return str((obj.balance or 0) + pending)


class NightAuditRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = NightAuditRun
        fields = [
            "id", "business_date", "rooms_posted", "room_revenue", "tax_posted",
            "completed", "created_at",
        ]
