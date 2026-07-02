from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import GroupBlockViewSet, PreCheckinPublicView, ReservationViewSet

router = DefaultRouter()
router.register("reservations", ReservationViewSet, basename="reservation")
router.register("group-blocks", GroupBlockViewSet, basename="groupblock")

urlpatterns = router.urls + [
    path("public/pre-checkin/", PreCheckinPublicView.as_view(), name="pre-checkin-public"),
]
