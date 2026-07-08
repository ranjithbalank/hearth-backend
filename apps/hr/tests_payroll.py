"""Payroll (FR-HRM): salary split + statutory deductions, run lifecycle,
snapshot immutability, draft adjustments and role gating."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.constants import ROLE_CEO, ROLE_GM, ROLE_HR
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
