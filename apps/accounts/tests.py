from django.test import TestCase
from rest_framework.test import APIClient

from .models import Branch, PasswordReset, User
from .constants import (
    ROLE_ADMIN,
    ROLE_CASHIER,
    ROLE_FRONT_OFFICE,
    ROLE_HOTEL_MGR,
    ROLE_HOUSEKEEPING,
    ROLE_MD,
    edition_entitlements,
    entitlement_allows,
    role_can_access,
)


class RbacTests(TestCase):
    def test_md_has_full_access(self):
        self.assertTrue(role_can_access(ROLE_MD, "settings"))
        self.assertTrue(role_can_access(ROLE_MD, "pos"))

    def test_cashier_scoped(self):
        self.assertTrue(role_can_access(ROLE_CASHIER, "pos"))
        self.assertFalse(role_can_access(ROLE_CASHIER, "folio"))

    def test_front_office_no_pos(self):
        self.assertTrue(role_can_access(ROLE_FRONT_OFFICE, "frontdesk"))
        self.assertFalse(role_can_access(ROLE_FRONT_OFFICE, "pos"))

    def test_front_office_owns_rooms_and_banquets(self):
        # The front desk is the single guest-facing desk for rooms AND banquets.
        self.assertTrue(role_can_access(ROLE_FRONT_OFFICE, "reservations"))
        self.assertTrue(role_can_access(ROLE_FRONT_OFFICE, "banquets"))
        self.assertTrue(role_can_access(ROLE_FRONT_OFFICE, "folio"))
        # Distribution/back-office stays with management.
        self.assertFalse(role_can_access(ROLE_FRONT_OFFICE, "channel"))
        self.assertFalse(role_can_access(ROLE_FRONT_OFFICE, "inventory"))

    def test_cashier_scoped_to_pos(self):
        self.assertTrue(role_can_access(ROLE_CASHIER, "pos"))
        self.assertFalse(role_can_access(ROLE_CASHIER, "inventory"))
        self.assertFalse(role_can_access(ROLE_CASHIER, "folio"))

    def test_kpi_dashboard_is_management_only(self):
        # The occupancy/ADR/RevPAR dashboard is Hotel Manager's, not the
        # front desk floor role — same split as Restaurant Manager vs.
        # Cashier/Captain on the F&B side.
        self.assertTrue(role_can_access(ROLE_HOTEL_MGR, "dashboard"))
        self.assertFalse(role_can_access(ROLE_FRONT_OFFICE, "dashboard"))
        self.assertFalse(role_can_access(ROLE_HOUSEKEEPING, "dashboard"))
        self.assertFalse(role_can_access(ROLE_CASHIER, "dashboard"))


class EntitlementTests(TestCase):
    def test_restaurant_edition_disables_hotel_modules(self):
        ent = edition_entitlements("restaurant")
        self.assertFalse(entitlement_allows(ent, "frontdesk"))   # hms
        self.assertTrue(entitlement_allows(ent, "pos"))          # restaurant

    def test_hotel_edition_disables_restaurant_modules(self):
        ent = edition_entitlements("hotel")
        self.assertTrue(entitlement_allows(ent, "frontdesk"))
        self.assertFalse(entitlement_allows(ent, "pos"))

    def test_both_enables_all(self):
        ent = edition_entitlements("both")
        self.assertTrue(entitlement_allows(ent, "frontdesk"))
        self.assertTrue(entitlement_allows(ent, "pos"))

    def test_shared_module_needs_no_entitlement(self):
        ent = edition_entitlements("restaurant")
        self.assertTrue(entitlement_allows(ent, "reports"))


class BranchImportTests(TestCase):
    """Bulk branch onboarding via CSV — template, per-row report, master-gated,
    and edition-derived entitlement flags (mirrors the manual Add-branch form)."""

    def _csv(self, body):
        from io import BytesIO
        f = BytesIO(body.encode())
        f.name = "branches.csv"
        return f

    def test_branch_import_creates_branches_and_reports_rows(self):
        admin = APIClient()
        admin.force_authenticate(User.objects.create_user(
            username="adminbrimp", password="Tk9$mZ2pQw!7", role=ROLE_ADMIN))
        r = admin.get("/api/auth/branches/import/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("name,code,city,state,gstin,edition,invoice_prefix", r.content.decode())

        from .views import get_property
        Branch.objects.create(property=get_property(), name="Existing", code="EXT")
        body = ("name,code,city,state,gstin,edition,invoice_prefix\n"
                "Existing Branch,EXT,Chennai,Tamil Nadu,,both,EXT-\n"     # duplicate code → skipped
                "Downtown,DTN,Chennai,Tamil Nadu,,restaurant,DTN-\n"
                "Bad Branch,,,,,both,\n")                                 # missing code → error
        r = admin.post("/api/auth/branches/import/", {"file": self._csv(body)}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["created"], 1)
        self.assertEqual(r.data["skipped_existing"], ["Existing Branch"])
        self.assertEqual(len(r.data["errors"]), 1)

        dtn = Branch.objects.get(code="DTN")
        self.assertEqual(dtn.edition, "restaurant")
        self.assertFalse(dtn.hms)
        self.assertTrue(dtn.restaurant)
        self.assertFalse(dtn.banquets)
        self.assertFalse(dtn.rms)

    def test_branch_import_is_master_gated(self):
        fo = APIClient()
        fo.force_authenticate(User.objects.create_user(
            username="fobrimp", password="Tk9$mZ2pQw!7", role=ROLE_FRONT_OFFICE))
        self.assertEqual(fo.get("/api/auth/branches/import/").status_code, 403)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="qa_reset_user", password="Original123!", email="qa_reset_user@hearth.example",
            role=ROLE_FRONT_OFFICE,
        )

    def test_request_does_not_reveal_whether_username_exists(self):
        r1 = self.client.post("/api/auth/password-reset/request/", {"username": "qa_reset_user"}, format="json")
        r2 = self.client.post("/api/auth/password-reset/request/", {"username": "no-such-user"}, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.data, r2.data)
        self.assertEqual(PasswordReset.objects.filter(user=self.user).count(), 1)
        self.assertEqual(PasswordReset.objects.count(), 1)  # nothing created for the fake username

    def test_token_is_single_use(self):
        self.client.post("/api/auth/password-reset/request/", {"username": "qa_reset_user"}, format="json")
        reset = PasswordReset.objects.get(user=self.user)
        r = self.client.post("/api/auth/password-reset/confirm/",
                              {"t": reset.token, "password": "BrandNewPass456!"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewPass456!"))

        r2 = self.client.get(f"/api/auth/password-reset/confirm/?t={reset.token}")
        self.assertEqual(r2.status_code, 404)
        r3 = self.client.post("/api/auth/password-reset/confirm/",
                               {"t": reset.token, "password": "AnotherPass789!"}, format="json")
        self.assertEqual(r3.status_code, 404)

    def test_expired_token_rejected(self):
        from datetime import timedelta

        from django.utils import timezone
        reset = PasswordReset.objects.create(
            user=self.user, token="expiredtoken1234", expires_at=timezone.now() - timedelta(minutes=1),
        )
        r = self.client.get(f"/api/auth/password-reset/confirm/?t={reset.token}")
        self.assertEqual(r.status_code, 404)

    def test_weak_password_rejected(self):
        self.client.post("/api/auth/password-reset/request/", {"username": "qa_reset_user"}, format="json")
        reset = PasswordReset.objects.get(user=self.user)
        r = self.client.post("/api/auth/password-reset/confirm/", {"t": reset.token, "password": "123"}, format="json")
        self.assertEqual(r.status_code, 400)
