"""End-to-end RBAC: the deny-by-default gating actually returns 403 over HTTP."""
from django.test import TestCase
from rest_framework.test import APIClient

from .models import User


class RbacApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _as(self, role):
        u = User.objects.create_user(username=f"u{role}".replace(" ", ""),
                                     password="Tk9$mZ2pQw!7", role=role)
        self.client.force_authenticate(u)

    def test_cashier_blocked_from_hotel_modules(self):
        self._as("F&B Cashier")
        self.assertEqual(self.client.get("/api/folios/").status_code, 403)       # hms/folio
        self.assertEqual(self.client.get("/api/work-orders/").status_code, 403)  # engineering
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 200)   # allowed

    def test_housekeeping_scope(self):
        self._as("Housekeeping")
        self.assertEqual(self.client.get("/api/housekeeping/").status_code, 200)
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 403)
        self.assertEqual(self.client.get("/api/revenue/").status_code, 403)

    def test_unauthenticated_denied(self):
        c = APIClient()
        self.assertEqual(c.get("/api/folios/").status_code, 401)
