from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    CategoryViewSet,
    FeedbackPublicView,
    FeedbackViewSet,
    KdsViewSet,
    MenuItemViewSet,
    OrderStatusPublicView,
    OrderViewSet,
    QrOrderView,
    ReconViewSet,
    TableReservationViewSet,
    TableViewSet,
    TillViewSet,
)

router = DefaultRouter()
router.register("pos/tables", TableViewSet, basename="table")
router.register("pos/categories", CategoryViewSet, basename="category")
router.register("pos/menu-items", MenuItemViewSet, basename="menuitem")
router.register("pos/orders", OrderViewSet, basename="order")
router.register("pos/till", TillViewSet, basename="till")
router.register("pos/reconciliation", ReconViewSet, basename="recon")
router.register("pos/table-reservations", TableReservationViewSet, basename="tablereservation")
router.register("crm/feedback", FeedbackViewSet, basename="feedback")
router.register("kds", KdsViewSet, basename="kds")

urlpatterns = router.urls + [
    path("pos/qr-order/", QrOrderView.as_view(), name="qr-order"),
    path("public/feedback/", FeedbackPublicView.as_view(), name="feedback-public"),
    path("public/order-status/", OrderStatusPublicView.as_view(), name="order-status-public"),
]
