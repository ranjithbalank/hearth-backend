from rest_framework import serializers

from .models import RatePlan, Room, RoomType


class RoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = ["id", "code", "name", "base_rate", "max_occupancy", "gst_slab"]


class RatePlanSerializer(serializers.ModelSerializer):
    room_type_code = serializers.CharField(source="room_type.code", read_only=True)

    class Meta:
        model = RatePlan
        fields = ["id", "name", "room_type", "room_type_code", "rate", "inclusions"]


class RoomSerializer(serializers.ModelSerializer):
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)
    room_type_code = serializers.CharField(source="room_type.code", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_sellable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Room
        fields = [
            "id", "number", "branch", "room_type", "room_type_name", "room_type_code",
            "floor", "view", "smoking", "status", "status_label", "ooo_reason",
            "cleaning_requested", "cleaning_note", "is_sellable", "updated_at",
        ]
