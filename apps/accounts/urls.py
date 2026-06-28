from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EntitlementView,
    HearthTokenView,
    MeView,
    PropertyView,
    SetupView,
    UserViewSet,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")

urlpatterns = [
    path("token/", HearthTokenView.as_view(), name="token_obtain_pair"),
    path("me/", MeView.as_view(), name="me"),
    path("property/", PropertyView.as_view(), name="property"),
    path("setup/", SetupView.as_view(), name="setup"),
    path("entitlements/", EntitlementView.as_view(), name="entitlements"),
    path("", include(router.urls)),
]
