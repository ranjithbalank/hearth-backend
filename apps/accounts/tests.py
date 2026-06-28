from django.test import TestCase

from .constants import (
    ROLE_CASHIER,
    ROLE_FRONT_OFFICE,
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
