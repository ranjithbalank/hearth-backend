from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.constants import ROLE_HOUSEKEEPING
from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin, shared_or_visible
from apps.masters.views import MasterViewSet
from apps.rooms.models import Room
from apps.rooms.serializers import RoomSerializer

from .models import ChecklistItem, HousekeepingTask, LinenItem, WorkOrder
from .serializers import (
    ChecklistItemSerializer, HousekeepingTaskSerializer, LinenItemSerializer, WorkOrderSerializer,
)

# Housekeeping status progression (BRD FR-HSK-001).
HK_NEXT = {
    Room.VACANT_DIRTY: Room.CLEANING,
    Room.CLEANING: Room.VACANT_CLEAN,
    Room.VACANT_CLEAN: Room.INSPECTED,
}


class HousekeepingViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "housekeeping"

    def list(self, request):
        # Rooms are branch-owned (security review 2026-07, finding B7) — same
        # "mine + not-yet-tagged" rule already used for Room elsewhere
        # (reservations/views.py).
        qs = shared_or_visible(Room.objects.select_related("room_type").all(), request)
        status_ = request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return Response(RoomSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def request_cleaning(self, request, pk=None):
        """Front desk flags a room for servicing (FR-HSK): occupied make-up-room
        requests and urgent vacant/dirty turnarounds land on the housekeeping
        board and in housekeeping's notifications."""
        room = shared_or_visible(Room.objects.all(), request).filter(pk=pk).first()
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
        room = shared_or_visible(Room.objects.all(), request).filter(pk=pk).first()
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
        # Keep an existing task in sync when a supervisor advances the room
        # directly instead of through the task's own start/complete actions —
        # a no-op when no open task exists, so properties that never assign
        # tasks keep working exactly as before.
        open_task = (HousekeepingTask.objects.filter(room=room)
                     .exclude(status=HousekeepingTask.DONE).first())
        if open_task:
            if before == Room.VACANT_DIRTY and nxt == Room.CLEANING and open_task.status == HousekeepingTask.ASSIGNED:
                open_task.status = HousekeepingTask.IN_PROGRESS
                open_task.started_at = timezone.now()
                open_task.save(update_fields=["status", "started_at"])
            elif before == Room.CLEANING and nxt == Room.VACANT_CLEAN:
                open_task.status = HousekeepingTask.DONE
                open_task.completed_at = timezone.now()
                open_task.save(update_fields=["status", "completed_at"])
        room.save(update_fields=["status", "cleaning_requested", "cleaning_note", "updated_at"])
        log_action(request.user, "hk_advance", entity="Room", entity_id=room.id,
                   before={"status": before}, after={"status": nxt})
        return Response(RoomSerializer(room).data)

    @action(detail=True, methods=["post"])
    def minibar(self, request, pk=None):
        """Post a consumed minibar item to the room's open folio (FR-HSK-005)."""
        room = shared_or_visible(Room.objects.all(), request).filter(pk=pk).first()
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
        rooms = shared_or_visible(Room.objects.all(), request)
        out = {}
        for r in rooms:
            out[r.status] = out.get(r.status, 0) + 1
        return Response(out)


class HousekeepingTaskViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    """Per-attendant work session on a room turn: assignment, checklist,
    timer and linen issued — purely additive on top of the shared board."""

    module = "housekeeping"
    queryset = HousekeepingTask.objects.select_related("room", "assigned_to").all()
    serializer_class = HousekeepingTaskSerializer

    def get_queryset(self):
        qs = shared_or_visible(super().get_queryset(), self.request, field="room__location")
        if self.request.query_params.get("mine"):
            qs = qs.filter(assigned_to=self.request.user)
        status_ = self.request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        if self.request.query_params.get("open"):
            qs = qs.exclude(status=HousekeepingTask.DONE)
        return qs

    def perform_create(self, serializer):
        room = serializer.validated_data["room"]
        if HousekeepingTask.objects.filter(room=room).exclude(status=HousekeepingTask.DONE).exists():
            raise ValidationError(
                {"detail": f"Room {room.number} already has an open task — reassign or complete it first"})
        assigned_to = serializer.validated_data.get("assigned_to")
        if not assigned_to or assigned_to.role != ROLE_HOUSEKEEPING:
            raise ValidationError({"detail": "Pick an active Housekeeping-role attendant to assign"})
        checklist = [{"label": c.label, "done": False}
                     for c in ChecklistItem.objects.filter(active=True).order_by("sort_order", "label")]
        task = serializer.save(checklist=checklist)
        log_action(self.request.user, "hk_task_assigned", entity="HousekeepingTask", entity_id=task.id,
                   after={"room": room.number, "assigned_to": assigned_to.username})

    @action(detail=False, methods=["get"])
    def attendants(self, request):
        """Housekeeping-role logins for the assignment picker — same shape
        as pos.TableViewSet.captains."""
        from apps.accounts.models import User
        qs = (User.objects.filter(role=ROLE_HOUSEKEEPING, is_active=True)
              .order_by("first_name", "username"))
        return Response([{"id": u.id, "name": u.get_full_name() or u.username} for u in qs])

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        """Hand the task to a different attendant, or send assigned_to=null
        to clear it — mirrors pos.TableViewSet.assign_captain."""
        task = self.get_object()
        attendant_id = request.data.get("assigned_to")
        if attendant_id is None:
            task.assigned_to = None
        else:
            from apps.accounts.models import User
            attendant = User.objects.filter(pk=attendant_id, role=ROLE_HOUSEKEEPING).first()
            if not attendant:
                return Response({"detail": "Not a Housekeeping login — pick a valid attendant"}, status=400)
            task.assigned_to = attendant
        task.save(update_fields=["assigned_to"])
        log_action(request.user, "hk_task_reassign", entity="HousekeepingTask", entity_id=task.id,
                   after={"assigned_to": attendant_id})
        return Response(HousekeepingTaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def toggle_item(self, request, pk=None):
        task = self.get_object()
        idx = request.data.get("index")
        try:
            idx = int(idx)
            task.checklist[idx]["done"] = not task.checklist[idx]["done"]
        except (TypeError, ValueError, IndexError, KeyError):
            return Response({"detail": "invalid checklist index"}, status=400)
        task.save(update_fields=["checklist"])
        return Response(HousekeepingTaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        task = self.get_object()
        if task.status != HousekeepingTask.ASSIGNED:
            return Response({"detail": f"task is already {task.get_status_display().lower()}"}, status=400)
        task.status = HousekeepingTask.IN_PROGRESS
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at"])
        room = task.room
        if room.status == Room.VACANT_DIRTY:
            room.status = Room.CLEANING
            room.save(update_fields=["status", "updated_at"])
        log_action(request.user, "hk_task_start", entity="HousekeepingTask", entity_id=task.id,
                   after={"room": room.number})
        return Response(HousekeepingTaskSerializer(task).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = self.get_object()
        if task.status == HousekeepingTask.DONE:
            return Response({"detail": "task is already done"}, status=400)
        task.status = HousekeepingTask.DONE
        task.completed_at = timezone.now()
        task.linen_issued = request.data.get("linen_issued") or {}
        task.note = str(request.data.get("note", ""))[:300]
        task.save(update_fields=["status", "completed_at", "linen_issued", "note"])
        room = task.room
        if room.status == Room.CLEANING:
            room.status = Room.VACANT_CLEAN
            room.cleaning_requested = False
            room.cleaning_note = ""
            room.save(update_fields=["status", "cleaning_requested", "cleaning_note", "updated_at"])
        log_action(request.user, "hk_task_complete", entity="HousekeepingTask", entity_id=task.id,
                   after={"room": room.number, "linen_issued": task.linen_issued})
        return Response(HousekeepingTaskSerializer(task).data)


class ChecklistItemViewSet(MasterViewSet):
    queryset = ChecklistItem.objects.all()
    serializer_class = ChecklistItemSerializer

    def in_use_count(self, obj):
        # Snapshotted onto each task at creation time — no live reference,
        # so a checklist item is always safe to edit or remove.
        return 0

    def perform_create(self, serializer):
        obj = serializer.save()
        log_action(self.request.user, "master_created", entity=type(obj).__name__,
                   entity_id=obj.id, after={"name": obj.label})

    def perform_update(self, serializer):
        before = {"name": serializer.instance.label, "active": serializer.instance.active}
        obj = serializer.save()
        log_action(self.request.user, "master_updated", entity=type(obj).__name__,
                   entity_id=obj.id, before=before, after={"name": obj.label, "active": obj.active})

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        log_action(request.user, "master_deleted", entity=type(obj).__name__,
                   entity_id=obj.id, before={"name": obj.label})
        return super(MasterViewSet, self).destroy(request, *args, **kwargs)


class LinenItemViewSet(MasterViewSet):
    queryset = LinenItem.objects.all()
    serializer_class = LinenItemSerializer

    def in_use_count(self, obj):
        return 0


class WorkOrderViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "engineering"
    queryset = WorkOrder.objects.select_related("room").all()
    serializer_class = WorkOrderSerializer

    def perform_create(self, serializer):
        wo = serializer.save(raised_by=self.request.user.username)
        # Cross-module seam: a maintenance order takes a VACANT room out of
        # service. An occupied room stays with its guest — the stay's
        # occupancy state must never be clobbered by a repair ticket (the
        # desk moves the guest via room-move if the room is unusable); it
        # can go OOO after check-out if the fault is still open.
        if wo.room and wo.room.status not in (Room.OOO, Room.OCCUPIED):
            wo.room.status = Room.OOO
            wo.room.ooo_reason = wo.title
            wo.room.save(update_fields=["status", "ooo_reason", "updated_at"])
        log_action(self.request.user, "workorder_raised", entity="WorkOrder", entity_id=wo.id,
                   after={"title": wo.title,
                          "room": wo.room.number if wo.room_id else None})

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
