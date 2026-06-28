from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin

from .models import RatePlan, Room, RoomType
from .serializers import RatePlanSerializer, RoomSerializer, RoomTypeSerializer


class RoomTypeViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "roommaster"
    queryset = RoomType.objects.all()
    serializer_class = RoomTypeSerializer


class RatePlanViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "roommaster"
    queryset = RatePlan.objects.select_related("room_type").all()
    serializer_class = RatePlanSerializer


class RoomViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Live room grid + status updates (BRD 5.1 / 5.2).

    Read access is shared by several modules, so we gate on `livegrid` (hms).
    """

    module = "livegrid"
    queryset = Room.objects.select_related("room_type").all()
    serializer_class = RoomSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        branch = self.request.query_params.get("branch")
        status_ = self.request.query_params.get("status")
        if branch:
            qs = qs.filter(branch=branch)
        if status_:
            qs = qs.filter(status=status_)
        return qs

    @action(detail=True, methods=["patch"])
    def status(self, request, pk=None):
        room = self.get_object()
        new_status = request.data.get("status")
        valid = dict(Room.STATUS_CHOICES)
        if new_status not in valid:
            return Response({"detail": "invalid status"}, status=400)
        before = room.status
        room.status = new_status
        if new_status == Room.OOO:
            room.ooo_reason = request.data.get("reason", "")
        room.save(update_fields=["status", "ooo_reason", "updated_at"])
        log_action(request.user, "room_status", entity="Room", entity_id=room.id,
                   before={"status": before}, after={"status": new_status})
        return Response(RoomSerializer(room).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        rooms = self.get_queryset()
        by_branch = {}
        for r in rooms:
            b = by_branch.setdefault(r.branch, {"branch": r.branch, "total": 0,
                                                "occupied": 0, "vacant": 0, "ooo": 0})
            b["total"] += 1
            if r.status == Room.OCCUPIED:
                b["occupied"] += 1
            elif r.status == Room.OOO:
                b["ooo"] += 1
            else:
                b["vacant"] += 1
        return Response(list(by_branch.values()))
