from datetime import date

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin
from apps.rooms.models import Room

from .models import Reservation
from .serializers import ReservationSerializer


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
