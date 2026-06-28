from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.rooms.models import Room
from apps.rooms.serializers import RoomSerializer

from .models import WorkOrder
from .serializers import WorkOrderSerializer

# Housekeeping status progression (BRD FR-HSK-001).
HK_NEXT = {
    Room.VACANT_DIRTY: Room.CLEANING,
    Room.CLEANING: Room.VACANT_CLEAN,
    Room.VACANT_CLEAN: Room.INSPECTED,
}


class HousekeepingViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "housekeeping"

    def list(self, request):
        qs = Room.objects.select_related("room_type").all()
        status_ = request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return Response(RoomSerializer(qs, many=True).data)

    @action(detail=True, methods=["patch"])
    def advance(self, request, pk=None):
        """Move a room one step along Dirty→Cleaning→Clean→Inspected."""
        room = Room.objects.filter(pk=pk).first()
        if not room:
            return Response({"detail": "room not found"}, status=404)
        nxt = HK_NEXT.get(room.status)
        if not nxt:
            return Response({"detail": f"no transition from {room.status}"}, status=400)
        before = room.status
        room.status = nxt
        room.save(update_fields=["status", "updated_at"])
        log_action(request.user, "hk_advance", entity="Room", entity_id=room.id,
                   before={"status": before}, after={"status": nxt})
        return Response(RoomSerializer(room).data)

    @action(detail=False, methods=["get"])
    def counters(self, request):
        rooms = Room.objects.all()
        out = {}
        for r in rooms:
            out[r.status] = out.get(r.status, 0) + 1
        return Response(out)


class WorkOrderViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "engineering"
    queryset = WorkOrder.objects.select_related("room").all()
    serializer_class = WorkOrderSerializer

    def perform_create(self, serializer):
        wo = serializer.save()
        # Cross-module seam: a maintenance order takes the room out of service.
        if wo.room and wo.room.status != Room.OOO:
            wo.room.status = Room.OOO
            wo.room.ooo_reason = wo.title
            wo.room.save(update_fields=["status", "ooo_reason", "updated_at"])

    @action(detail=True, methods=["patch"])
    def advance(self, request, pk=None):
        wo = self.get_object()
        order = [WorkOrder.OPEN, WorkOrder.IN_PROGRESS, WorkOrder.DONE]
        idx = order.index(wo.status)
        if idx < len(order) - 1:
            wo.status = order[idx + 1]
            if wo.status == WorkOrder.DONE:
                wo.closed_at = timezone.now()
                # Returning to service -> room becomes dirty for housekeeping.
                if wo.room and wo.room.status == Room.OOO:
                    wo.room.status = Room.VACANT_DIRTY
                    wo.room.ooo_reason = ""
                    wo.room.save(update_fields=["status", "ooo_reason", "updated_at"])
            wo.save()
        return Response(WorkOrderSerializer(wo).data)
