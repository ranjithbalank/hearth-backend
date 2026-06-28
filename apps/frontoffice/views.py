from datetime import date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import ModuleViewSetMixin
from apps.reservations.models import Reservation
from apps.rooms.models import Room

from . import services
from .models import Folio, NightAuditRun
from .serializers import FolioSerializer, NightAuditRunSerializer


class FolioViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "folio"
    queryset = Folio.objects.prefetch_related("lines", "settlements").select_related("room").all()
    serializer_class = FolioSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        room = self.request.query_params.get("room")
        if status_:
            qs = qs.filter(status=status_)
        if room:
            qs = qs.filter(room__number=room)
        return qs

    @action(detail=True, methods=["post"])
    def settle(self, request, pk=None):
        folio = self.get_object()
        payments = request.data.get("payments", [])
        if not payments:
            return Response({"detail": "payments required"}, status=400)
        services.settle_folio(folio, payments, user=request.user)
        return Response(FolioSerializer(folio).data)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        folio = self.get_object()
        try:
            services.check_out(folio, payments=request.data.get("payments"), user=request.user)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(FolioSerializer(folio).data)


class CheckInView(ModuleViewSetMixin, viewsets.ViewSet):
    module = "checkin"

    def create(self, request):
        resv_id = request.data.get("reservation")
        room_id = request.data.get("room")
        resv = Reservation.objects.filter(pk=resv_id).first()
        if not resv:
            return Response({"detail": "reservation not found"}, status=404)
        room = Room.objects.filter(pk=room_id).first() if room_id else None
        if room is None:
            room = Room.objects.filter(
                room_type=resv.room_type, status__in=Room.SELLABLE
            ).first()
        if room is None:
            return Response({"detail": "no sellable room available"}, status=400)
        folio = services.check_in(resv, room, user=request.user)
        # Capture KYC + guest-type from the multi-step wizard (BRD FR-PMS-004/012).
        id_type = request.data.get("id_type")
        guest_type = request.data.get("guest_type")
        if id_type or guest_type:
            from apps.accounts.models import log_action
            log_action(
                request.user, "kyc_capture", entity="Folio", entity_id=folio.id,
                after={"id_type": id_type, "id_number_present": bool(request.data.get("id_number")),
                       "guest_type": guest_type},
                note="Check-in KYC captured",
            )
            if guest_type == "corporate":
                folio.routing = "city_ledger"
                folio.save(update_fields=["routing"])
        return Response(FolioSerializer(folio).data, status=status.HTTP_201_CREATED)


class NightAuditView(ModuleViewSetMixin, viewsets.ViewSet):
    module = "accounting"

    def list(self, request):
        runs = NightAuditRun.objects.all()[:30]
        return Response(NightAuditRunSerializer(runs, many=True).data)

    def create(self, request):
        from apps.accounts.models import Property
        prop = Property.objects.first()
        biz = (prop.business_date if prop and prop.business_date else date.today())
        run = services.run_night_audit(biz, user=request.user)
        return Response(NightAuditRunSerializer(run).data, status=status.HTTP_201_CREATED)
