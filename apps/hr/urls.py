from rest_framework.routers import DefaultRouter

from .views import HrViewSet, LeaveViewSet, SalaryAdvanceViewSet

router = DefaultRouter()
router.register("hr", HrViewSet, basename="hr")
router.register("leave", LeaveViewSet, basename="leave")
router.register("hr-advances", SalaryAdvanceViewSet, basename="hr-advances")

urlpatterns = router.urls
