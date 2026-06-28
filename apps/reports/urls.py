from django.urls import path

from .views import CatalogueView, DashboardView, ExecutiveView, SalesSummaryView

urlpatterns = [
    path("reports/dashboard/", DashboardView.as_view(), name="reports-dashboard"),
    path("reports/executive/", ExecutiveView.as_view(), name="reports-executive"),
    path("reports/sales-summary/", SalesSummaryView.as_view(), name="reports-sales"),
    path("reports/catalogue/", CatalogueView.as_view(), name="reports-catalogue"),
]
