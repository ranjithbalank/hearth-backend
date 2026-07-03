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

    # --- Segregation of duties across the full role set ---

    def test_front_office_blocked_from_back_office(self):
        self._as("Front Office")
        self.assertEqual(self.client.get("/api/reports/view/").status_code, 403)   # reports
        self.assertEqual(self.client.get("/api/reports/dayend/").status_code, 403)  # accounting
        self.assertEqual(self.client.get("/api/hr/").status_code, 403)              # hr
        self.assertEqual(self.client.get("/api/inventory/").status_code, 403)       # stores
        self.assertEqual(self.client.get("/api/folios/").status_code, 200)          # own desk

    def test_finance_books_only_no_floor(self):
        self._as("Finance")
        self.assertEqual(self.client.get("/api/reports/dayend/").status_code, 200)  # accounting
        self.assertEqual(self.client.get("/api/tax/").status_code, 200)
        self.assertEqual(self.client.get("/api/reports/view/").status_code, 200)
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 403)      # no POS
        self.assertEqual(self.client.get("/api/housekeeping/").status_code, 403)    # no floor

    def test_ceo_oversight_no_configuration(self):
        self._as("CEO")
        self.assertEqual(self.client.get("/api/reports/executive/").status_code, 200)
        self.assertEqual(self.client.get("/api/reports/view/").status_code, 200)
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 403)      # no floor ops
        self.assertEqual(self.client.get("/api/gst-master/").status_code, 403)      # no config

    def test_admin_configures_but_no_operations(self):
        self._as("Admin")
        self.assertEqual(self.client.get("/api/gst-master/").status_code, 200)      # config master
        self.assertEqual(self.client.get("/api/auth/roles/matrix/").status_code, 200)
        self.assertEqual(self.client.get("/api/folios/").status_code, 403)          # no guest money
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 403)      # no POS

    def test_chef_kitchen_scope(self):
        self._as("Chef / Kitchen")
        self.assertEqual(self.client.get("/api/recipes/").status_code, 200)
        self.assertEqual(self.client.get("/api/inventory/").status_code, 200)
        self.assertEqual(self.client.get("/api/material-requests/").status_code, 200)
        self.assertEqual(self.client.get("/api/purchase-orders/").status_code, 403)  # no purchasing
        self.assertEqual(self.client.get("/api/folios/").status_code, 403)

    def test_store_keeper_supply_chain_scope(self):
        self._as("Store Keeper")
        self.assertEqual(self.client.get("/api/inventory/").status_code, 200)
        self.assertEqual(self.client.get("/api/purchase-orders/").status_code, 200)
        self.assertEqual(self.client.get("/api/suppliers/").status_code, 200)
        self.assertEqual(self.client.get("/api/pos/orders/").status_code, 403)       # no sales
        self.assertEqual(self.client.get("/api/reports/dayend/").status_code, 403)   # no books

    def test_role_matrix_locked_to_roles_module(self):
        """Nobody can grant themselves access: floor roles can't even view the matrix."""
        self._as("Housekeeping")
        self.assertEqual(self.client.get("/api/auth/roles/matrix/").status_code, 403)
        r = self.client.post("/api/auth/roles/matrix/",
                             {"role": "Housekeeping", "module": "accounting", "allowed": True},
                             format="json")
        self.assertEqual(r.status_code, 403)

    def test_super_admin_full_access_and_protected(self):
        self._as("Super Admin")
        self.assertEqual(self.client.get("/api/folios/").status_code, 200)
        self.assertEqual(self.client.get("/api/reports/dayend/").status_code, 200)
        r = self.client.post("/api/auth/roles/matrix/",
                             {"role": "Super Admin", "module": "pos", "allowed": False},
                             format="json")
        self.assertEqual(r.status_code, 400)  # protected role can't be edited
