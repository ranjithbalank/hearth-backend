from rest_framework.routers import DefaultRouter

from .views import (
    GoodsReceiptViewSet,
    PurchaseOrderViewSet,
    SupplierViewSet,
    VendorViewSet,
)

router = DefaultRouter()
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("vendors", VendorViewSet, basename="vendor")
router.register("purchase-orders", PurchaseOrderViewSet, basename="po")
router.register("goods-receipts", GoodsReceiptViewSet, basename="grn")

urlpatterns = router.urls
