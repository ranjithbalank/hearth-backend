from rest_framework.routers import DefaultRouter

from .views import (
    ChecklistItemViewSet, HousekeepingTaskViewSet, HousekeepingViewSet, LinenItemViewSet, WorkOrderViewSet,
)

router = DefaultRouter()
# Registered before "housekeeping" so these nested-looking prefixes never
# collide with HousekeepingViewSet's own detail routes.
router.register("housekeeping/checklist-items", ChecklistItemViewSet, basename="hk-checklist-item")
router.register("housekeeping/linen-items", LinenItemViewSet, basename="hk-linen-item")
router.register("housekeeping/tasks", HousekeepingTaskViewSet, basename="housekeeping-task")
router.register("housekeeping", HousekeepingViewSet, basename="housekeeping")
router.register("work-orders", WorkOrderViewSet, basename="workorder")

urlpatterns = router.urls
