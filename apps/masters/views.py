"""Masters CRUD (Settings > Masters).

Reads are open to ANY authenticated user — every dropdown that consumes a
master (the indent form's department picker, the POS tender buttons, the HR
employee form) belongs to roles that don't have the 'settings' module.
Writes stay locked to settings-capable roles (Admin/GM/MD/Super Admin).
"""
from rest_framework import viewsets
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModulePermission

from .models import Department, Designation, KitchenStation, PaymentMethod
from .serializers import (
    DepartmentSerializer,
    DesignationSerializer,
    KitchenStationSerializer,
    PaymentMethodSerializer,
)


class MasterViewSet(viewsets.ModelViewSet):
    """Shared behavior: read-open/write-gated permissions, audit logging,
    and the deactivate-don't-delete rule for rows already referenced by
    historical records."""

    module = "settings"

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [ModulePermission()]

    def in_use_count(self, obj) -> int:
        """How many existing records reference this master row by name."""
        return 0

    def perform_create(self, serializer):
        obj = serializer.save()
        log_action(self.request.user, "master_created", entity=type(obj).__name__,
                   entity_id=obj.id, after={"name": obj.name})

    def perform_update(self, serializer):
        before = {"name": serializer.instance.name, "active": serializer.instance.active}
        obj = serializer.save()
        log_action(self.request.user, "master_updated", entity=type(obj).__name__,
                   entity_id=obj.id, before=before,
                   after={"name": obj.name, "active": obj.active})

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        used = self.in_use_count(obj)
        if used:
            return Response(
                {"detail": f"'{obj.name}' is used by {used} record(s) — "
                           f"mark it inactive instead so history stays intact."},
                status=400)
        log_action(request.user, "master_deleted", entity=type(obj).__name__,
                   entity_id=obj.id, before={"name": obj.name})
        return super().destroy(request, *args, **kwargs)


class DepartmentViewSet(MasterViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer

    def in_use_count(self, obj):
        from apps.hr.models import Employee
        from apps.matreq.models import MaterialRequest
        return (Employee.objects.filter(department=obj.name).count()
                + MaterialRequest.objects.filter(department=obj.name).count())


class DesignationViewSet(MasterViewSet):
    queryset = Designation.objects.all()
    serializer_class = DesignationSerializer

    def in_use_count(self, obj):
        from apps.hr.models import Employee
        return Employee.objects.filter(role=obj.name).count()


class KitchenStationViewSet(MasterViewSet):
    queryset = KitchenStation.objects.all()
    serializer_class = KitchenStationSerializer

    def in_use_count(self, obj):
        from apps.pos.models import MenuItem
        return MenuItem.objects.filter(station=obj.name).count()

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.is_bar:
            return Response(
                {"detail": f"'{obj.name}' is the bar station — deactivate it instead."},
                status=400)
        return super().destroy(request, *args, **kwargs)


class PaymentMethodViewSet(MasterViewSet):
    queryset = PaymentMethod.objects.all()
    serializer_class = PaymentMethodSerializer

    def in_use_count(self, obj):
        from apps.frontoffice.models import Settlement
        return Settlement.objects.filter(tender=obj.name).count()

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.builtin:
            return Response(
                {"detail": f"'{obj.name}' is a built-in tender — deactivate it instead."},
                status=400)
        return super().destroy(request, *args, **kwargs)
