from django.urls import path

from .views import MessageLogView, OtpRequestView, OtpVerifyView

urlpatterns = [
    path("messages/", MessageLogView.as_view(), name="messages"),
    path("otp/request/", OtpRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OtpVerifyView.as_view(), name="otp-verify"),
]
