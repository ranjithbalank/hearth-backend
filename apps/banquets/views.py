from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.tax import service as tax

from .models import Event, FunctionSpace


def _event_dict(e):
    return {
        "id": e.id, "title": e.title, "host": e.host, "contact": e.contact,
        "event_type": e.event_type, "space": e.space.name, "space_id": e.space_id,
        "event_date": e.event_date, "covers": e.covers, "deposit": str(e.deposit),
        "package_amount": str(e.package_amount), "status": e.status, "billed": e.billed,
        "food_covers": e.food_covers, "food_pref": e.food_pref, "beo_status": e.beo_status,
    }


class BanquetViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "banquets"

    def list(self, request):
        spaces = [{"id": s.id, "name": s.name, "capacity": s.capacity}
                  for s in FunctionSpace.objects.all()]
        events = [_event_dict(e) for e in Event.objects.select_related("space")]
        return Response({"spaces": spaces, "events": events})

    def create(self, request):
        """Create an event booking for a walk-in enquiry (BRD FR-BQT-002/003)."""
        space = FunctionSpace.objects.filter(pk=request.data.get("space")).first()
        if not space:
            return Response({"detail": "function space required"}, status=400)
        event_date = request.data.get("event_date")
        if not event_date:
            return Response({"detail": "event date required"}, status=400)
        # Prevent double-booking a confirmed hall on the same date (FR-BQT-003).
        if Event.objects.filter(space=space, event_date=event_date,
                                status=Event.CONFIRMED).exists():
            return Response({"detail": f"{space.name} is already booked on {event_date}"},
                            status=status.HTTP_409_CONFLICT)
        covers = int(request.data.get("covers", 0) or 0)
        if covers > space.capacity:
            return Response({"detail": f"{space.name} seats {space.capacity}; {covers} requested"},
                            status=400)
        e = Event.objects.create(
            space=space, title=request.data.get("title", "Event"),
            host=request.data.get("host", ""), contact=request.data.get("contact", ""),
            event_type=request.data.get("event_type", ""), event_date=event_date,
            covers=covers, package_amount=Decimal(str(request.data.get("package_amount", 0) or 0)),
            deposit=Decimal(str(request.data.get("deposit", 0) or 0)),
            food_covers=int(request.data.get("food_covers", 0) or 0),
            food_pref=request.data.get("food_pref", ""),
            status=Event.TENTATIVE,
        )
        log_action(request.user, "event_create", entity="Event", entity_id=e.id,
                   after={"space": space.name, "date": str(event_date)})
        return Response(_event_dict(e), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def availability(self, request):
        """Which halls are free on a given date (FR-BQT-001)."""
        d = request.query_params.get("date")
        booked = set(Event.objects.filter(event_date=d, status=Event.CONFIRMED)
                     .values_list("space_id", flat=True)) if d else set()
        return Response([
            {"id": s.id, "name": s.name, "capacity": s.capacity, "available": s.id not in booked}
            for s in FunctionSpace.objects.all()
        ])

    @action(detail=True, methods=["get"])
    def beo_pdf(self, request, pk=None):
        """Download the Banquet Event Order as a PDF (FR-BQT-004)."""
        from django.http import HttpResponse

        from apps.accounts.views import get_property
        from .beo_pdf import build_beo_pdf
        e = Event.objects.filter(pk=pk).select_related("space").first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        pdf = build_beo_pdf(e, get_property().name)
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="BEO-{e.id}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        e = Event.objects.filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        e.status = Event.CONFIRMED
        fields = ["status"]
        # Fire the BEO catering prep to the kitchen display (FR-BQT-004).
        if e.food_covers > 0 and not e.beo_status:
            e.beo_status = "pending"
            fields.append("beo_status")
            log_action(request.user, "beo_fired", entity="Event", entity_id=e.id,
                       after={"food_covers": e.food_covers, "food_pref": e.food_pref})
        e.save(update_fields=fields)
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
