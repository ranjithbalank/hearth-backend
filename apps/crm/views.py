from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin

from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(ModuleViewSetMixin, viewsets.ModelViewSet):
    module = "crm"
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ctype = self.request.query_params.get("type")
        mobile = self.request.query_params.get("mobile")
        if ctype:
            qs = qs.filter(customer_type=ctype)
        if mobile:
            qs = qs.filter(mobile=mobile)
        return qs

    @action(detail=False, methods=["post"])
    def campaign(self, request):
        """SMS/WhatsApp blast to a segment (FR-CRM parity with Petpooja campaigns).

        Consent-gated: only customers with marketing_consent and a mobile get it.
        Placeholders: {name}, {points}. Delivery via the pluggable messaging adapter.
        """
        from apps.integrations import services as integ

        from .models import Campaign
        segment = request.data.get("segment", "all")
        channel = request.data.get("channel", "sms")
        message = str(request.data.get("message", "")).strip()
        name = str(request.data.get("name", "")).strip() or f"Campaign {segment}"
        if not message:
            return Response({"detail": "a message is required"}, status=400)
        if channel not in ("sms", "whatsapp"):
            return Response({"detail": "channel must be sms or whatsapp"}, status=400)
        qs = Customer.objects.all()
        if segment == "guests":
            qs = qs.filter(customer_type=Customer.TYPE_GUEST)
        elif segment == "corporate":
            qs = qs.filter(customer_type=Customer.TYPE_CORPORATE)
        elif segment == "loyal":
            qs = qs.filter(loyalty_points__gt=0)
        sent = skipped = 0
        for c in qs:
            if not c.marketing_consent or not c.mobile:
                skipped += 1
                continue
            body = message.replace("{name}", c.name).replace("{points}", str(c.loyalty_points))
            integ.notify(channel, c.mobile, body)
            sent += 1
        camp = Campaign.objects.create(name=name, channel=channel, segment=segment,
                                       message=message, sent_count=sent, skipped_count=skipped,
                                       created_by=request.user.username)
        log_action(request.user, "crm_campaign", entity="Campaign", entity_id=camp.id,
                   after={"segment": segment, "sent": sent, "skipped": skipped})
        return Response({"id": camp.id, "sent": sent, "skipped": skipped}, status=201)

    @action(detail=False, methods=["get"])
    def campaigns(self, request):
        """Recent campaign history."""
        from .models import Campaign
        return Response([{
            "id": c.id, "name": c.name, "channel": c.channel, "segment": c.segment,
            "message": c.message, "sent_count": c.sent_count, "skipped_count": c.skipped_count,
            "created_by": c.created_by, "created_at": c.created_at,
        } for c in Campaign.objects.all()[:20]])

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Auto-fill a saved customer by mobile (FR-POS-009)."""
        mobile = request.query_params.get("mobile", "")
        cust = Customer.objects.filter(mobile=mobile).first()
        if not cust:
            return Response({"found": False})
        return Response({"found": True, "customer": CustomerSerializer(cust).data})

    @action(detail=True, methods=["post"])
    def settle_ar(self, request, pk=None):
        """Record a receipt against a company's city-ledger / AR balance (they pay
        on invoice after a BTC stay)."""
        cust = self.get_object()
        amount = Decimal(str(request.data.get("amount") or 0))
        if amount <= 0:
            return Response({"detail": "a positive amount is required"}, status=400)
        applied = min(amount, cust.outstanding)
        cust.outstanding = cust.outstanding - applied
        cust.save(update_fields=["outstanding"])
        log_action(request.user, "ar_receipt", entity="Customer", entity_id=cust.id,
                   after={"received": str(applied), "tender": request.data.get("tender", "Cash"),
                          "outstanding": str(cust.outstanding)})
        return Response({"received": str(applied), "outstanding": str(cust.outstanding)})

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """DPDP data-subject access request: full profile + linked activity (SR-054)."""
        cust = self.get_object()
        orders = list(cust.orders.values("id", "mode", "status", "created_at"))
        reservations = list(
            cust.reservations.values("id", "checkin_date", "checkout_date", "status")
        )
        # City-ledger (bill-to-company) folios billed to this corporate account —
        # the stays that make up its outstanding balance.
        city_ledger = [
            {"folio": f.id, "guest": f.guest_name, "invoice_no": f.invoice_no,
             "room": f.room.number if f.room else None,
             "settled_at": f.settled_at, "amount": str(f.charges_total)}
            for f in cust.city_ledger_folios.select_related("room").all()
        ]
        log_action(request.user, "dpdp_export", entity="Customer", entity_id=cust.id)
        return Response({
            "profile": CustomerSerializer(cust).data,
            "orders": orders,
            "reservations": reservations,
            "city_ledger": city_ledger,
        })

    @action(detail=True, methods=["post"])
    def erase(self, request, pk=None):
        """DPDP erasure: anonymise PII while preserving financial records (SR-053/054)."""
        cust = self.get_object()
        before = {"name": cust.name, "mobile": cust.mobile, "email": cust.email}
        cust.name = f"Erased Customer {cust.id}"
        cust.mobile = f"erased-{cust.id}"
        cust.email = ""
        cust.address = ""
        cust.locality = ""
        cust.gstin = ""
        cust.marketing_consent = False
        cust.tags = []
        cust.save()
        # Registration-card evidence (ID scan, signature) on this guest's
        # folios is PII too — erase it with the profile. The financial rows
        # (charges/settlements) stay, as SR-053 requires.
        from apps.frontoffice.models import Folio
        wiped = (Folio.objects.filter(reservation__guest=cust)
                 .exclude(id_scan="", signature="")
                 .update(id_scan="", signature=""))
        log_action(request.user, "dpdp_erase", entity="Customer", entity_id=cust.id,
                   before=before, after={"anonymised": True, "registration_scans_wiped": wiped})
        return Response({"erased": True, "customer": CustomerSerializer(cust).data})
