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
        self.assertEqual(row["gross_earned"], "15000.00")  # 30000 × 15.5/31
        # payable is now NET of statutory deductions (PF 12% of the 7,500
        # earned basic = 900; no ESI above the 21k gross ceiling; no PT
        # under 21k earned) — see payroll.compute_payslip.
        self.assertEqual(row["payable"], "14100.00")


class EmployeeImportTests(TestCase):
    def setUp(self):
        from apps.masters.models import Department, Designation
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hr2", password="Tk9$mZ2pQw!7", role="HR Manager"))
        Department.objects.get_or_create(name="Kitchen", defaults={"active": True})
        Designation.objects.get_or_create(name="Cook", defaults={"active": True})

    def test_template_has_expected_columns(self):
        r = self.client.get(reverse("hr-import-employees"))
        self.assertEqual(r.status_code, 200)
        header = r.content.decode().splitlines()[0]
        self.assertEqual(
            header, "name,department,role,phone,wage_type,monthly_salary,daily_rate,branch")

    def test_import_creates_valid_rows_and_reports_errors(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_body = (
            "name,department,role,phone,wage_type,monthly_salary,daily_rate,branch\r\n"
            "Anita Sharma,Kitchen,Cook,9000000001,monthly,18000,,\r\n"
            "Bad Row,NoSuchDept,NoSuchRole,,,,,\r\n"
            ",,,,,,,\r\n"
        )
        f = SimpleUploadedFile("employees.csv", csv_body.encode(), content_type="text/csv")
        r = self.client.post(reverse("hr-import-employees"), {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["created"], 1)
        self.assertEqual(r.data["skipped_existing"], [])
        self.assertEqual(len(r.data["errors"]), 1)
        self.assertEqual(r.data["errors"][0]["reason"], "'NoSuchDept' is not an active department")
        self.assertTrue(Employee.objects.filter(name="Anita Sharma").exists())

    def test_import_skips_existing_names(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        Employee.objects.create(name="Anita Sharma", department="Kitchen", role="Cook")
        csv_body = (
            "name,department,role,phone,wage_type,monthly_salary,daily_rate,branch\r\n"
            "Anita Sharma,Kitchen,Cook,9000000001,monthly,18000,,\r\n"
        )
        f = SimpleUploadedFile("employees.csv", csv_body.encode(), content_type="text/csv")
        r = self.client.post(reverse("hr-import-employees"), {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["created"], 0)
        self.assertEqual(r.data["skipped_existing"], ["Anita Sharma"])
        self.assertEqual(Employee.objects.filter(name="Anita Sharma").count(), 1)
