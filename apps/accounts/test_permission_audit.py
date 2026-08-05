"""Permission audit — segregation of duties and privilege escalation.

Hearth's RBAC promise is that floor roles get no back-office and back-office
roles get no floor ops, and that nobody can hand out authority they don't hold.
These probe that promise from the outside, as an attacker with a valid login
would: authenticate as a real role and try the things that role must not do.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from .constants import (
    ROLE_ADMIN,
    ROLE_CASHIER,
    ROLE_FRONT_OFFICE,
    ROLE_HOUSEKEEPING,
    ROLE_SUPER_ADMIN,
)
from .models import User


def client_for(username, role, **extra):
    user = User.objects.create_user(username=username, password="Tk9$mZ2pQw!7",
                                    role=role, **extra)
    c = APIClient()
    c.force_authenticate(user)
    return c, user


class SegregationOfDutiesTests(TestCase):
    """A floor role reaching a back-office screen is the finding; a 403 or 404
    is the pass. Anything 2xx means the module gate did not hold."""

    def setUp(self):
        self.hk, _ = client_for("hk_probe", ROLE_HOUSEKEEPING)
        self.cashier, _ = client_for("cash_probe", ROLE_CASHIER)
        self.fo, _ = client_for("fo_probe", ROLE_FRONT_OFFICE)

    def _denied(self, response, what):
        self.assertIn(response.status_code, (401, 403, 404),
                      f"{what} was reachable (HTTP {response.status_code})")

    def test_housekeeping_cannot_read_payroll(self):
        self._denied(self.hk.get("/api/hr/payroll/"), "payroll from Housekeeping")

    def test_housekeeping_cannot_read_the_user_list(self):
        self._denied(self.hk.get("/api/auth/users/"), "the user list from Housekeeping")

    def test_housekeeping_cannot_change_entitlements(self):
        self._denied(self.hk.post("/api/auth/entitlements/", {"hms": False}, format="json"),
                     "entitlements from Housekeeping")

    def test_a_cashier_cannot_read_the_audit_log(self):
        self._denied(self.cashier.get("/api/auth/audit/"), "the audit log from a cashier")

    def test_a_cashier_cannot_approve_purchase_orders(self):
        self._denied(self.cashier.get("/api/purchase-orders/"),
                     "purchase orders from a cashier")

    def test_front_office_cannot_open_the_pos_till(self):
        self._denied(self.fo.post("/api/pos/till/open/", {"opening_float": "1000"},
                                  format="json"),
                     "the POS till from Front Office")


class PrivilegeEscalationTests(TestCase):
    """The rule that matters most: nobody hands out authority above their own."""

    def test_an_admin_cannot_create_a_super_admin(self):
        c, _ = client_for("admin_probe", ROLE_ADMIN)
        r = c.post("/api/auth/users/", {
            "username": "sneaky_owner", "role": ROLE_SUPER_ADMIN,
            "password": "Str0ng!pass9"}, format="json")
        self.assertNotIn(r.status_code, (200, 201),
                         "an Admin minted a Super Admin")
        self.assertFalse(User.objects.filter(username="sneaky_owner").exists())

    def test_a_cashier_cannot_create_any_login(self):
        c, _ = client_for("cash_probe2", ROLE_CASHIER)
        r = c.post("/api/auth/users/", {
            "username": "cashier_made_this", "role": ROLE_CASHIER,
            "password": "Str0ng!pass9"}, format="json")
        self.assertIn(r.status_code, (401, 403, 404))
        self.assertFalse(User.objects.filter(username="cashier_made_this").exists())

    def test_a_user_cannot_promote_themselves(self):
        """The most direct escalation there is: PATCH your own role."""
        c, user = client_for("selfpromo", ROLE_CASHIER)
        r = c.patch(f"/api/auth/users/{user.id}/", {"role": ROLE_SUPER_ADMIN},
                    format="json")
        user.refresh_from_db()
        self.assertEqual(user.role, ROLE_CASHIER,
                         f"a cashier promoted themselves to Super Admin (HTTP {r.status_code})")

    def test_a_user_cannot_grant_themselves_extra_modules(self):
        c, user = client_for("rightsgrab", ROLE_HOUSEKEEPING)
        c.patch(f"/api/auth/users/{user.id}/", {"rights": ["settings", "hr"]},
                format="json")
        user.refresh_from_db()
        self.assertNotIn("settings", user.rights or [],
                         "a housekeeper granted themselves the settings module")


class PayslipPrivacyTests(TestCase):
    """Payslips are reachable by an ordinary employee for their OWN record —
    the module gate is deliberately dropped there, so the ownership check
    inside is the only thing standing between one employee and another's pay."""

    def setUp(self):
        from apps.hr.models import Employee
        from apps.masters.models import Department, Designation
        Department.objects.get_or_create(name="Perm Dept", defaults={"active": True})
        Designation.objects.get_or_create(name="Perm Role", defaults={"active": True})
        self.c_a, self.user_a = client_for("emp_a", ROLE_HOUSEKEEPING)
        self.c_b, self.user_b = client_for("emp_b", ROLE_HOUSEKEEPING)
        self.emp_b = Employee.objects.create(
            name="Employee Bee", department="Perm Dept", role="Perm Role",
            monthly_salary=Decimal("40000"), user=self.user_b)

    def test_one_employee_cannot_read_anothers_payslips(self):
        r = self.c_a.get(f"/api/hr/my_payslips/?employee={self.emp_b.id}")
        if r.status_code == 200:
            payload = r.json()
            rows = payload if isinstance(payload, list) else payload.get("results", [])
            self.assertEqual(rows, [],
                             "one employee read another employee's payslips")
