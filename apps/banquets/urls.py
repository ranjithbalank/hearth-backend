from rest_framework.routers import DefaultRouter

from .views import BanquetViewSet

router = DefaultRouter()
router.register("banquets", BanquetViewSet, basename="banquet")

urlpatterns = router.urls
