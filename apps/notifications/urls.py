from django.urls import path

from .views import ApprovalInboxView, NotificationView

urlpatterns = [
    path("notifications/", NotificationView.as_view(), name="notifications"),
    path("approvals/", ApprovalInboxView.as_view(), name="approvals"),
]
