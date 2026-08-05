"""TEMPORARY probe test - hotel-only entitlement vs cross-cutting roles.

DELETE AFTER RUNNING. Uses the Django test DB only.
"""
import json

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Entitlement, Property, User


class HotelOnlyDashboardProbe(TestCase):
    def setUp(self):
        prop = Property.objects.create(name="Hotel Only Probe", edition="hotel",
                                       setup_done=True)
        Entitlement.objects.create(property=prop, hms=True, restaurant=False,
                                   banquets=False, rms=False)
        from apps.accounts.permissions import active_entitlements
        print("\n### ACTIVE ENTITLEMENTS ###")
        print(json.dumps(active_entitlements(), indent=2))

    def _client(self, username, role):
        c = APIClient()
        c.force_authenticate(User.objects.create_user(
            username=username, password="Tk9$mZ2pQw!7", role=role))
        return c

    def _dump(self, label, resp):
        print(f"\n### {label} ###")
        print("HTTP", resp.status_code)
        try:
            print(json.dumps(resp.json(), indent=2, sort_keys=False))
        except Exception:
            print(repr(resp.content[:2000]))

    def test_probe(self):
        gm = self._client("probe_gm", "General Manager")
        admin = self._client("probe_admin", "Admin")
        hm = self._client("probe_hm", "Hotel Manager")

        self._dump("GM /api/reports/dashboard/", gm.get("/api/reports/dashboard/"))
        self._dump("ADMIN /api/reports/dashboard/", admin.get("/api/reports/dashboard/"))
        self._dump("HOTEL MANAGER /api/reports/dashboard/", hm.get("/api/reports/dashboard/"))

        self._dump("GM /api/recipes/pending_dishes/",
                   gm.get("/api/recipes/pending_dishes/"))
        self._dump("GM /api/reports/executive/", gm.get("/api/reports/executive/"))
        self._dump("GM /api/reports/revenue-trend/", gm.get("/api/reports/revenue-trend/"))

        # What the nav/module layer tells the frontend it may open.
        self._dump("GM /api/auth/me/", gm.get("/api/auth/me/"))
