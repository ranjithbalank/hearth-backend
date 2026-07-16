from rest_framework.routers import DefaultRouter

from .views import DepartmentViewSet, DesignationViewSet, PaymentMethodViewSet

router = DefaultRouter()
router.register("masters/departments", DepartmentViewSet, basename="department")
router.register("masters/designations", DesignationViewSet, basename="designation")
router.register("masters/payment-methods", PaymentMethodViewSet, basename="payment-method")

urlpatterns = router.urls
