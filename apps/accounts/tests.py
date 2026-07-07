from django.test import TestCase

from .constants import (
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
