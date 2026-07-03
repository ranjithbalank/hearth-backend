from rest_framework.routers import DefaultRouter

from .views import IngredientCategoryViewSet, IngredientViewSet, UomViewSet

router = DefaultRouter()
router.register("inventory-categories", IngredientCategoryViewSet, basename="ingredient-category")
router.register("inventory-uoms", UomViewSet, basename="uom")
router.register("inventory", IngredientViewSet, basename="ingredient")

urlpatterns = router.urls
