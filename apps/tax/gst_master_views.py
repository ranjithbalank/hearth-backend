from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import Property, log_action
from apps.accounts.permissions import ModuleViewSetMixin

from .models import GstSlab


class GstMasterViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """GST rate slabs + with/without-GST billing mode (BRD 5.23)."""

    module = "gstmaster"

    def list(self, request):
        prop = Property.objects.first()
        slabs = [
            {"id": g.id, "name": g.name, "rate": str(g.rate), "hsn_sac": g.hsn_sac,
             "applies_to": g.applies_to}
            for g in GstSlab.objects.all()
        ]
        return Response({
            "billing_mode": prop.gst_billing_mode if prop else "with_gst",
            "slabs": slabs,
        })

    @action(detail=False, methods=["post"])
    def billing_mode(self, request):
        prop = Property.objects.first()
        mode = request.data.get("mode")
        if mode not in ("with_gst", "without_gst"):
            return Response({"detail": "mode must be with_gst or without_gst"}, status=400)
        prop.gst_billing_mode = mode
        prop.save(update_fields=["gst_billing_mode"])
        log_action(request.user, "gst_billing_mode", entity="Property", entity_id=prop.id,
                   after={"mode": mode})
        return Response({"billing_mode": mode})
