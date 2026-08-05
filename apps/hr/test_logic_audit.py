"""Logic-layer audit — HR roster and payroll invariants."""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.masters.models import Department, Designation

from .models import Employee


class RosterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.hr = User.objects.create_user(
            username="hraudit", password="Tk9$mZ2pQw!7", role="HR Manager")
        self.client.force_authenticate(self.hr)
        Department.objects.get_or_create(name="Audit Dept", defaults={"active": True})
        Designation.objects.get_or_create(name="Audit Role", defaults={"active": True})

    def _add(self, name):
        return self.client.post("/api/hr/", {
            "name": name, "department": "Audit Dept", "role": "Audit Role",
            "monthly_salary": "30000"}, format="json")

    def test_the_same_person_cannot_be_added_to_the_roster_twice(self):
        """Two payroll records for one person means two salaries, two leave
        balances and two advances. The CSV import already treats the name as
        unique and skips a repeat; the API create accepted it — the same rule
        applied on one path and not the other."""
        first = self._add("Ramesh Kumar")
        self.assertEqual(first.status_code, 201)
        second = self._add("ramesh kumar")
        self.assertEqual(second.status_code, 400,
                         "a second payroll record was created for the same person")
        self.assertEqual(Employee.objects.filter(name__iexact="ramesh kumar").count(), 1)

    def test_a_distinct_name_is_still_accepted(self):
        self.assertEqual(self._add("Suresh Kumar").status_code, 201)
        self.assertEqual(self._add("Mahesh Kumar").status_code, 201)

    def test_a_negative_salary_is_refused(self):
        r = self.client.post("/api/hr/", {
            "name": "Negative Salary", "department": "Audit Dept", "role": "Audit Role",
            "monthly_salary": "-5000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Employee.objects.filter(name="Negative Salary").exists())
