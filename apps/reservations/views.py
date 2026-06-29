from datetime import date
from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.rooms.models import Room, RoomType

from .models import Reservation
from .serializers import ReservationSerializer

# Allowed oversell beyond physical inventory per room type (BRD FR-PMS-003).
OVERBOOKING_TOLERANCE = 0


class ReservationViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "reservations"
    queryset = Reservation.objects.select_related("room_type", "room", "rate_plan").all()
    serializer_class = ReservationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return qs

    @action(detail=False, methods=["get"])
    def arrivals(self, request):
        """Expected arrivals = booked reservations not yet checked in."""
        day = request.query_params.get("date")
        qs = self.get_queryset().filter(status=Reservation.BOOKED)
        if day:
            qs = qs.filter(checkin_date=day)
        return Response(ReservationSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def room_options(self, request, pk=None):
        """Sellable rooms matching the reservation's room type."""
        resv = self.get_object()
        rooms = Room.objects.filter(
            room_type=resv.room_type, status__in=Room.SELLABLE
        ).select_related("room_type")
        from apps.rooms.serializers import RoomSerializer
        return Response(RoomSerializer(rooms, many=True).data)

    @action(detail=False, methods=["get"])
    def availability(self, request):
        """Sellable count per room type for a window, honouring overbooking tolerance."""
        from datetime import datetime, timedelta
        ci = request.query_params.get("checkin") or date.today().isoformat()
        co = request.query_params.get("checkout") or ci
        ci_d = datetime.strptime(ci, "%Y-%m-%d").date()
        co_d = datetime.strptime(co, "%Y-%m-%d").date()
        if co_d <= ci_d:  # treat a single date as a one-night window
            co_d = ci_d + timedelta(days=1)
        out = []
        for rt in RoomType.objects.all():
            physical = Room.objects.filter(room_type=rt).exclude(status=Room.OOO).count()
            # Reservations overlapping [ci, co) that hold inventory.
            held = Reservation.objects.filter(
                room_type=rt, status__in=[Reservation.BOOKED, Reservation.IN_HOUSE],
                checkin_date__lt=co_d, checkout_date__gt=ci_d,
            ).count()
            available = physical - held + OVERBOOKING_TOLERANCE
            out.append({"room_type": rt.code, "name": rt.name,
                        "physical": physical, "held": held, "available": available})
        return Response(out)

    @action(detail=True, methods=["post"])
    def room_move(self, request, pk=None):
        """Move an in-house guest to another room (FR-PMS-007); folio + room status follow."""
        resv = self.get_object()
        dest = Room.objects.filter(pk=request.data.get("room")).first()
        if not dest:
            return Response({"detail": "destination room not found"}, status=400)
        old = resv.room
        resv.room = dest
        resv.save(update_fields=["room"])
        dest.status = Room.OCCUPIED
        dest.save(update_fields=["status", "updated_at"])
        if old and old.id != dest.id:
            old.status = Room.VACANT_DIRTY
            old.save(update_fields=["status", "updated_at"])
        folio = getattr(resv, "folio", None)
        if folio:
            folio.room = dest
            folio.save(update_fields=["room"])
        log_action(request.user, "room_move", entity="Reservation", entity_id=resv.id,
                   after={"from": old.number if old else None, "to": dest.number})
        return Response(ReservationSerializer(resv).data)

    @action(detail=True, methods=["post"])
    def no_show(self, request, pk=None):
        """Mark a no-show and post the configured penalty (FR-PMS-007)."""
        resv = self.get_object()
        resv.status = Reservation.NO_SHOW
        resv.save(update_fields=["status"])
        penalty = self._post_penalty(resv, request, default=resv.rate, reason="No-show penalty")
        log_action(request.user, "no_show", entity="Reservation", entity_id=resv.id,
                   after={"penalty": str(penalty)})
        return Response(ReservationSerializer(resv).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel a reservation with an optional cancellation charge."""
        resv = self.get_object()
        resv.status = Reservation.CANCELLED
        resv.save(update_fields=["status"])
        charge = Decimal(str(request.data.get("charge", 0)))
        if charge > 0:
            self._post_penalty(resv, request, default=charge, reason="Cancellation charge")
        log_action(request.user, "reservation_cancel", entity="Reservation", entity_id=resv.id)
        return Response(ReservationSerializer(resv).data)

    def _post_penalty(self, resv, request, default, reason):
        """Open (or reuse) a folio and post a penalty charge to the city ledger."""
        from apps.frontoffice.models import Folio, FolioLine
        from apps.frontoffice.services import post_charge
        from apps.tax import service as tax
        amount = Decimal(str(request.data.get("amount", default) or 0))
        if amount <= 0:
            return Decimal("0")
        folio = getattr(resv, "folio", None)
        if not folio:
            folio = Folio.objects.create(reservation=resv, guest_name=resv.guest_name,
                                         routing="city_ledger")
        post_charge(folio, kind=FolioLine.KIND_INCIDENTAL, description=reason,
                    amount=amount, gst_rate=tax.room_rate_for(amount),
                    source="penalty", user=request.user)
        return amount
