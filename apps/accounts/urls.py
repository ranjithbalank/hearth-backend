from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AuditLogView,
    BranchViewSet,
    EntitlementView,
    HearthTokenView,
    MeView,
    MfaDisableView,
    MfaSetupView,
    MfaVerifyView,
    PropertyView,
    RoleMatrixView,
    SetupView,
    UserBranchAccessViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("branches", BranchViewSet, basename="branch")
router.register("branch-access", UserBranchAccessViewSet, basename="branch-access")

urlpatterns = [
    path("token/", HearthTokenView.as_view(), name="token_obtain_pair"),
    path("me/", MeView.as_view(), name="me"),
    path("property/", PropertyView.as_view(), name="property"),
    path("setup/", SetupView.as_view(), name="setup"),
    path("entitlements/", EntitlementView.as_view(), name="entitlements"),
    path("roles/matrix/", RoleMatrixView.as_view(), name="roles-matrix"),
    path("audit/", AuditLogView.as_view(), name="audit-log"),
    path("mfa/setup/", MfaSetupView.as_view(), name="mfa-setup"),
    path("mfa/verify/", MfaVerifyView.as_view(), name="mfa-verify"),
    path("mfa/disable/", MfaDisableView.as_view(), name="mfa-disable"),
    path("", include(router.urls)),
]
