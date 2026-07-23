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
            header,
            "name,department,role,country_code,phone,wage_type,monthly_salary,"
            "daily_rate,weekly_rate,branch")

    def test_import_creates_valid_rows_and_reports_errors(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_body = (
            "name,department,role,country_code,phone,wage_type,monthly_salary,daily_rate,weekly_rate,branch\r\n"
            "Anita Sharma,Kitchen,Cook,+91,9000000001,monthly,18000,,,\r\n"
            "Bad Row,NoSuchDept,NoSuchRole,,,,,,,\r\n"
            ",,,,,,,,,\r\n"
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


class CountryCodeAndWeeklyWageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hrcc", password="Tk9$mZ2pQw!7", role="HR Manager"))
        from apps.masters.models import Department, Designation
        Department.objects.get_or_create(name="Kitchen", defaults={"active": True})
        Designation.objects.get_or_create(name="Cook", defaults={"active": True})

    def test_create_defaults_country_code_to_plus91(self):
        r = self.client.post(reverse("hr-list"),
                             {"name": "Meena Iyer", "department": "Kitchen", "role": "Cook"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["country_code"], "+91")

    def test_create_rejects_malformed_country_code(self):
        r = self.client.post(reverse("hr-list"),
                             {"name": "Meena Iyer", "department": "Kitchen", "role": "Cook",
                              "country_code": "91"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_create_accepts_weekly_wage_type(self):
        r = self.client.post(reverse("hr-list"),
                             {"name": "Sam Contract", "department": "Kitchen", "role": "Cook",
                              "wage_type": "weekly", "weekly_rate": "4000"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["wage_type"], "weekly")
        self.assertEqual(Decimal(r.data["weekly_rate"]), Decimal("4000"))

    def test_update_can_switch_to_weekly_wage(self):
        emp = Employee.objects.create(name="Ravi", department="Kitchen", role="Cook",
                                      monthly_salary=Decimal("20000"))
        r = self.client.patch(f"/api/hr/{emp.id}/",
                              {"wage_type": "weekly", "weekly_rate": "5000"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        emp.refresh_from_db()
        self.assertEqual(emp.wage_type, "weekly")
        self.assertEqual(emp.weekly_rate, Decimal("5000.00"))

    def test_update_can_change_country_code(self):
        emp = Employee.objects.create(name="Ravi", department="Kitchen", role="Cook")
        r = self.client.patch(f"/api/hr/{emp.id}/", {"country_code": "+1"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        emp.refresh_from_db()
        self.assertEqual(emp.country_code, "+1")


class SetStatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hrstatus", password="Tk9$mZ2pQw!7", role="HR Manager"))
        self.emp = Employee.objects.create(name="Divya", department="Kitchen", role="Cook", status="Active")

    def test_set_status_toggles_active_and_inactive(self):
        r = self.client.post(reverse("hr-set-status", args=[self.emp.id]), {"status": "Inactive"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, "Inactive")
        r = self.client.post(reverse("hr-set-status", args=[self.emp.id]), {"status": "Active"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, "Active")

    def test_set_status_rejects_invalid_value(self):
        r = self.client.post(reverse("hr-set-status", args=[self.emp.id]), {"status": "Terminated"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_set_status_404_for_unknown_employee(self):
        r = self.client.post(reverse("hr-set-status", args=[999999]), {"status": "Active"}, format="json")
        self.assertEqual(r.status_code, 404)


class OverviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="hroverview", password="Tk9$mZ2pQw!7", role="HR Manager"))

    def test_overview_counts_headcount_and_muster(self):
        from django.utils import timezone
        e1 = Employee.objects.create(name="A", department="Kitchen", role="Cook", status="Active")
        e2 = Employee.objects.create(name="B", department="Kitchen", role="Cook", status="Active")
        Employee.objects.create(name="C", department="Kitchen", role="Cook", status="Inactive")
        today = timezone.localdate()
        Attendance.objects.create(employee=e1, date=today, status="present")
        Attendance.objects.create(employee=e2, date=today, status="half")
        r = self.client.get(reverse("hr-overview"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["headcount"], 2)
        self.assertEqual(r.data["today"]["present"], 1)
        self.assertEqual(r.data["today"]["half"], 1)

    def test_overview_lists_employees_on_approved_leave_today(self):
        from django.utils import timezone
        from .models import LeaveRequest, LeaveType
        e = Employee.objects.create(name="A", department="Kitchen", role="Cook", status="Active")
        lt = LeaveType.objects.create(name="Casual", annual_quota=12, is_paid=True)
        today = timezone.localdate()
        LeaveRequest.objects.create(
            employee=e, leave_type=lt, start_date=today, end_date=today, days=1,
            status=LeaveRequest.APPROVED, requested_by="hroverview")
        r = self.client.get(reverse("hr-overview"))
        self.assertEqual(len(r.data["on_leave"]), 1)
        self.assertEqual(r.data["on_leave"][0]["employee"], "A")

    def test_overview_wage_bill_splits_salaried_vs_casual(self):
        Employee.objects.create(name="Salaried", department="Kitchen", role="Cook",
                                status="Active", wage_type="monthly", monthly_salary=Decimal("20000"))
        Employee.objects.create(name="Casual", department="Kitchen", role="Cook",
                                status="Active", wage_type="daily", daily_rate=Decimal("700"))
        r = self.client.get(reverse("hr-overview"))
        self.assertEqual(Decimal(r.data["monthly_wage_bill"]), Decimal("20000") + Decimal("700") * 26)
