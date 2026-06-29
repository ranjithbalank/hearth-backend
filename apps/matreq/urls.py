from rest_framework.routers import DefaultRouter

from .views import MaterialRequestViewSet

router = DefaultRouter()
router.register("material-requests", MaterialRequestViewSet, basename="matreq")

urlpatterns = router.urls
