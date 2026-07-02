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

    @action(detail=True, methods=["post"])
    def request_cleaning(self, request, pk=None):
        """Front desk flags a room for servicing (FR-HSK): occupied make-up-room
        requests and urgent vacant/dirty turnarounds land on the housekeeping
        board and in housekeeping's notifications."""
        room = Room.objects.filter(pk=pk).first()
        if not room:
            return Response({"detail": "room not found"}, status=404)
        room.cleaning_requested = True
        room.cleaning_note = str(request.data.get("note", ""))[:160]
        room.save(update_fields=["cleaning_requested", "cleaning_note", "updated_at"])
        log_action(request.user, "hk_request", entity="Room", entity_id=room.id,
                   after={"room": room.number, "note": room.cleaning_note})
        return Response(RoomSerializer(room).data)

    @action(detail=True, methods=["patch"])
    def advance(self, request, pk=None):
        """Move a room one step along Dirty→Cleaning→Clean→Inspected.
        An occupied room with a cleaning request is simply marked serviced."""
        room = Room.objects.filter(pk=pk).first()
        if not room:
            return Response({"detail": "room not found"}, status=404)
        if room.status == Room.OCCUPIED and room.cleaning_requested:
            room.cleaning_requested = False
            room.cleaning_note = ""
            room.save(update_fields=["cleaning_requested", "cleaning_note", "updated_at"])
            log_action(request.user, "hk_serviced", entity="Room", entity_id=room.id,
                       after={"room": room.number})
            return Response(RoomSerializer(room).data)
        nxt = HK_NEXT.get(room.status)
        if not nxt:
            return Response({"detail": f"no transition from {room.status}"}, status=400)
        before = room.status
        room.status = nxt
        if room.cleaning_requested:  # picking up the room satisfies the request
            room.cleaning_requested = False
            room.cleaning_note = ""
        room.save(update_fields=["status", "cleaning_requested", "cleaning_note", "updated_at"])
        log_action(request.user, "hk_advance", entity="Room", entity_id=room.id,
                   before={"status": before}, after={"status": nxt})
        return Response(RoomSerializer(room).data)

    @action(detail=True, methods=["post"])
    def minibar(self, request, pk=None):
        """Post a consumed minibar item to the room's open folio (FR-HSK-005)."""
        room = Room.objects.filter(pk=pk).first()
        if not room:
            return Response({"detail": "room not found"}, status=404)
        from apps.frontoffice.models import Folio, FolioLine
        from apps.frontoffice.services import post_charge
        from apps.tax import service as tax
        folio = Folio.objects.filter(room=room, status=Folio.OPEN).first()
        if not folio:
            return Response({"detail": "no open folio for this room"}, status=400)
        item = request.data.get("item", "Minibar item")
        from decimal import Decimal
        amount = Decimal(str(request.data.get("amount", 0)))
        if amount <= 0:
            return Response({"detail": "amount required"}, status=400)
        post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description=f"Minibar: {item}",
                    amount=amount, gst_rate=tax.FNB_RATE, source="minibar", user=request.user)
        log_action(request.user, "minibar_post", entity="Folio", entity_id=folio.id,
                   after={"item": item, "amount": str(amount)})
        return Response({"posted": True, "folio": folio.id})

    @action(detail=False, methods=["get", "post"])
    def lost_found(self, request):
        """List or log lost-and-found items (FR-HSK-007)."""
        from .models import LostFoundItem
        if request.method == "POST":
            LostFoundItem.objects.create(
                description=request.data.get("description", ""),
                location=request.data.get("location", ""),
                handler=request.data.get("handler", ""),
                guest_contact=request.data.get("guest_contact", ""))
        return Response([
            {"id": i.id, "description": i.description, "location": i.location,
             "handler": i.handler, "status": i.status, "found_at": i.found_at}
            for i in LostFoundItem.objects.all()[:50]
        ])

    @action(detail=True, methods=["post"])
    def lost_found_claim(self, request, pk=None):
        from .models import LostFoundItem
        item = LostFoundItem.objects.filter(pk=pk).first()
        if not item:
            return Response({"detail": "not found"}, status=404)
        item.status = request.data.get("status", LostFoundItem.CLAIMED)
        item.save(update_fields=["status"])
        return Response({"id": item.id, "status": item.status})

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
