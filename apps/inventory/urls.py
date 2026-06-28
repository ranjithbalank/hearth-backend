from rest_framework.routers import DefaultRouter

from .views import IngredientViewSet

router = DefaultRouter()
router.register("inventory", IngredientViewSet, basename="ingredient")

urlpatterns = router.urls
