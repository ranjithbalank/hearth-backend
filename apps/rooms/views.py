from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import BranchScopedMixin, BranchUniqueFriendlyMixin, ModuleViewSetMixin

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


class RoomViewSet(BranchScopedMixin, BranchUniqueFriendlyMixin, ModuleViewSetMixin, viewsets.ModelViewSet):
    """Live room grid + status updates (BRD 5.1 / 5.2).

    Read access is shared by several modules, so we gate on `livegrid` (hms).
    """

    module = "livegrid"
    queryset = Room.objects.select_related("room_type").all()
    serializer_class = RoomSerializer
    duplicate_message = "A room with this number already exists there."

    def get_queryset(self):
        qs = super().get_queryset()
        branch = self.request.query_params.get("branch")
        status_ = self.request.query_params.get("status")
        if branch:
            qs = qs.filter(branch=branch)
        if status_:
            qs = qs.filter(status=status_)
        return qs

    @action(detail=False, methods=["post"])
    def infer_floors(self, request):
        """One-time setup helper: set each room's floor from its number
        (101 → 1, 204 → 2, 1203 → 12). Rooms whose numbers don't start with
        digits are left alone. Room-master gated, like the import."""
        import re
        from apps.accounts.constants import role_can_access
        if not role_can_access(getattr(request.user, "role", ""), "roommaster"):
            return Response({"detail": "floor setup needs the Room Master screen (manager/admin)"},
                            status=403)
        changed = 0
        for room in self.get_queryset():
            m = re.match(r"(\d+)", room.number)
            if not m:
                continue
            n = int(m.group(1))
            floor = n // 100 if n >= 100 else n // 10 if n >= 10 else n
            if floor >= 1 and room.floor != floor:
                room.floor = floor
                room.save(update_fields=["floor"])
                changed += 1
        log_action(request.user, "rooms_infer_floors", entity="Room",
                   after={"changed": changed})
        return Response({"changed": changed})

    @action(detail=False, methods=["get", "post"], url_path="import")
    def import_rooms(self, request):
        """Bulk room onboarding: GET the CSV template, POST it filled.
        Master-level only (roommaster) — the shared livegrid read gate lets
        the whole desk see rooms, not mass-create them. Unknown room types
        are created on the fly with the given base rate."""
        from decimal import Decimal, InvalidOperation
        from apps.accounts.constants import role_can_access
        from apps.accounts.csv_import import parse_upload, template_response
        if not role_can_access(getattr(request.user, "role", ""), "roommaster"):
            return Response({"detail": "room import needs the Room Master screen (manager/admin)"},
                            status=403)
        columns = ["number", "room_type_code", "room_type_name", "base_rate", "floor"]
        if request.method == "GET":
            return template_response("rooms-template.csv", columns, [
                ["101", "STD", "Standard", "4500", "1"],
                ["102", "STD", "Standard", "4500", "1"],
                ["201", "DLX", "Deluxe", "6500", "2"],
            ])
        try:
            rows = parse_upload(request)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        created, skipped, errors = [], [], []
        for lineno, row in rows:
            number = row.get("number", "")
            if not number:
                continue
            if Room.objects.filter(number__iexact=number).exists():
                skipped.append(number)
                continue
            try:
                code = (row.get("room_type_code") or "STD").upper()
                rt, _ = RoomType.objects.get_or_create(
                    code=code,
                    defaults={"name": row.get("room_type_name") or code,
                              "base_rate": Decimal(row.get("base_rate") or "0")})
                Room.objects.create(number=number, room_type=rt,
                                    floor=int(row.get("floor") or 1))
                created.append(number)
            except (ValueError, InvalidOperation) as e:
                errors.append({"row": lineno, "name": number, "reason": str(e)[:120]})
        log_action(request.user, "room_import", entity="Room",
                   after={"created": len(created), "errors": len(errors)})
        return Response({"created": len(created), "skipped_existing": skipped,
                         "errors": errors})

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
