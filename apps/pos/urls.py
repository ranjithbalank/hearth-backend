from rest_framework.routers import DefaultRouter

from .views import (
    CategoryViewSet,
    MenuItemViewSet,
    OrderViewSet,
    TableViewSet,
)

router = DefaultRouter()
router.register("pos/tables", TableViewSet, basename="table")
router.register("pos/categories", CategoryViewSet, basename="category")
router.register("pos/menu-items", MenuItemViewSet, basename="menuitem")
router.register("pos/orders", OrderViewSet, basename="order")

urlpatterns = router.urls
