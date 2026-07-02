from rest_framework import serializers

from .models import Reservation


class ReservationSerializer(serializers.ModelSerializer):
    room_type_code = serializers.CharField(source="room_type.code", read_only=True)
    room_number = serializers.CharField(source="room.number", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    guest_mobile = serializers.CharField(source="guest.mobile", read_only=True, default="")

    class Meta:
        model = Reservation
        fields = [
            "id", "guest", "guest_name", "guest_mobile", "room_type", "room_type_code", "rate_plan",
            "room", "room_number", "checkin_date", "checkout_date", "nights",
            "source", "source_label", "status", "status_label", "rate", "deposit",
            "prepaid", "notes", "channel_name", "ota_ref", "created_at",
            "precheckin", "precheckin_done",
        ]
