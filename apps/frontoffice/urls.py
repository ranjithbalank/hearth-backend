from rest_framework.routers import DefaultRouter

from .views import CheckInView, FolioViewSet, NightAuditView

router = DefaultRouter()
router.register("folios", FolioViewSet, basename="folio")
router.register("checkin", CheckInView, basename="checkin")
router.register("night-audit", NightAuditView, basename="nightaudit")

urlpatterns = router.urls
