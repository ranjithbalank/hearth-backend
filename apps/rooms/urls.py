from rest_framework.routers import DefaultRouter

from .views import RatePlanViewSet, RoomTypeViewSet, RoomViewSet

router = DefaultRouter()
router.register("room-types", RoomTypeViewSet, basename="roomtype")
router.register("rate-plans", RatePlanViewSet, basename="rateplan")
router.register("rooms", RoomViewSet, basename="room")

urlpatterns = router.urls
