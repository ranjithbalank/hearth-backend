from django.test import TestCase
from rest_framework.test import APIClient

from .models import Branch, PasswordReset, Role, User
from .constants import (
    ALL_MODULES,
    PO_APPROVER_ROLES,
    ROLE_ADMIN,
    ROLE_CASHIER,
    ROLE_FRONT_OFFICE,
    ROLE_GM,
    ROLE_HOTEL_MGR,
    ROLE_HOUSEKEEPING,
    ROLE_HR,
    ROLE_MD,
    ROLE_CHOICES,
    ROLE_FINANCE,
    ROLE_SUPER_ADMIN,
    edition_entitlements,
    entitlement_allows,
    role_can_access,
)
from .rbac import assignable_roles, base_role, can_access, can_assign_role, role_names_for


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


class RoleAssignmentTests(TestCase):
    """The seniority ladder behind Users & Roles: who may hand out what."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="x", role=ROLE_SUPER_ADMIN, is_superuser=True)
        self.admin = User.objects.create_user(username="adm", password="x", role=ROLE_ADMIN)
        self.hr = User.objects.create_user(username="hrm", password="x", role=ROLE_HR)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    # --- the rule itself ---

    def test_you_may_grant_your_own_tier_and_below(self):
        self.assertTrue(can_assign_role(ROLE_SUPER_ADMIN, ROLE_SUPER_ADMIN))
        self.assertTrue(can_assign_role(ROLE_MD, ROLE_GM))          # same tier
        self.assertTrue(can_assign_role(ROLE_ADMIN, ROLE_CASHIER))  # below

    def test_you_may_never_grant_above_yourself(self):
        self.assertFalse(can_assign_role(ROLE_ADMIN, ROLE_SUPER_ADMIN))
        self.assertFalse(can_assign_role(ROLE_ADMIN, ROLE_GM))
        self.assertFalse(can_assign_role(ROLE_HR, ROLE_MD))
        self.assertFalse(can_assign_role(ROLE_CASHIER, ROLE_ADMIN))

    def test_retired_roles_are_never_grantable(self):
        # Old accounts may still carry these; nobody can create a new one.
        self.assertFalse(can_assign_role(ROLE_SUPER_ADMIN, "Night Auditor"))

    def test_picker_offers_only_what_you_may_grant(self):
        self.assertIn(ROLE_SUPER_ADMIN, assignable_roles(ROLE_SUPER_ADMIN))
        offered = assignable_roles(ROLE_ADMIN)
        self.assertIn(ROLE_CASHIER, offered)
        self.assertIn(ROLE_HR, offered)
        self.assertNotIn(ROLE_SUPER_ADMIN, offered)
        self.assertNotIn(ROLE_MD, offered)

    # --- enforced over the API ---

    def test_admin_cannot_create_a_super_admin(self):
        r = self.api(self.admin).post("/api/auth/users/", {
            "username": "sneaky", "role": ROLE_SUPER_ADMIN, "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_admin_can_create_the_roles_below_them(self):
        r = self.api(self.admin).post("/api/auth/users/", {
            "username": "newcashier", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 201)

    def test_hr_can_create_users(self):
        # HR staffs the property up — that's the whole point of the "users"
        # module being split out of "settings".
        r = self.api(self.hr).post("/api/auth/users/", {
            "username": "newchef", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 201)

    def test_floor_roles_cannot_reach_the_screen_at_all(self):
        captain = User.objects.create_user(username="cap", password="x", role=ROLE_CASHIER)
        r = self.api(captain).get("/api/auth/users/")
        self.assertEqual(r.status_code, 403)

    def test_nobody_promotes_themselves(self):
        r = self.api(self.admin).patch(f"/api/auth/users/{self.admin.id}/",
                                       {"role": ROLE_SUPER_ADMIN})
        self.assertEqual(r.status_code, 403)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, ROLE_ADMIN)

    def test_you_cannot_touch_an_account_above_your_level(self):
        r = self.api(self.admin).patch(f"/api/auth/users/{self.owner.id}/",
                                       {"is_active": False})
        self.assertEqual(r.status_code, 403)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_the_last_owner_cannot_be_removed(self):
        r = self.api(self.owner).patch(f"/api/auth/users/{self.owner.id}/", {"is_active": False})
        self.assertEqual(r.status_code, 403)
        r = self.api(self.owner).delete(f"/api/auth/users/{self.owner.id}/")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_a_second_owner_frees_the_first(self):
        second = User.objects.create_user(
            username="owner2", password="x", role=ROLE_SUPER_ADMIN)
        r = self.api(second).patch(f"/api/auth/users/{self.owner.id}/", {"is_active": False})
        self.assertEqual(r.status_code, 200)

    def test_role_options_endpoint_carries_the_mapping(self):
        r = self.api(self.hr).get("/api/auth/users/assignable-roles/")
        self.assertEqual(r.status_code, 200)
        by_role = {row["role"]: row for row in r.json()}
        self.assertNotIn(ROLE_SUPER_ADMIN, by_role)
        self.assertIn("pos", by_role[ROLE_CASHIER]["modules"])

    def test_matrix_cannot_be_used_to_widen_your_own_role(self):
        r = self.api(self.admin).post("/api/auth/roles/matrix/",
                                      {"role": ROLE_ADMIN, "module": "pos", "allowed": True})
        self.assertEqual(r.status_code, 403)

    def test_matrix_cannot_grant_what_you_do_not_hold(self):
        r = self.api(self.admin).post("/api/auth/roles/matrix/",
                                      {"role": ROLE_CASHIER, "module": "accounting", "allowed": True})
        self.assertEqual(r.status_code, 403)


class RoleMasterTests(TestCase):
    """Roles as data: the property can invent one, and it behaves as its base."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="x", role=ROLE_SUPER_ADMIN, is_superuser=True)
        self.admin = User.objects.create_user(username="adm", password="x", role=ROLE_ADMIN)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_the_builtins_are_seeded(self):
        self.assertEqual(Role.objects.filter(is_system=True).count(), len(ROLE_CHOICES))
        self.assertEqual(Role.objects.get(name=ROLE_SUPER_ADMIN).modules, "*")
        self.assertEqual(Role.objects.get(name=ROLE_CASHIER).rank, 1)

    def test_every_screen_can_be_mapped(self):
        # approvals/kds/online were granted in ROLE_ALLOW but missing from
        # ALL_MODULES, so Role Mapping had no row for them.
        for module in ("approvals", "kds", "online"):
            self.assertIn(module, ALL_MODULES)

    def test_owner_creates_a_custom_role(self):
        r = self.api(self.owner).post("/api/auth/roles/", {
            "name": "Night Manager", "base_role": ROLE_FRONT_OFFICE, "rank": 2,
            "modules": ["frontdesk", "folio", "reports"]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        role = Role.objects.get(name="Night Manager")
        self.assertFalse(role.is_system)
        self.assertEqual(role.behaves_as, ROLE_FRONT_OFFICE)

    def test_a_custom_role_is_assignable_and_carries_its_own_screens(self):
        Role.objects.create(name="Night Manager", base_role=ROLE_FRONT_OFFICE, rank=1,
                            modules=["frontdesk", "folio"])
        r = self.api(self.owner).post("/api/auth/users/", {
            "username": "nm", "role": "Night Manager", "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 201, r.data)
        night = User.objects.get(username="nm")
        self.assertTrue(can_access(night.role, "frontdesk"))
        self.assertFalse(can_access(night.role, "pos"))

    def test_a_custom_role_inherits_its_base_behaviour(self):
        # Front Office may not approve a PO; a role based on it may not either,
        # whatever screens it was given.
        Role.objects.create(name="Night Manager", base_role=ROLE_FRONT_OFFICE, rank=2,
                            modules=list(ALL_MODULES))
        self.assertEqual(base_role("Night Manager"), ROLE_FRONT_OFFICE)
        self.assertNotIn(base_role("Night Manager"), PO_APPROVER_ROLES)
        # …and one based on Finance may.
        Role.objects.create(name="Night Accountant", base_role=ROLE_FINANCE, rank=2, modules=[])
        self.assertIn(base_role("Night Accountant"), PO_APPROVER_ROLES)

    def test_a_role_cannot_out_rank_its_creator(self):
        r = self.api(self.admin).post("/api/auth/roles/", {
            "name": "Deputy Owner", "base_role": ROLE_FRONT_OFFICE, "rank": 4,
            "modules": []}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_you_cannot_grant_screens_you_do_not_hold(self):
        r = self.api(self.admin).post("/api/auth/roles/", {
            "name": "Shadow Cashier", "base_role": ROLE_CASHIER, "rank": 1,
            "modules": ["pos", "accounting"]}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("pos", r.data["detail"])

    def test_a_builtin_cannot_be_renamed_or_deleted(self):
        cashier = Role.objects.get(name=ROLE_CASHIER)
        r = self.api(self.owner).patch(f"/api/auth/roles/{cashier.id}/",
                                       {"name": "Till Operator"}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self.api(self.owner).delete(f"/api/auth/roles/{cashier.id}/")
        self.assertEqual(r.status_code, 403)

    def test_a_builtins_screens_stay_editable(self):
        cashier = Role.objects.get(name=ROLE_CASHIER)
        r = self.api(self.owner).patch(f"/api/auth/roles/{cashier.id}/",
                                       {"modules": ["pos", "kds"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(can_access(ROLE_CASHIER, "matreq"))
        self.assertTrue(can_access(ROLE_CASHIER, "kds"))

    def test_a_role_in_use_cannot_be_deleted(self):
        role = Role.objects.create(name="Night Manager", base_role=ROLE_FRONT_OFFICE,
                                   rank=1, modules=["frontdesk"])
        User.objects.create_user(username="nm", password="x", role="Night Manager")
        r = self.api(self.owner).delete(f"/api/auth/roles/{role.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("still hold", r.data["detail"])

    def test_a_retired_role_cannot_be_assigned(self):
        Role.objects.create(name="Night Auditor", rank=0, modules=[], active=False)
        r = self.api(self.owner).post("/api/auth/users/", {
            "username": "na", "role": "Night Auditor", "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 403)   # refused by the ladder, before validation
        self.assertFalse(User.objects.filter(username="na").exists())

    def test_an_unknown_role_is_rejected(self):
        r = self.api(self.owner).post("/api/auth/users/", {
            "username": "ghost", "role": "Wizard", "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 403)

    def test_custom_roles_show_up_in_the_picker(self):
        Role.objects.create(name="Night Manager", base_role=ROLE_FRONT_OFFICE, rank=1,
                            modules=["frontdesk"])
        rows = self.api(self.admin).get("/api/auth/users/assignable-roles/").json()
        night = next(r for r in rows if r["role"] == "Night Manager")
        self.assertTrue(night["custom"])
        self.assertEqual(night["behaves_as"], ROLE_FRONT_OFFICE)
        self.assertEqual(night["modules"], ["frontdesk"])

    def test_finding_people_by_role_includes_custom_ones(self):
        Role.objects.create(name="Floor Supervisor", base_role=ROLE_HOUSEKEEPING, rank=1,
                            modules=["housekeeping"])
        self.assertIn("Floor Supervisor", role_names_for(ROLE_HOUSEKEEPING))
        self.assertIn(ROLE_HOUSEKEEPING, role_names_for(ROLE_HOUSEKEEPING))


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


class LicenceBoundaryTests(TestCase):
    """The commercial line: the edition is sold, not chosen.

    Both endpoints under test used to let the customer grant themselves modules
    they had not bought — /auth/setup/ was AllowAny with no completion guard,
    and /auth/entitlements/ passed hms/restaurant/banquets/rms straight through
    its serializer for any settings-capable role.
    """

    def setUp(self):
        from .models import Entitlement, Property

        self.prop = Property.objects.create(name="Anna's Kitchen", edition="restaurant")
        self.ent = Entitlement.objects.create(
            property=self.prop, hms=False, restaurant=True, banquets=False, rms=False)
        self.owner = User.objects.create_user(
            username="qa_owner", password="Tk9$mZ2pQw!7", role=ROLE_MD, is_superuser=True)
        self.client = APIClient()

    def _auth(self, user=None):
        c = APIClient()
        c.force_authenticate(user or self.owner)
        return c

    # --- /auth/setup/ ---------------------------------------------------

    def test_setup_rejects_anonymous(self):
        r = self.client.post("/api/auth/setup/", {"edition": "both"}, format="json")
        self.assertIn(r.status_code, (401, 403))
        self.ent.refresh_from_db()
        self.assertFalse(self.ent.hms)

    def test_setup_ignores_edition_from_the_customer(self):
        """The whole point: an owner running first-run setup on a restaurant
        licence cannot post their way onto the hotel suite."""
        self.prop.setup_done = False
        self.prop.save()
        r = self._auth().post("/api/auth/setup/",
                              {"edition": "both", "name": "Anna's Kitchen"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.prop.refresh_from_db()
        self.ent.refresh_from_db()
        self.assertEqual(self.prop.edition, "restaurant")  # unchanged
        self.assertFalse(self.ent.hms)
        self.assertTrue(self.prop.setup_done)

    def test_setup_is_one_time(self):
        self.prop.setup_done = True
        self.prop.save()
        r = self._auth().post("/api/auth/setup/", {"name": "Renamed"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_setup_opens_the_first_branch(self):
        """Setup used to leave the customer with zero branches, so every
        branch-scoped screen was empty on the first login."""
        self.prop.setup_done = False
        self.prop.save()
        self.assertEqual(Branch.objects.filter(property=self.prop).count(), 0)
        r = self._auth().post("/api/auth/setup/",
                              {"name": "Anna's Kitchen", "city": "Coimbatore",
                               "state": "Tamil Nadu"}, format="json")
        self.assertEqual(r.status_code, 200)
        branch = Branch.objects.get(property=self.prop)
        self.assertEqual(branch.city, "Coimbatore")
        self.assertEqual(branch.edition, "restaurant")
        self.assertEqual(branch.status, Branch.STATUS_ACTIVE)
        self.assertTrue(branch.restaurant)
        self.assertFalse(branch.hms)  # the licence follows the branch down

    def test_setup_needs_a_provisioned_edition(self):
        self.prop.edition = ""
        self.prop.setup_done = False
        self.prop.save()
        r = self._auth().post("/api/auth/setup/", {"name": "X"}, format="json")
        self.assertEqual(r.status_code, 409)

    # --- /auth/entitlements/ --------------------------------------------

    def test_owner_cannot_grant_themselves_a_licensed_module(self):
        r = self._auth().patch("/api/auth/entitlements/", {"hms": True}, format="json")
        self.assertEqual(r.status_code, 403)
        self.ent.refresh_from_db()
        self.assertFalse(self.ent.hms)

    def test_config_flags_are_still_the_customers_to_change(self):
        """The lock is on the commercial flags only — how they run what they
        bought stays theirs."""
        r = self._auth().patch("/api/auth/entitlements/",
                               {"kds_partial_ready": True}, format="json")
        self.assertEqual(r.status_code, 200)
        self.ent.refresh_from_db()
        self.assertTrue(self.ent.kds_partial_ready)

    def test_provision_is_the_only_way_up(self):
        from django.core.management import call_command

        call_command("provision", "--edition", "both", verbosity=0)
        self.ent.refresh_from_db()
        self.prop.refresh_from_db()
        self.assertTrue(self.ent.hms and self.ent.restaurant)
        self.assertEqual(self.prop.edition, "both")


class OnboardingFlowTests(TestCase):
    """The whole first run, the way the wizard walks it: bootstrap the owner,
    sign in, record the business, seed starter data, land ready to trade."""

    def setUp(self):
        from .models import Entitlement, Property

        self.prop = Property.objects.create(name="Hearth Property", edition="both")
        Entitlement.objects.create(property=self.prop)
        self.client = APIClient()

    def test_fresh_install_reaches_ready_to_operate(self):
        from apps.pos.models import Category, MenuItem, Table
        from apps.rooms.models import RatePlan, Room, RoomType

        # 1. No owner yet — the property read says so, and bootstrap is open.
        r = self.client.get("/api/auth/property/")
        self.assertTrue(r.json()["needs_admin"])
        r = self.client.post("/api/auth/bootstrap/", {
            "name": "Meera Rao", "username": "meera",
            "email": "meera@seaside.example", "password": "Tk9$mZ2pQw!7",
        }, format="json")
        self.assertEqual(r.status_code, 201)

        # 2. Sign in — everything past here is authenticated.
        r = self.client.post("/api/auth/token/",
                             {"username": "meera", "password": "Tk9$mZ2pQw!7"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")

        # 3. Business details. Nothing about the edition is sent or accepted.
        r = self.client.post("/api/auth/setup/", {
            "name": "Seaside Grand", "address": "12 Beach Road", "city": "Chennai",
            "state": "Tamil Nadu", "gstin": "33ABCDE1234F1Z5", "currency": "INR",
        }, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Branch.objects.get(property=self.prop).city, "Chennai")

        # 4. Mid-setup the checklist is honest about what's missing.
        r = self.client.get("/api/auth/setup/checklist/")
        self.assertEqual(r.status_code, 200)
        before = r.json()["checklist"]
        self.assertFalse(before["ready_to_operate"])
        self.assertIn("rooms", before["blocking"])
        self.assertIn("menuitems", before["blocking"])
        # A "both" licence is offered hotel AND restaurant packs.
        offered = {p["key"] for p in r.json()["packs"]}
        self.assertIn("hotel_rooms", offered)
        self.assertIn("menu_south_indian", offered)

        # 5. Starter packs.
        r = self.client.post("/api/auth/setup/starter-packs/", {
            "packs": ["gst_india", "hotel_rooms", "rooms", "menu_south_indian", "tables"],
            "options": {"rooms_count": 24, "tables_count": 10},
        }, format="json")
        self.assertEqual(r.status_code, 200)

        self.assertEqual(RoomType.objects.count(), 3)
        self.assertEqual(RatePlan.objects.count(), 9)   # 3 types x 3 plans
        self.assertEqual(Room.objects.count(), 24)
        self.assertEqual(Table.objects.count(), 10)
        self.assertTrue(Category.objects.filter(is_bar=False).exists())
        self.assertTrue(MenuItem.objects.exists())
        # Rooms are numbered by floor and hung off the branch that setup opened.
        self.assertTrue(Room.objects.filter(number="101").exists())
        self.assertTrue(Room.objects.filter(number="301").exists())
        self.assertIsNotNone(Room.objects.first().location)

        # 6. Ready to trade, with the optional remainder still listed.
        after = self.client.get("/api/auth/setup/checklist/").json()["checklist"]
        self.assertTrue(after["ready_to_operate"])
        self.assertEqual(after["blocking"], [])
        self.assertGreater(after["pct"], before["pct"])
        self.assertIn("staff", [s["key"] for s in after["steps"] if not s["done"]])

    def test_packs_are_idempotent(self):
        from apps.pos.models import MenuItem

        from .onboarding import apply_packs

        apply_packs(self.prop, ["menu_south_indian"])
        first = MenuItem.objects.count()
        apply_packs(self.prop, ["menu_south_indian"])
        self.assertEqual(MenuItem.objects.count(), first)

    def test_packs_respect_the_licence(self):
        from apps.rooms.models import RoomType

        from .onboarding import apply_packs, pack_catalog

        ent = self.prop.entitlement
        ent.hms = False
        ent.save()

        self.assertNotIn("hotel_rooms", {p["key"] for p in pack_catalog(self.prop)})
        apply_packs(self.prop, ["hotel_rooms"])  # asked for anyway
        self.assertEqual(RoomType.objects.count(), 0)

    def test_checklist_only_asks_for_what_is_licensed(self):
        from .onboarding import checklist

        ent = self.prop.entitlement
        ent.hms = False  # restaurant-only install
        ent.save()

        keys = {s["key"] for s in checklist(self.prop)["steps"]}
        self.assertNotIn("rooms", keys)
        self.assertNotIn("roomtypes", keys)
        self.assertIn("menuitems", keys)
        self.assertIn("tables", keys)


class InvoiceNumberRuleTests(TestCase):
    """GST Rule 46(b): a tax-invoice number is at most 16 characters.

    The format is PREFIX-YYYYMM-NNNNN — 13 characters of overhead — so the
    prefix can be at most 3. `invoice_prefix` allows 12, and the shipped
    default POS prefix was "BILL" (4), which produced a 17-character number.
    """

    def setUp(self):
        from .models import Entitlement, Property

        self.prop = Property.objects.create(name="Seaside Grand", edition="both")
        Entitlement.objects.create(property=self.prop)
        self.admin = User.objects.create_user(
            username="qa_numbering", password="Tk9$mZ2pQw!7", role=ROLE_MD, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_api_refuses_an_over_long_statutory_prefix(self):
        r = self.client.patch("/api/auth/property/", {"invoice_prefix": "SEASIDE"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["field"], "invoice_prefix")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.invoice_prefix, "HRT")  # unchanged

        r = self.client.patch("/api/auth/property/", {"bill_prefix": "BILLS"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_internal_series_are_not_capped(self):
        """A purchase order isn't a tax document — no 16-character limit."""
        r = self.client.patch("/api/auth/property/", {"po_prefix": "PURCHASE"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.po_prefix, "PURCHASE")

    def test_three_character_prefix_is_accepted(self):
        r = self.client.patch("/api/auth/property/", {"invoice_prefix": "SEA"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.invoice_prefix, "SEA")

    def test_generation_trims_a_legacy_prefix_to_stay_compliant(self):
        """Properties configured before the cap existed (the "BILL" default)
        keep working, and their numbers come out inside 16 characters."""
        from apps.pos.models import Order

        from .numbering import GST_MAX_DOC_NUMBER, next_document_number

        self.prop.bill_prefix = "BILL"  # 4 chars — set directly, bypassing the API
        self.prop.save()
        number = next_document_number(Order, "bill_no", self.prop.bill_prefix,
                                      max_total=GST_MAX_DOC_NUMBER)
        self.assertLessEqual(len(number), 16)
        self.assertTrue(number.startswith("BIL-"))

    def test_uncapped_call_is_unchanged(self):
        from apps.procurement.models import PurchaseOrder

        from .numbering import next_document_number

        number = next_document_number(PurchaseOrder, "po_no", "PURCHASE")
        self.assertTrue(number.startswith("PURCHASE-"))


class CaseCollisionTests(TestCase):
    """Two records that differ only in capitals read as one thing to a person
    and as two to the database — the bug that let `abishek1828` and
    `ABISHEK1828` both exist as separate logins."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="x", role=ROLE_SUPER_ADMIN, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_a_username_cannot_be_reused_in_different_capitals(self):
        first = self.client.post("/api/auth/users/", {
            "username": "abishek1828", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(first.status_code, 201)
        second = self.client.post("/api/auth/users/", {
            "username": "ABISHEK1828", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(second.status_code, 400)
        self.assertEqual(User.objects.filter(username__iexact="abishek1828").count(), 1)

    def test_usernames_are_stored_lower_case(self):
        self.client.post("/api/auth/users/", {
            "username": "Rasakannu", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertTrue(User.objects.filter(username="rasakannu").exists())

    def test_a_username_must_look_like_one(self):
        for bad in ("a", "-nope", "has space", "kya@hoo"):
            r = self.client.post("/api/auth/users/", {
                "username": bad, "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
            self.assertEqual(r.status_code, 400, f"{bad!r} was accepted")

    def test_renaming_onto_another_account_is_blocked(self):
        self.client.post("/api/auth/users/", {
            "username": "arun", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        other = User.objects.create_user(username="meera", password="x", role=ROLE_CASHIER)
        r = self.client.patch(f"/api/auth/users/{other.id}/", {"username": "ARUN"})
        self.assertEqual(r.status_code, 400)

    def test_you_can_still_save_your_own_record(self):
        # The uniqueness check has to exclude the row being edited, or nobody
        # could ever save an unrelated change.
        u = User.objects.create_user(username="meera", password="x", role=ROLE_CASHIER)
        r = self.client.patch(f"/api/auth/users/{u.id}/", {"username": "meera",
                                                           "first_name": "Meera"})
        self.assertEqual(r.status_code, 200, r.data)

    def test_signing_in_ignores_capitals(self):
        from django.contrib.auth import authenticate
        User.objects.create_user(username="arun", password="Tk9$mZ2pQw!7", role=ROLE_CASHIER)
        self.assertIsNotNone(authenticate(username="ARUN", password="Tk9$mZ2pQw!7"))
        self.assertIsNotNone(authenticate(username="arun", password="Tk9$mZ2pQw!7"))
        self.assertIsNone(authenticate(username="arun", password="wrong"))

    def test_legacy_mixed_case_pairs_still_sign_in_exactly(self):
        # A database created before this fix can hold both spellings. Neither
        # account may be silently swapped for the other.
        User.objects.create_user(username="dup", password="Tk9$mZ2pQw!7", role=ROLE_CASHIER)
        User.objects.create_user(username="DUP", password="Rr4$xB8vLz!3", role=ROLE_HOUSEKEEPING)
        from django.contrib.auth import authenticate
        self.assertEqual(authenticate(username="dup", password="Tk9$mZ2pQw!7").role, ROLE_CASHIER)
        self.assertEqual(authenticate(username="DUP", password="Rr4$xB8vLz!3").role, ROLE_HOUSEKEEPING)
        # Ambiguous casing that matches neither exactly is refused, not guessed.
        self.assertIsNone(authenticate(username="Dup", password="Tk9$mZ2pQw!7"))

    def test_a_role_cannot_be_duplicated_in_different_capitals(self):
        Role.objects.create(name="Night Manager", base_role=ROLE_FRONT_OFFICE, rank=1, modules=[])
        r = self.client.post("/api/auth/roles/", {
            "name": "night manager", "base_role": ROLE_FRONT_OFFICE, "rank": 1,
            "modules": []}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_a_master_cannot_be_duplicated_in_different_capitals(self):
        # Departments are the dangerous one: indent and leave approvals route on
        # the exact string, so a lower-case twin would have no approver at all.
        from apps.masters.models import Department
        Department.objects.get_or_create(name="Kitchen")
        r = self.client.post("/api/masters/departments/", {"name": "kitchen"}, format="json")
        self.assertEqual(r.status_code, 400)


class SharedInputRuleTests(TestCase):
    """The Aug-2026 audit findings, each pinned by the test that would have
    caught it. All three were the same shape: a rule the codebase already knew
    how to write, not applied on every path that needed it."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner2", password="x", role=ROLE_SUPER_ADMIN, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    # -- F1: one email, one account -------------------------------------
    def test_an_email_cannot_be_shared_by_two_accounts(self):
        first = self.client.post("/api/auth/users/", {
            "username": "meera", "email": "front@hearth.test",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(first.status_code, 201)
        second = self.client.post("/api/auth/users/", {
            "username": "arun2", "email": "front@hearth.test",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(second.status_code, 400)
        self.assertIn("email", second.data)
        self.assertEqual(User.objects.filter(email__iexact="front@hearth.test").count(), 1)

    def test_an_email_clash_is_caught_across_capitals_too(self):
        self.client.post("/api/auth/users/", {
            "username": "meera2", "email": "Desk@Hearth.test",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        clash = self.client.post("/api/auth/users/", {
            "username": "arun3", "email": "desk@hearth.TEST",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(clash.status_code, 400)

    def test_blank_emails_do_not_collide(self):
        # Floor staff often have no address; that must not make them one person.
        a = self.client.post("/api/auth/users/", {
            "username": "floor1", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        b = self.client.post("/api/auth/users/", {
            "username": "floor2", "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual((a.status_code, b.status_code), (201, 201))

    # -- W1: one phone rule ---------------------------------------------
    def test_a_phone_number_keeps_its_country_code(self):
        r = self.client.post("/api/auth/users/", {
            "username": "intl", "phone": "+91 90000 00000",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(User.objects.get(username="intl").phone, "+919000000000")

    def test_a_phone_number_typed_with_separators_is_stored_one_way(self):
        self.client.post("/api/auth/users/", {
            "username": "spaced", "phone": "(044) 2222-3333",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(User.objects.get(username="spaced").phone, "04422223333")

    def test_letters_are_not_a_phone_number(self):
        r = self.client.post("/api/auth/users/", {
            "username": "bogus", "phone": "call me",
            "role": ROLE_CASHIER, "password": "Str0ng!pass9"})
        self.assertEqual(r.status_code, 400)

    def test_the_international_prefix_folds_to_plus(self):
        from .validators import normalize_phone
        self.assertEqual(normalize_phone("0091 90000 00000"), "+919000000000")

    def test_a_number_longer_than_e164_allows_is_refused(self):
        from rest_framework.serializers import ValidationError
        from .validators import validate_phone
        with self.assertRaises(ValidationError):
            validate_phone("+9199999999999999")

    def test_a_blank_phone_still_passes(self):
        from .validators import validate_phone
        self.assertEqual(validate_phone(""), "")
