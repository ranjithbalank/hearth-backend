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

    @action(detail=True, methods=["get"])
    def invoice_pdf(self, request, pk=None):
        """Download the folio as a GST tax-invoice PDF (FR-TAX-003)."""
        from django.http import HttpResponse

        from apps.accounts.views import get_property
        from .invoice_pdf import build_invoice_pdf
        folio = self.get_object()
        prop = get_property()
        pdf = build_invoice_pdf(folio, prop.name, prop.gstin)
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        name = folio.invoice_no or f"folio-{folio.id}"
        resp["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def email_invoice(self, request, pk=None):
        """Send the invoice to the guest via the messaging adapter (FR-NOT-001)."""
        from apps.integrations import services as integ
        folio = self.get_object()
        guest = folio.reservation.guest if folio.reservation else None
        email = getattr(guest, "email", "") or ""
        mobile = getattr(guest, "mobile", "") or ""
        body = (f"{folio.guest_name}, your invoice {folio.invoice_no or '(pending)'} "
                f"total ₹{folio.charges_total}. Thank you for staying with us.")
        if email:
            integ.notify("email", email, body)
            return Response({"sent": True, "channel": "email", "to": email})
        if mobile:
            integ.notify("sms", mobile, body)
            return Response({"sent": True, "channel": "sms", "to": mobile})
        return Response({"detail": "No email or mobile on file for this guest"}, status=400)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        folio = self.get_object()
        # Always settle the full remaining balance with one tender — check-out may
        # post the stay's room charges first, so the client's pre-read balance is
        # stale. Tender comes from payments[0], an explicit `tender`, else Cash.
        pays = request.data.get("payments") or []
        tender = (pays[0].get("tender") if pays else None) or request.data.get("tender") or "Cash"
        try:
            services.check_out(folio, tender=tender, user=request.user)
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
        # Capture & store KYC + guest-type from the multi-step wizard (BRD FR-PMS-004/012).
        id_type = request.data.get("id_type", "")
        id_number = request.data.get("id_number", "")
        guest_type = request.data.get("guest_type", "")
        if id_type or guest_type or id_number:
            from apps.accounts.models import log_action
            folio.id_type = id_type
            folio.id_number = id_number
            folio.guest_type = guest_type
            if guest_type == "corporate":
                folio.routing = "city_ledger"
            folio.save(update_fields=["id_type", "id_number", "guest_type", "routing"])
            log_action(
                request.user, "kyc_capture", entity="Folio", entity_id=folio.id,
                after={"id_type": id_type, "id_number_present": bool(id_number),
                       "guest_type": guest_type},
                note="Check-in KYC captured",
            )
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
