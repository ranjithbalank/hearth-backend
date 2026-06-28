from rest_framework import viewsets
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin

from .models import Employee


class HrViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "hr"

    def list(self, request):
        return Response([
            {"id": e.id, "name": e.name, "department": e.department, "role": e.role,
             "phone": e.phone, "shifts": e.shifts, "status": e.status}
            for e in Employee.objects.all()
        ])
