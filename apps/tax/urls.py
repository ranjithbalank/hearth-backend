from rest_framework.routers import DefaultRouter

from .views import TaxViewSet

router = DefaultRouter()
router.register("tax", TaxViewSet, basename="tax")

urlpatterns = router.urls
