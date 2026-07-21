from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import HrViewSet, InvitePublicView, LeaveViewSet, SalaryAdvanceViewSet

router = DefaultRouter()
router.register("hr", HrViewSet, basename="hr")
router.register("leave", LeaveViewSet, basename="leave")
router.register("hr-advances", SalaryAdvanceViewSet, basename="hr-advances")

urlpatterns = router.urls + [
    path("public/invite/", InvitePublicView.as_view(), name="invite-public"),
]
