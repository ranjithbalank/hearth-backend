"""Payroll (FR-HRM): salary split + statutory deductions, run lifecycle,
snapshot immutability, draft adjustments and role gating."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.constants import ROLE_ADMIN, ROLE_CEO, ROLE_GM, ROLE_HR
from .models import Attendance, Employee, PayrollRun
from .payroll import compute_payslip

User = get_user_model()

YEAR = date.today().year
MONTH = f"{YEAR}-06"          # June: 30 days
DAYS = 30


def _emp(**kw):
    """Unsaved Employee as a compute_payslip input snapshot."""
    defaults = dict(wage_type="monthly", monthly_salary=0, daily_rate=0, statutory=True)
    defaults.update(kw)
    return Employee(name="x", department="x", role="x", **defaults)


class PayrollMathTests(APITestCase):
    def test_full_month_with_deductions(self):
        # ₹30,000 gross, all 30 days: basic 15,000 / HRA 6,000 / other 9,000.
        m = compute_payslip(_emp(monthly_salary=Decimal("30000")), Decimal("30"), DAYS)
        self.assertEqual(m["basic"], Decimal("15000.00"))
        self.assertEqual(m["hra"], Decimal("6000.00"))
        self.assertEqual(m["other_allowance"], Decimal("9000.00"))
        # PF capped at 1800 (12% of 15,000 basic hits the ceiling exactly),
        # no ESI (gross above 21k), PT applies.
        self.assertEqual(m["pf"], Decimal("1800.00"))
        self.assertEqual(m["esi"], Decimal("0"))
        self.assertEqual(m["pt"], Decimal("200"))
        self.assertEqual(m["net"], Decimal("28000.00"))

    def test_esi_below_ceiling_and_proration(self):
        # ₹20,000 gross, 15 of 30 days: half of everything, ESI applies.
        m = compute_payslip(_emp(monthly_salary=Decimal("20000")), Decimal("15"), DAYS)
        self.assertEqual(m["gross_earned"], Decimal("10000.00"))
        self.assertEqual(m["basic"], Decimal("5000.00"))
        self.assertEqual(m["pf"], Decimal("600.00"))       # 12% of 5,000
        self.assertEqual(m["esi"], Decimal("75.00"))       # 0.75% of 10,000
        self.assertEqual(m["pt"], Decimal("0"))            # earned gross under 21k
        self.assertEqual(m["net"], Decimal("9325.00"))

    def test_daily_wage_off_rolls(self):
        # ₹700/day casual, 22.5 days, off the rolls: rate × days, no
        # split, no deductions at all.
        m = compute_payslip(_emp(wage_type="daily", daily_rate=Decimal("700"),
                                 statutory=False), Decimal("22.5"), DAYS)
        self.assertEqual(m["gross_earned"], Decimal("15750.00"))
        self.assertEqual(m["basic"], Decimal("15750.00"))
        self.assertEqual(m["hra"], Decimal("0.00"))
        self.assertEqual(m["pf"], Decimal("0"))
        self.assertEqual(m["esi"], Decimal("0"))
        self.assertEqual(m["pt"], Decimal("0"))
        self.assertEqual(m["net"], Decimal("15750.00"))

    def test_daily_wage_on_rolls_gets_statutory(self):
        # A registered daily-rated worker still gets PF/ESI off earned pay.
        m = compute_payslip(_emp(wage_type="daily", daily_rate=Decimal("500"),
                                 statutory=True), Decimal("20"), DAYS)
        self.assertEqual(m["gross_earned"], Decimal("10000.00"))
        self.assertEqual(m["pf"], Decimal("1200.00"))      # 12% of 10,000 basic
        self.assertEqual(m["esi"], Decimal("75.00"))       # within the 21k ceiling
        self.assertEqual(m["net"], Decimal("8725.00"))

    def test_monthly_off_rolls_no_deductions(self):
        m = compute_payslip(_emp(monthly_salary=Decimal("30000"), statutory=False),
                            Decimal("30"), DAYS)
        self.assertEqual(m["pf"], Decimal("0"))
        self.assertEqual(m["pt"], Decimal("0"))
        self.assertEqual(m["net"], Decimal("30000.00"))

    def test_monthly_without_allowances_is_all_basic(self):
        # No-allowance structure: the whole gross is basic. PF base rises
        # with it — 12% of 10,000 instead of 12% of the 5,000 split basic.
        m = compute_payslip(_emp(monthly_salary=Decimal("10000"), has_allowances=False),
                            Decimal("30"), DAYS)
        self.assertEqual(m["basic"], Decimal("10000.00"))
        self.assertEqual(m["hra"], Decimal("0.00"))
        self.assertEqual(m["other_allowance"], Decimal("0.00"))
        self.assertEqual(m["pf"], Decimal("1200.00"))
        self.assertEqual(m["esi"], Decimal("75.00"))       # within the 21k ceiling
        self.assertEqual(m["net"], Decimal("8725.00"))

    def test_weekly_wage_full_week_above_esi_ceiling(self):
        # ₹7,000/week, one full week (7 of 7 payable days): same 50/20/30
        # split as monthly. Monthly-equivalent (7000×52/12 ≈ 30,333) is
        # above the ESI ceiling, so no ESI — same as a well-paid monthly
        # employee would see.
        m = compute_payslip(_emp(wage_type="weekly", weekly_rate=Decimal("7000")),
                            Decimal("7"), DAYS)
        self.assertEqual(m["gross_earned"], Decimal("7000.00"))
        self.assertEqual(m["basic"], Decimal("3500.00"))
        self.assertEqual(m["hra"], Decimal("1400.00"))
        self.assertEqual(m["other_allowance"], Decimal("2100.00"))
        self.assertEqual(m["pf"], Decimal("420.00"))        # 12% of 3,500 basic
        self.assertEqual(m["esi"], Decimal("0"))
        self.assertEqual(m["net"], Decimal("6580.00"))

    def test_weekly_wage_prorated_and_under_esi_ceiling(self):
        # ₹3,000/week, half a week (3.5 of 7 payable days). Monthly-equivalent
        # (~13,000) is under the ESI ceiling, so ESI applies.
        m = compute_payslip(_emp(wage_type="weekly", weekly_rate=Decimal("3000")),
                            Decimal("3.5"), DAYS)
        self.assertEqual(m["gross_earned"], Decimal("1500.00"))
        self.assertEqual(m["basic"], Decimal("750.00"))
        self.assertEqual(m["pf"], Decimal("90.00"))
        self.assertEqual(m["esi"], Decimal("11.25"))
        self.assertEqual(m["net"], Decimal("1398.75"))


class PayrollRunTests(APITestCase):
    def setUp(self):
        self.hr = User.objects.create_user("hruser", password="x", role=ROLE_HR)
        self.ceo = User.objects.create_user("ceouser", password="x", role=ROLE_CEO)
        self.gm = User.objects.create_user("gmuser", password="x", role=ROLE_GM)
        self.emp = Employee.objects.create(
            name="Sunita", department="Housekeeping", role="HK Supervisor",
            monthly_salary=Decimal("30000"))
        # 10 present days marked in June.
        for d in range(1, 11):
            Attendance.objects.create(employee=self.emp, date=date(YEAR, 6, d),
                                      status=Attendance.PRESENT)

    def _run(self, user=None):
        self.client.force_authenticate(user or self.hr)
        return self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")

    def test_preview_then_run_snapshot(self):
        self.client.force_authenticate(self.hr)
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        self.assertIsNone(res.data["run"])
        row = res.data["rows"][0]
        self.assertEqual(row["payable_days"], "10")
        self.assertEqual(row["gross_earned"], "10000.00")
        # Run it, then add attendance — the snapshot must not move.
        self.assertEqual(self._run().status_code, 201)
        Attendance.objects.create(employee=self.emp, date=date(YEAR, 6, 15),
                                  status=Attendance.PRESENT)
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        self.assertEqual(res.data["run"]["status"], "draft")
        self.assertEqual(res.data["rows"][0]["gross_earned"], "10000.00")
        # A second run for the same month is refused.
        self.assertEqual(self._run().status_code, 400)

    def test_ceo_is_read_only(self):
        res = self._run(self.ceo)
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.ceo)
        self.assertEqual(self.client.get(f"/api/hr/payroll/?month={MONTH}").status_code, 200)

    def test_adjust_then_lifecycle_locks(self):
        self._run()
        self.client.force_authenticate(self.hr)
        slip = self.client.get(f"/api/hr/payroll/?month={MONTH}").data["rows"][0]["payslip"]
        res = self.client.post("/api/hr/adjust_payslip/",
                               {"payslip": slip, "amount": "500", "note": "festival bonus"},
                               format="json")
        self.assertEqual(res.status_code, 200, res.data)
        base_net = Decimal("10000.00") - Decimal("600") - Decimal("0") - Decimal("0")  # pf only? see below
        # net = gross_earned − pf − esi − pt + adjustment; just check the delta.
        self.assertEqual(Decimal(res.data["net"]) - Decimal(res.data["adjustment"]),
                         Decimal(res.data["gross_earned"]) - Decimal(res.data["pf"])
                         - Decimal(res.data["esi"]) - Decimal(res.data["pt"]))
        # Replacing an adjustment swaps it, not stacks it.
        res = self.client.post("/api/hr/adjust_payslip/",
                               {"payslip": slip, "amount": "200"}, format="json")
        self.assertEqual(Decimal(res.data["adjustment"]), Decimal("200.00"))
        # draft → finalized: adjustments refuse; finalized → paid.
        res = self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.assertEqual(res.data["status"], "finalized")
        res = self.client.post("/api/hr/adjust_payslip/",
                               {"payslip": slip, "amount": "999"}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.assertEqual(res.data["status"], "paid")
        self.assertEqual(res.data["paid_by"], "hruser")
        res = self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_discard_draft_only(self):
        self._run()
        self.client.force_authenticate(self.gm)
        res = self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.assertEqual(res.data["status"], "finalized")
        res = self.client.post("/api/hr/advance_payroll/",
                               {"month": MONTH, "action": "discard"}, format="json")
        self.assertEqual(res.status_code, 400)   # finalized can't be discarded
        run = PayrollRun.objects.get(month=MONTH)
        run.status = PayrollRun.DRAFT
        run.save(update_fields=["status"])
        res = self.client.post("/api/hr/advance_payroll/",
                               {"month": MONTH, "action": "discard"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(PayrollRun.objects.filter(month=MONTH).exists())

    def test_salary_edit(self):
        self.client.force_authenticate(self.hr)
        res = self.client.put(f"/api/hr/{self.emp.id}/",
                              {"monthly_salary": "36000"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["monthly_salary"], "36000")
        res = self.client.put(f"/api/hr/{self.emp.id}/",
                              {"monthly_salary": "-5"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_switch_to_daily_wage(self):
        self.client.force_authenticate(self.hr)
        res = self.client.put(f"/api/hr/{self.emp.id}/",
                              {"wage_type": "daily", "daily_rate": "650",
                               "statutory": False}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["wage_type"], "daily")
        # 10 present days × ₹650, nothing deducted.
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        row = res.data["rows"][0]
        self.assertEqual(row["gross_earned"], "6500.00")
        self.assertEqual(row["pf"], "0")
        self.assertEqual(row["net"], "6500.00")
        self.assertEqual(row["monthly_salary"], "650.00")   # the per-day rate


class PayslipPdfTests(APITestCase):
    """payslip_pdf's ownership/role gate (security review 2026-07) and the
    my_payslips self-service endpoint it shares a permission bypass with."""

    def setUp(self):
        self.hr = User.objects.create_user("hrpdf", password="x", role=ROLE_HR)
        self.ceo = User.objects.create_user("ceopdf", password="x", role=ROLE_CEO)
        self.admin = User.objects.create_user("adminpdf", password="x", role=ROLE_ADMIN)
        self.emp = Employee.objects.create(
            name="Kavya", department="Housekeeping", role="HK Supervisor",
            monthly_salary=Decimal("30000"))
        self.other_emp = Employee.objects.create(
            name="Other", department="Housekeeping", role="HK Supervisor",
            monthly_salary=Decimal("25000"))
        for e in (self.emp, self.other_emp):
            for d in range(1, 11):
                Attendance.objects.create(employee=e, date=date(YEAR, 6, d), status=Attendance.PRESENT)
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")
        rows = self.client.get(f"/api/hr/payroll/?month={MONTH}").data["rows"]
        self.slip = next(r["payslip"] for r in rows if r["id"] == self.emp.id)

    def test_payroll_manager_can_download_any_payslip(self):
        self.client.force_authenticate(self.hr)
        res = self.client.get(f"/api/hr/payslip_pdf/?payslip={self.slip}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")

    def test_admin_blocked_from_others_payslip(self):
        # Regression test: Admin holds "employees" for provisioning only, not
        # payroll — it must not be able to fetch a payslip by id.
        self.client.force_authenticate(self.admin)
        res = self.client.get(f"/api/hr/payslip_pdf/?payslip={self.slip}")
        self.assertEqual(res.status_code, 403)

    def test_ceo_can_view_any_payslip(self):
        self.client.force_authenticate(self.ceo)
        res = self.client.get(f"/api/hr/payslip_pdf/?payslip={self.slip}")
        self.assertEqual(res.status_code, 200)

    def test_employee_can_view_own_payslip(self):
        user = User.objects.create_user("kavya", password="x", role="Housekeeping")
        self.emp.user = user
        self.emp.save(update_fields=["user"])
        self.client.force_authenticate(user)
        res = self.client.get(f"/api/hr/payslip_pdf/?payslip={self.slip}")
        self.assertEqual(res.status_code, 200)

    def test_employee_blocked_from_others_payslip(self):
        user = User.objects.create_user("kavya2", password="x", role="Housekeeping")
        self.emp.user = user
        self.emp.save(update_fields=["user"])
        other_slip = next(r["payslip"] for r in
                          self.client_get_rows() if r["id"] == self.other_emp.id)
        self.client.force_authenticate(user)
        res = self.client.get(f"/api/hr/payslip_pdf/?payslip={other_slip}")
        self.assertEqual(res.status_code, 403)

    def client_get_rows(self):
        self.client.force_authenticate(self.hr)
        return self.client.get(f"/api/hr/payroll/?month={MONTH}").data["rows"]

    def test_payslip_pdf_404_for_unknown_id(self):
        self.client.force_authenticate(self.hr)
        res = self.client.get("/api/hr/payslip_pdf/?payslip=999999")
        self.assertEqual(res.status_code, 404)

    def test_my_payslips_lists_own_finalized_and_paid_runs(self):
        user = User.objects.create_user("kavya3", password="x", role="Housekeeping")
        self.emp.user = user
        self.emp.save(update_fields=["user"])
        self.client.force_authenticate(user)
        # Still a draft — not shown yet.
        self.assertEqual(self.client.get("/api/hr/my_payslips/").data, [])
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")  # finalize
        self.client.force_authenticate(user)
        rows = self.client.get("/api/hr/my_payslips/").data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["month"], MONTH)

    def test_my_payslips_without_linked_employee_record(self):
        user = User.objects.create_user("nolink", password="x", role="Housekeeping")
        self.client.force_authenticate(user)
        res = self.client.get("/api/hr/my_payslips/")
        self.assertIn("detail", res.data)
