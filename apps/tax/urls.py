from rest_framework.routers import DefaultRouter

from .gst_master_views import GstMasterViewSet
from .views import TaxViewSet

router = DefaultRouter()
router.register("tax", TaxViewSet, basename="tax")
router.register("gst-master", GstMasterViewSet, basename="gstmaster")

urlpatterns = router.urls
