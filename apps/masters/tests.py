from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.hr.models import Employee

from .models import Department, Designation, PaymentMethod


def client_as(role, username):
    c = APIClient()
    c.force_authenticate(User.objects.create_user(
        username=username, password="Tk9$mZ2pQw!7", role=role))
    return c


class MastersSeedTests(TestCase):
    def test_seed_rows_exist(self):
        self.assertTrue(Department.objects.filter(name="Kitchen").exists())
        self.assertTrue(Designation.objects.filter(name="Steward").exists())
        cash = PaymentMethod.objects.get(name="Cash")
        self.assertTrue(cash.builtin and cash.counts_as_cash and not cash.captain_allowed)
        upi = PaymentMethod.objects.get(name="UPI")
        self.assertTrue(upi.builtin and upi.captain_allowed and not upi.counts_as_cash)


class MastersPermissionTests(TestCase):
    def test_reads_open_to_floor_roles_writes_settings_only(self):
        # A captain has no 'settings' module but their tender buttons and the
        # indent department picker both read from the masters.
        captain = client_as("Captain", "cap")
        self.assertEqual(captain.get("/api/masters/departments/").status_code, 200)
        self.assertEqual(captain.get("/api/masters/payment-methods/").status_code, 200)
        r = captain.post("/api/masters/departments/", {"name": "Spa"}, format="json")
        self.assertEqual(r.status_code, 403)
        # Admin holds 'settings' and may write.
        admin = client_as("Admin", "adm")
        r = admin.post("/api/masters/departments/", {"name": "Spa"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)


class MastersLifecycleTests(TestCase):
    def setUp(self):
        self.admin = client_as("Admin", "adm2")

    def test_department_in_use_deactivates_instead_of_deleting(self):
        Employee.objects.create(name="Asha", department="Kitchen", role="Cook")
        dep = Department.objects.get(name="Kitchen")
        r = self.admin.delete(f"/api/masters/departments/{dep.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mark it inactive", r.data["detail"])
        # Deactivating works, and an unused department deletes cleanly.
        r = self.admin.patch(f"/api/masters/departments/{dep.id}/", {"active": False}, format="json")
        self.assertEqual(r.status_code, 200)
        unused = Department.objects.get(name="Banquets")
        self.assertEqual(self.admin.delete(f"/api/masters/departments/{unused.id}/").status_code, 204)

    def test_builtin_tender_cannot_be_renamed_or_deleted(self):
        cash = PaymentMethod.objects.get(name="Cash")
        r = self.admin.patch(f"/api/masters/payment-methods/{cash.id}/",
                             {"name": "Monies"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.admin.delete(f"/api/masters/payment-methods/{cash.id}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("built-in", r.data["detail"])

    def test_custom_tender_flags_drive_role_can_tender(self):
        from apps.accounts.constants import role_can_tender
        self.admin.post("/api/masters/payment-methods/",
                        {"name": "Sodexo", "captain_allowed": False}, format="json")
        self.assertFalse(role_can_tender("Captain", "Sodexo"))
        self.assertTrue(role_can_tender("F&B Cashier", "Sodexo"))
        # Flipping captain_allowed opens it up tableside.
        pm = PaymentMethod.objects.get(name="Sodexo")
        self.admin.patch(f"/api/masters/payment-methods/{pm.id}/",
                         {"captain_allowed": True}, format="json")
        self.assertTrue(role_can_tender("Captain", "Sodexo"))
        # Deactivating kills it for everyone.
        self.admin.patch(f"/api/masters/payment-methods/{pm.id}/",
                         {"active": False}, format="json")
        self.assertFalse(role_can_tender("F&B Cashier", "Sodexo"))


class AuditTrailTests(TestCase):
    def test_master_change_is_backtrackable(self):
        """Every master edit lands in the audit log with before → after,
        readable from /auth/audit/ by settings-capable roles only."""
        admin = client_as("Admin", "audadm")
        dep = Department.objects.get(name="Kitchen")
        admin.patch(f"/api/masters/departments/{dep.id}/", {"active": False}, format="json")
        r = admin.get("/api/auth/audit/?entity=Department&action=master_updated")
        self.assertEqual(r.status_code, 200)
        row = r.data[0]
        self.assertEqual(row["user"], "audadm")
        self.assertEqual(row["before"], {"name": "Kitchen", "active": True})
        self.assertEqual(row["after"], {"name": "Kitchen", "active": False})
        # Floor roles can't read the trail.
        captain = client_as("Captain", "audcap")
        self.assertEqual(captain.get("/api/auth/audit/").status_code, 403)


class EmployeeMasterTests(TestCase):
    def setUp(self):
        self.admin = client_as("Admin", "adm3")

    def test_create_validates_against_masters(self):
        r = self.admin.post("/api/hr/", {
            "name": "Ravi", "department": "Kitchen", "role": "Cook",
            "phone": "9876543210", "monthly_salary": "18000"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        r = self.admin.post("/api/hr/", {
            "name": "Maya", "department": "Rooftop", "role": "Cook"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not an active department", r.data["detail"])

    def test_status_toggle(self):
        e = Employee.objects.create(name="Asha", department="Kitchen", role="Cook")
        r = self.admin.post(f"/api/hr/{e.id}/set_status/", {"status": "Inactive"}, format="json")
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.status, "Inactive")
