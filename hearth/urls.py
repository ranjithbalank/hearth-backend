from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView


def health(_request):
    return JsonResponse({"service": "hearth", "status": "ok"})


api_patterns = [
    path("health/", health, name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("auth/", include("apps.accounts.urls")),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("", include("apps.rooms.urls")),
    path("", include("apps.reservations.urls")),
    path("", include("apps.frontoffice.urls")),
    path("", include("apps.housekeeping.urls")),
    path("", include("apps.pos.urls")),
    path("", include("apps.tax.urls")),
    path("", include("apps.crm.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.revenue.urls")),
    path("", include("apps.channel.urls")),
    path("", include("apps.booking.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.recipes.urls")),
    path("", include("apps.procurement.urls")),
    path("", include("apps.banquets.urls")),
    path("", include("apps.hr.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.integrations.urls")),
    path("", include("apps.matreq.urls")),
    path("", include("apps.masters.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]
