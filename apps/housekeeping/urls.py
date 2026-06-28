from rest_framework.routers import DefaultRouter

from .views import HousekeepingViewSet, WorkOrderViewSet

router = DefaultRouter()
router.register("housekeeping", HousekeepingViewSet, basename="housekeeping")
router.register("work-orders", WorkOrderViewSet, basename="workorder")

urlpatterns = router.urls
