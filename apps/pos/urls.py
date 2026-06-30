from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    CategoryViewSet,
    KdsViewSet,
    MenuItemViewSet,
    OrderViewSet,
    QrOrderView,
    TableViewSet,
)

router = DefaultRouter()
router.register("pos/tables", TableViewSet, basename="table")
router.register("pos/categories", CategoryViewSet, basename="category")
router.register("pos/menu-items", MenuItemViewSet, basename="menuitem")
router.register("pos/orders", OrderViewSet, basename="order")
router.register("kds", KdsViewSet, basename="kds")

urlpatterns = router.urls + [
    path("pos/qr-order/", QrOrderView.as_view(), name="qr-order"),
]
