from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

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
