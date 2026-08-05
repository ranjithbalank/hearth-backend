from rest_framework import serializers

from .models import Reservation


class ReservationSerializer(serializers.ModelSerializer):
    room_type_code = serializers.CharField(source="room_type.code", read_only=True)
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)
    room_number = serializers.CharField(source="room.number", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    guest_mobile = serializers.CharField(source="guest.mobile", read_only=True, default="")

    class Meta:
        model = Reservation
        fields = [
            "id", "guest", "guest_name", "guest_mobile", "room_type", "room_type_code",
            "room_type_name", "rate_plan",
            "room", "room_number", "checkin_date", "checkout_date", "nights",
            "source", "source_label", "status", "status_label", "rate", "deposit",
            "prepaid", "notes", "channel_name", "ota_ref", "created_at",
            "precheckin", "precheckin_done", "location",
        ]
        read_only_fields = ["location"]  # set server-side from the booker's active branch

    def validate_guest_name(self, value):
        from apps.accounts.validators import validate_person_name
        return validate_person_name(value)

    def validate(self, attrs):
        """Dates are the source of truth for a stay; `nights` follows them.

        Both were accepted independently, so a booking could carry a checkout
        before its check-in (negative room nights into revenue, occupancy and
        ADR) or a `nights` that simply disagreed with the dates it was billed
        against. Deriving nights here means the two can no longer drift.
        """
        from apps.accounts.validators import validate_date_order
        attrs = super().validate(attrs)
        checkin = attrs.get("checkin_date") or getattr(self.instance, "checkin_date", None)
        checkout = attrs.get("checkout_date") or getattr(self.instance, "checkout_date", None)
        validate_date_order(checkin, checkout)
        if checkin and checkout:
            attrs["nights"] = (checkout - checkin).days
        return attrs
