from rest_framework.routers import DefaultRouter

from .views import HrViewSet

router = DefaultRouter()
router.register("hr", HrViewSet, basename="hr")

urlpatterns = router.urls
