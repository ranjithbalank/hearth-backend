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
        log_action(request.user, "dpdp_export", entity="Customer", entity_id=cust.id)
        return Response({
            "profile": CustomerSerializer(cust).data,
            "orders": orders,
            "reservations": reservations,
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
        log_action(request.user, "dpdp_erase", entity="Customer", entity_id=cust.id,
                   before=before, after={"anonymised": True})
        return Response({"erased": True, "customer": CustomerSerializer(cust).data})
