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
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)
    room_type_code = serializers.CharField(source="room_type.code", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_sellable = serializers.BooleanField(read_only=True)

    def validate(self, attrs):
        # A new room must never silently land on floor 1: when no floor is
        # sent, read it off the room number (500 → 5, 1203 → 12) — same rule
        # as the live grid's floor fixer. Model default 1 only remains for
        # non-numeric numbers ("Cabin A").
        if self.instance is None and "floor" not in getattr(self, "initial_data", {}):
            import re
            m = re.match(r"\d+", str(attrs.get("number") or ""))
            if m:
                n = int(m.group())
                attrs["floor"] = (n // 100 if n >= 100 else n // 10 if n >= 10 else n) or 1
        return attrs

    class Meta:
        model = Room
        fields = [
            "id", "number", "branch", "location", "location_name",
            "room_type", "room_type_name", "room_type_code",
            "floor", "view", "smoking", "status", "status_label", "ooo_reason",
            "cleaning_requested", "cleaning_note", "is_sellable", "updated_at",
        ]
        # DRF auto-forces every field in ANY UniqueConstraint to required=True,
        # even a conditional one — it can't reason about `condition=`, so it
        # would wrongly demand `location` on every create. The two constraints
        # (see Room.Meta) still enforce uniqueness at the DB level; the
        # viewset turns that IntegrityError into a clean 400 instead.
        validators = []
