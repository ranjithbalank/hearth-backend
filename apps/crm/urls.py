from rest_framework.routers import DefaultRouter

from .views import CustomerViewSet, LoyaltyRewardViewSet, LoyaltyTierViewSet

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("crm/loyalty-tiers", LoyaltyTierViewSet, basename="loyalty-tier")
router.register("crm/loyalty-rewards", LoyaltyRewardViewSet, basename="loyalty-reward")

urlpatterns = router.urls
