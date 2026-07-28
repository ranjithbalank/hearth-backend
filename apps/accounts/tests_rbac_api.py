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


class FeatureToggleTests(TestCase):
    """The per-feature configuration layer: the dependency engine + that a
    disabled feature is enforced over HTTP (not merely hidden in the UI)."""

    def setUp(self):
        self.client = APIClient()

    def test_dependency_engine(self):
        from .features import apply_toggle, dependents, resolve
        ent = {"hms": True, "restaurant": True, "banquets": True, "rms": True}
        base = resolve(ent, {})
        self.assertTrue(base["housekeeping"])
        # Housekeeping turns off independently — the hotel core stays on.
        cfg = apply_toggle({}, "housekeeping", False, ent)
        r = resolve(ent, cfg)
        self.assertFalse(r["housekeeping"])
        self.assertTrue(r["livegrid"])
        self.assertTrue(r["frontdesk"])
        self.assertEqual(dependents("housekeeping"), set())
        # Inventory cascades its dependents off.
        self.assertIn("recipes", dependents("inventory"))
        r2 = resolve(ent, apply_toggle({}, "inventory", False, ent))
        self.assertFalse(r2["recipes"])
        self.assertFalse(r2["procurement"])
        self.assertTrue(r2["pos"])
        # Enabling a child pulls its prerequisites on.
        cfg3 = apply_toggle({"suppliers": False, "procurement": False}, "procurement", True, ent)
        self.assertTrue(cfg3["suppliers"])
        self.assertTrue(cfg3["inventory"])
        # Hotel edition hides restaurant features regardless of toggle.
        rh = resolve({"hms": True, "restaurant": False, "banquets": True, "rms": True}, {})
        self.assertFalse(rh["pos"])
        self.assertTrue(rh["housekeeping"])

    def test_disabled_feature_403s_over_http(self):
        from .models import Entitlement, Property
        prop = Property.objects.create(name="T", edition="both", setup_done=True)
        Entitlement.objects.create(property=prop, features={"housekeeping": False})
        u = User.objects.create_user(username="gmfeat", password="Tk9$mZ2pQw!7",
                                     role="General Manager")
        self.client.force_authenticate(u)
        # Housekeeping is off -> 403 even for a full-access role.
        self.assertEqual(self.client.get("/api/housekeeping/").status_code, 403)
        # The hotel core it depends on is untouched.
        self.assertEqual(self.client.get("/api/rooms/").status_code, 200)

    def test_feature_toggle_endpoint_cascades(self):
        from .models import Entitlement, Property
        prop = Property.objects.create(name="T2", edition="both", setup_done=True)
        Entitlement.objects.create(property=prop)
        u = User.objects.create_user(username="adminfeat", password="Tk9$mZ2pQw!7",
                                     role="Super Admin")
        self.client.force_authenticate(u)
        r = self.client.patch("/api/auth/entitlements/",
                              {"feature": "inventory", "enabled": False}, format="json")
        self.assertEqual(r.status_code, 200)
        eff = r.data["entitlement"]["features_effective"]
        self.assertFalse(eff["inventory"])
        self.assertFalse(eff["recipes"])       # cascaded
        self.assertFalse(eff["procurement"])   # cascaded
        self.assertTrue(eff["pos"])            # untouched
