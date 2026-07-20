from django.utils import timezone
from rest_framework import serializers

from .models import ChecklistItem, HousekeepingTask, LinenItem, WorkOrder


class WorkOrderSerializer(serializers.ModelSerializer):
    room_number = serializers.CharField(source="room.number", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "id", "room", "room_number", "title", "detail", "status",
            "status_label", "raised_by", "created_at", "closed_at",
        ]


class ChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = ["id", "label", "sort_order", "active"]


class LinenItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = LinenItem
        fields = ["id", "name", "par_per_room", "active"]


def attendant_on_leave_today(attendant_user):
    """An attendant on approved leave today shouldn't keep a room's task
    stuck on them all day — mirrors pos.captain_on_leave_today exactly."""
    if attendant_user is None:
        return False
    from datetime import date

    employee = getattr(attendant_user, "employee_record", None)
    if not employee:
        return False
    from apps.hr.models import LeaveRequest
    today = date.today()
    return employee.leave_requests.filter(
        status=LeaveRequest.APPROVED, start_date__lte=today, end_date__gte=today,
    ).exists()


class HousekeepingTaskSerializer(serializers.ModelSerializer):
    room_number = serializers.CharField(source="room.number", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    assigned_to_on_leave = serializers.SerializerMethodField()
    elapsed_minutes = serializers.SerializerMethodField()

    class Meta:
        model = HousekeepingTask
        fields = [
            "id", "room", "room_number", "assigned_to", "assigned_to_name",
            "assigned_to_on_leave", "status", "status_label", "checklist",
            "linen_issued", "note", "created_at", "started_at", "completed_at",
            "elapsed_minutes",
        ]

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to:
            return None
        return obj.assigned_to.get_full_name() or obj.assigned_to.username

    def get_assigned_to_on_leave(self, obj):
        return attendant_on_leave_today(obj.assigned_to)

    def get_elapsed_minutes(self, obj):
        if not obj.started_at:
            return None
        end = obj.completed_at or timezone.now()
        return round((end - obj.started_at).total_seconds() / 60, 1)
