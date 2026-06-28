from rest_framework.routers import DefaultRouter

from .views import RevenueViewSet

router = DefaultRouter()
router.register("revenue", RevenueViewSet, basename="revenue")

urlpatterns = router.urls
