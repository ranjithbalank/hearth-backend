from datetime import date, timedelta

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin
from apps.reservations.models import Reservation
from apps.rooms.models import Room, RoomType


class BookingViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Direct (commission-free) booking engine drawing on the same inventory."""

    module = "booking"

    def list(self, request):
        out = []
        for rt in RoomType.objects.all():
            available = Room.objects.filter(room_type=rt, status__in=Room.SELLABLE).count()
            out.append({
                "room_type": rt.code, "name": rt.name,
                "rate": str(rt.base_rate), "available": available,
                "max_occupancy": rt.max_occupancy,
            })
        return Response(out)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        total = Reservation.objects.count() or 1
        direct = Reservation.objects.filter(
            source__in=[Reservation.SOURCE_BOOKING, Reservation.SOURCE_DIRECT]
        ).count()
        ota = Reservation.objects.filter(source=Reservation.SOURCE_OTA).count()
        # Commission saved vs an assumed 15% OTA commission on direct revenue.
        from decimal import Decimal
        direct_rev = sum(
            (r.rate for r in Reservation.objects.filter(source=Reservation.SOURCE_BOOKING)),
            start=Decimal("0"),
        )
        return Response({
            "direct_share_pct": round(direct / total * 100, 1),
            "ota_bookings": ota,
            "direct_bookings": direct,
            "commission_saved": str((direct_rev * Decimal("0.15")).quantize(Decimal("0.01"))),
        })

    def create(self, request):
        code = request.data.get("room_type")
        rt = RoomType.objects.filter(code=code).first()
        if not rt:
            return Response({"detail": "room_type not found"}, status=400)
        nights = int(request.data.get("nights", 1))
        resv = Reservation.objects.create(
            guest_name=request.data.get("guest_name", "Web Guest"),
            room_type=rt,
            checkin_date=date.today() + timedelta(days=int(request.data.get("in_days", 1))),
            checkout_date=date.today() + timedelta(days=int(request.data.get("in_days", 1)) + nights),
            nights=nights, rate=rt.base_rate,
            source=Reservation.SOURCE_BOOKING, status=Reservation.BOOKED,
            prepaid=bool(request.data.get("prepaid", True)),
            deposit=rt.base_rate if request.data.get("prepaid", True) else 0,
        )
        log_action(request.user, "direct_booking", entity="Reservation", entity_id=resv.id,
                   after={"room_type": code, "guest": resv.guest_name})
        return Response(
            {"id": resv.id, "guest_name": resv.guest_name, "room_type": code,
             "checkin_date": resv.checkin_date.isoformat()},
            status=status.HTTP_201_CREATED,
        )
