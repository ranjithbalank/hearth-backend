from rest_framework.routers import DefaultRouter

from .views import ChannelViewSet

router = DefaultRouter()
router.register("channel", ChannelViewSet, basename="channel")

urlpatterns = router.urls
