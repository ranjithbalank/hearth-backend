from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.tax import service as tax

from .models import Event, FunctionSpace


def _event_dict(e):
    return {
        "id": e.id, "title": e.title, "host": e.host, "space": e.space.name,
        "event_date": e.event_date, "covers": e.covers,
        "package_amount": str(e.package_amount), "status": e.status, "billed": e.billed,
    }


class BanquetViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "banquets"

    def list(self, request):
        spaces = [{"id": s.id, "name": s.name, "capacity": s.capacity}
                  for s in FunctionSpace.objects.all()]
        events = [_event_dict(e) for e in Event.objects.select_related("space")]
        return Response({"spaces": spaces, "events": events})

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        e = Event.objects.filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        e.status = Event.CONFIRMED
        e.save(update_fields=["status"])
        return Response(_event_dict(e))

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        """Bill the event (banquets attract 18% GST). Marks billed; returns the tax split."""
        e = Event.objects.filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        breakdown = tax.compute(e.package_amount, tax.BANQUET_RATE)
        e.billed = True
        e.status = Event.COMPLETED
        e.save(update_fields=["billed", "status"])
        log_action(request.user, "event_bill", entity="Event", entity_id=e.id,
                   after={"total": str(breakdown["total"])})
        return Response({"event": _event_dict(e), "tax": {k: str(v) for k, v in breakdown.items()}})
