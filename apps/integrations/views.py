from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import log_action
from apps.accounts.permissions import ModulePermission

from . import services
from .models import SentMessage


class ModuleAPIView(APIView):
    permission_classes = [IsAuthenticated, ModulePermission]
    module = None


class MessageLogView(ModuleAPIView):
    module = "notifications"

    def get(self, request):
        rows = [
            {"id": m.id, "channel": m.channel, "to": m.to, "body": m.body,
             "status": m.status, "created_at": m.created_at}
            for m in SentMessage.objects.all()[:50]
        ]
        return Response(rows)


class OtpRequestView(ModuleAPIView):
    module = "crm"

    def post(self, request):
        mobile = request.data.get("mobile", "")
        if not mobile:
            return Response({"detail": "mobile required"}, status=400)
        services.send_otp(mobile)
        log_action(request.user, "otp_request", entity="Otp", entity_id=mobile)
        return Response({"sent": True})


class OtpVerifyView(ModuleAPIView):
    module = "crm"

    def post(self, request):
        ok = services.verify_otp(request.data.get("mobile", ""), request.data.get("code", ""))
        return Response({"verified": ok}, status=200 if ok else 400)
