from rest_framework.routers import DefaultRouter

from .views import GroupBlockViewSet, ReservationViewSet

router = DefaultRouter()
router.register("reservations", ReservationViewSet, basename="reservation")
router.register("group-blocks", GroupBlockViewSet, basename="groupblock")

urlpatterns = router.urls
