from datetime import datetime, time as dtime
from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin, shared_or_visible
from apps.tax import service as tax

from .models import CateringRate, Event, FunctionSpace


def _parse_time(v):
    """Coerce a 'HH:MM' string (or time) to a time; None if empty/invalid."""
    if not v:
        return None
    if isinstance(v, dtime):
        return v
    try:
        return datetime.strptime(str(v)[:5], "%H:%M").time()
    except ValueError:
        return None


def _overlaps(s1, e1, s2, e2):
    """Two time ranges overlap. If either range is open (no times), treat as a
    whole-day booking that conflicts with anything else that day."""
    if not (s1 and e1) or not (s2 and e2):
        return True
    return s1 < e2 and s2 < e1


def _find_clash(space, event_date, start, end, exclude_pk=None):
    """Return a confirmed event in this hall/date whose time overlaps, else None."""
    qs = Event.objects.filter(space=space, event_date=event_date, status=Event.CONFIRMED)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    for other in qs:
        if _overlaps(start, end, other.start_time, other.end_time):
            return other
    return None


def _clash_msg(space, other):
    when = ""
    if other.start_time and other.end_time:
        when = f" ({other.start_time.strftime('%H:%M')}–{other.end_time.strftime('%H:%M')})"
    return f"{space.name} is already booked on {other.event_date}{when} for '{other.title}'"


def _event_dict(e):
    return {
        "id": e.id, "title": e.title, "host": e.host, "contact": e.contact,
        "event_type": e.event_type, "space": e.space.name, "space_id": e.space_id,
        "event_date": e.event_date,
        "start_time": e.start_time.strftime("%H:%M") if e.start_time else "",
        "end_time": e.end_time.strftime("%H:%M") if e.end_time else "",
        "covers": e.covers, "deposit": str(e.deposit),
        "package_amount": str(e.package_amount), "status": e.status, "billed": e.billed,
        "food_covers": e.food_covers, "food_pref": e.food_pref,
        "food_veg": e.food_veg, "food_nonveg": e.food_nonveg,
        "veg_rate": str(e.veg_rate), "nonveg_rate": str(e.nonveg_rate),
        "catering_amount": str(e.catering_amount), "bill_subtotal": str(e.bill_subtotal),
        "beo_status": e.beo_status, "beo_no": e.beo_no,
    }


class BanquetViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "banquets"

    def list(self, request):
        # Banquets had no branch scoping anywhere in this module (security
        # review 2026-07, finding B-banquets) — halls and their events now
        # follow the same "mine + not-yet-tagged" rule as every other module.
        spaces = shared_or_visible(FunctionSpace.objects.all(), request)
        events = shared_or_visible(Event.objects.select_related("space"), request,
                                   field="space__location")
        return Response({
            "spaces": [{"id": s.id, "name": s.name, "capacity": s.capacity} for s in spaces],
            "events": [_event_dict(e) for e in events],
        })

    def create(self, request):
        """Create an event booking for a walk-in enquiry (BRD FR-BQT-002/003)."""
        space = shared_or_visible(FunctionSpace.objects.all(), request).filter(
            pk=request.data.get("space")).first()
        if not space:
            return Response({"detail": "function space required"}, status=400)
        event_date = request.data.get("event_date")
        if not event_date:
            return Response({"detail": "event date required"}, status=400)
        # Prevent double-booking: a confirmed hall clashes only if the *times*
        # overlap on that date (FR-BQT-003), so non-overlapping same-day events
        # (e.g. a lunch and an evening reception) are allowed.
        start = _parse_time(request.data.get("start_time"))
        end = _parse_time(request.data.get("end_time"))
        clash = _find_clash(space, event_date, start, end)
        if clash:
            return Response({"detail": _clash_msg(space, clash)}, status=status.HTTP_409_CONFLICT)
        covers = int(request.data.get("covers", 0) or 0)
        if covers > space.capacity:
            return Response({"detail": f"{space.name} seats {space.capacity}; {covers} requested"},
                            status=400)
        # Catering: for "both" we take a veg/nonveg split; otherwise a single count.
        food_pref = request.data.get("food_pref", "")
        food_veg = int(request.data.get("food_veg", 0) or 0)
        food_nonveg = int(request.data.get("food_nonveg", 0) or 0)
        if food_pref == "veg":
            food_nonveg = 0
        elif food_pref == "nonveg":
            food_veg = 0
        food_covers = food_veg + food_nonveg
        # Rates fall back to the property's standard catering prices when omitted.
        defaults = CateringRate.get_solo()
        veg_rate = Decimal(str(request.data.get("veg_rate") or 0)) or defaults.veg_rate
        nonveg_rate = Decimal(str(request.data.get("nonveg_rate") or 0)) or defaults.nonveg_rate
        e = Event.objects.create(
            space=space, title=request.data.get("title", "Event"),
            host=request.data.get("host", ""), contact=request.data.get("contact", ""),
            event_type=request.data.get("event_type", ""), event_date=event_date,
            start_time=request.data.get("start_time") or None,
            end_time=request.data.get("end_time") or None,
            covers=covers, package_amount=Decimal(str(request.data.get("package_amount", 0) or 0)),
            deposit=Decimal(str(request.data.get("deposit", 0) or 0)),
            food_covers=food_covers, food_pref=food_pref,
            food_veg=food_veg, food_nonveg=food_nonveg,
            veg_rate=veg_rate, nonveg_rate=nonveg_rate,
            status=Event.TENTATIVE,
        )
        log_action(request.user, "event_create", entity="Event", entity_id=e.id,
                   after={"space": space.name, "date": str(event_date)})
        e.refresh_from_db()  # coerce string time inputs to time objects for serialisation
        return Response(_event_dict(e), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """Adjust an enquiry before it's billed — space, date/time, covers,
        catering & pricing (BRD FR-BQT-002). Billed events are locked."""
        e = shared_or_visible(Event.objects.all(), request, field="space__location").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        if e.billed:
            return Response({"detail": "a billed event can't be edited"}, status=400)
        d = request.data

        if d.get("space"):
            space = shared_or_visible(FunctionSpace.objects.all(), request).filter(pk=d.get("space")).first()
            if not space:
                return Response({"detail": "function space required"}, status=400)
            e.space = space
        if d.get("event_date"):
            e.event_date = d.get("event_date")
        # Don't let an edit collide (by time) with another confirmed booking.
        new_start = _parse_time(d["start_time"]) if "start_time" in d else e.start_time
        new_end = _parse_time(d["end_time"]) if "end_time" in d else e.end_time
        clash = _find_clash(e.space, e.event_date, new_start, new_end, exclude_pk=e.pk)
        if clash:
            return Response({"detail": _clash_msg(e.space, clash)}, status=status.HTTP_409_CONFLICT)
        if "covers" in d:
            covers = int(d.get("covers") or 0)
            if covers > e.space.capacity:
                return Response({"detail": f"{e.space.name} seats {e.space.capacity}; {covers} requested"},
                                status=400)
            e.covers = covers

        for field in ("title", "host", "contact", "event_type"):
            if field in d:
                setattr(e, field, d.get(field) or "")
        if "start_time" in d:
            e.start_time = d.get("start_time") or None
        if "end_time" in d:
            e.end_time = d.get("end_time") or None
        if "package_amount" in d:
            e.package_amount = Decimal(str(d.get("package_amount") or 0))
        if "deposit" in d:
            e.deposit = Decimal(str(d.get("deposit") or 0))

        if any(k in d for k in ("food_pref", "food_veg", "food_nonveg")):
            food_pref = d.get("food_pref", e.food_pref)
            food_veg = int(d.get("food_veg", e.food_veg) or 0)
            food_nonveg = int(d.get("food_nonveg", e.food_nonveg) or 0)
            if food_pref == "veg":
                food_nonveg = 0
            elif food_pref == "nonveg":
                food_veg = 0
            e.food_pref, e.food_veg, e.food_nonveg = food_pref, food_veg, food_nonveg
            e.food_covers = food_veg + food_nonveg
        if "veg_rate" in d:
            e.veg_rate = Decimal(str(d.get("veg_rate") or 0))
        if "nonveg_rate" in d:
            e.nonveg_rate = Decimal(str(d.get("nonveg_rate") or 0))

        e.save()
        log_action(request.user, "event_update", entity="Event", entity_id=e.id,
                   after={"title": e.title, "date": str(e.event_date)})
        e.refresh_from_db()
        return Response(_event_dict(e))

    @action(detail=False, methods=["get", "post"], url_path="catering_prices")
    def catering_prices(self, request):
        """Standard per-plate catering prices (the master new events default to)."""
        rate = CateringRate.get_solo()
        if request.method == "POST":
            rate.veg_rate = Decimal(str(request.data.get("veg_rate") or 0))
            rate.nonveg_rate = Decimal(str(request.data.get("nonveg_rate") or 0))
            rate.save()
            log_action(request.user, "catering_rate_update", entity="CateringRate",
                       entity_id=rate.id, after={"veg": str(rate.veg_rate), "nonveg": str(rate.nonveg_rate)})
        return Response({"veg_rate": str(rate.veg_rate), "nonveg_rate": str(rate.nonveg_rate)})

    @action(detail=False, methods=["get"])
    def availability(self, request):
        """Which halls are free on a given date (FR-BQT-001)."""
        d = request.query_params.get("date")
        booked = set(Event.objects.filter(event_date=d, status=Event.CONFIRMED)
                     .values_list("space_id", flat=True)) if d else set()
        return Response([
            {"id": s.id, "name": s.name, "capacity": s.capacity, "available": s.id not in booked}
            for s in shared_or_visible(FunctionSpace.objects.all(), request)
        ])

    @action(detail=True, methods=["get"])
    def beo_pdf(self, request, pk=None):
        """Download the Banquet Event Order as a PDF (FR-BQT-004)."""
        from django.http import HttpResponse

        from apps.accounts.views import get_property
        from .beo_pdf import build_beo_pdf
        e = shared_or_visible(Event.objects.select_related("space"), request,
                              field="space__location").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        pdf = build_beo_pdf(e, get_property().name)
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        ref = e.beo_no or f"BEO-{e.id}"
        resp["Content-Disposition"] = f'attachment; filename="{ref}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        e = shared_or_visible(Event.objects.all(), request, field="space__location").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        # Can't confirm into a slot another confirmed event already holds — this
        # is the real guard, since two tentative enquiries may overlap freely.
        clash = _find_clash(e.space, e.event_date, e.start_time, e.end_time, exclude_pk=e.pk)
        if clash:
            return Response({"detail": _clash_msg(e.space, clash)}, status=status.HTTP_409_CONFLICT)
        e.status = Event.CONFIRMED
        fields = ["status"]
        if not e.beo_no:
            from apps.accounts.models import Property
            from apps.accounts.numbering import next_document_number
            prop = Property.objects.first()
            e.beo_no = next_document_number(Event, "beo_no", prop.beo_prefix if prop else "BEO")
            fields.append("beo_no")
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
        """Bill the event: hall/package + catering (plates × per-plate rate),
        18% GST on the subtotal, less the advance deposit = balance due."""
        e = shared_or_visible(Event.objects.all(), request, field="space__location").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        catering = e.catering_amount
        subtotal = e.bill_subtotal
        breakdown = tax.compute(subtotal, tax.BANQUET_RATE)
        deposit = Decimal(str(e.deposit))
        balance = (breakdown["total"] - deposit).quantize(Decimal("0.01"))
        e.billed = True
        e.status = Event.COMPLETED
        e.save(update_fields=["billed", "status"])
        log_action(request.user, "event_bill", entity="Event", entity_id=e.id,
                   after={"subtotal": str(subtotal), "total": str(breakdown["total"])})
        lines = [
            {"label": "Hall / package", "amount": str(e.package_amount)},
        ]
        if catering:
            if e.food_veg and e.veg_rate:
                lines.append({"label": f"Veg catering — {e.food_veg} × {e.veg_rate}",
                              "amount": str(e.food_veg * e.veg_rate)})
            if e.food_nonveg and e.nonveg_rate:
                lines.append({"label": f"Non-veg catering — {e.food_nonveg} × {e.nonveg_rate}",
                              "amount": str(e.food_nonveg * e.nonveg_rate)})
        return Response({
            "event": _event_dict(e),
            "lines": lines,
            "catering": str(catering),
            "subtotal": str(subtotal),
            "tax": {k: str(v) for k, v in breakdown.items()},
            "deposit": str(deposit),
            "balance": str(balance),
        })
