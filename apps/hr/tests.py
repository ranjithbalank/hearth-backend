from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Attendance, Employee


class AttendancePayrollTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="gm", password="Tk9$mZ2pQw!7", role="General Manager"))
        self.emp = Employee.objects.create(name="Ravi", department="Kitchen", role="Cook",
                                           monthly_salary=Decimal("30000"))

    def test_mark_and_read_attendance(self):
        r = self.client.post(reverse("hr-mark-attendance"),
                             {"date": "2026-07-01", "marks": {str(self.emp.id): "present"}},
                             format="json")
        self.assertEqual(r.data["saved"], 1)
        r = self.client.get(reverse("hr-attendance") + "?date=2026-07-01")
        self.assertEqual(r.data["marks"][str(self.emp.id)], "present")
        # Re-marking the same day updates, not duplicates.
        self.client.post(reverse("hr-mark-attendance"),
                         {"date": "2026-07-01", "marks": {str(self.emp.id): "half"}}, format="json")
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 1)

    def test_payroll_prorates_by_payable_days(self):
        # 15 present + 1 half in July (31 days) → 15.5 payable days.
        for d in range(1, 16):
            Attendance.objects.create(employee=self.emp, date=f"2026-07-{d:02d}", status="present")
        Attendance.objects.create(employee=self.emp, date="2026-07-16", status="half")
        r = self.client.get(reverse("hr-payroll") + "?month=2026-07")
        row = r.data["rows"][0]
        self.assertEqual(row["payable_days"], "15.5")
        self.assertEqual(row["payable"], "15000.00")  # 30000 × 15.5/31
