"""Salary advances & loans (FR-HRM): issuing, automatic payroll recovery,
settlement, discard-safety and write-off."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.constants import ROLE_CEO, ROLE_HOUSEKEEPING, ROLE_HR
from .models import Attendance, Employee, PayrollRun, SalaryAdvance

User = get_user_model()

YEAR = date.today().year
MONTH = f"{YEAR}-06"
DAYS = 30


class SalaryAdvanceTests(APITestCase):
    def setUp(self):
        self.hr = User.objects.create_user("hruser", password="x", role=ROLE_HR)
        self.ceo = User.objects.create_user("ceouser", password="x", role=ROLE_CEO)
        self.hk = User.objects.create_user("hkuser", password="x", role=ROLE_HOUSEKEEPING)
        self.emp = Employee.objects.create(
            name="Sunita", department="Housekeeping", role="HK Supervisor",
            monthly_salary=Decimal("30000"))
        for d in range(1, 31):
            Attendance.objects.create(employee=self.emp, date=date(YEAR, 6, d),
                                      status=Attendance.PRESENT)

    def _issue(self, user=None, **kw):
        self.client.force_authenticate(user or self.hr)
        body = {"employee": self.emp.id, "kind": "advance", "amount": "5000"}
        body.update(kw)
        return self.client.post("/api/hr-advances/", body, format="json")

    def test_issue_gated_to_payroll_managers(self):
        res = self._issue(self.hk)
        self.assertEqual(res.status_code, 403)
        res = self._issue(self.ceo)
        self.assertEqual(res.status_code, 403)
        res = self._issue(self.hr)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["balance"], "5000.00")

    def test_loan_requires_installment(self):
        res = self._issue(kind="loan", amount="12000")
        self.assertEqual(res.status_code, 400)
        res = self._issue(kind="loan", amount="12000", monthly_installment="2000")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["monthly_installment"], "2000.00")

    def test_advance_recovered_in_full_next_payroll(self):
        self._issue(amount="5000")
        self.client.force_authenticate(self.hr)
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        row = res.data["rows"][0]
        # 30k gross, deductions per compute_payslip: pf 1800, esi 0, pt 200
        # -> earned net 28000; advance takes the full 5000.
        self.assertEqual(row["advance_recovery"], "5000.00")
        self.assertEqual(row["net"], "23000.00")

    def test_loan_recovers_installment_and_settles_over_months(self):
        self._issue(kind="loan", amount="6000", monthly_installment="2500")
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        self.assertEqual(res.data["rows"][0]["advance_recovery"], "2500.00")
        # Still just planned — nothing applied until the run is paid.
        adv = SalaryAdvance.objects.get(employee=self.emp)
        self.assertEqual(adv.recovered, Decimal("0"))
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")  # -> paid
        adv.refresh_from_db()
        self.assertEqual(adv.recovered, Decimal("2500.00"))
        self.assertEqual(adv.status, SalaryAdvance.ACTIVE)   # 3500 still owed

    def test_discarded_draft_never_applies_recovery(self):
        self._issue(amount="5000")
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")
        self.client.post("/api/hr/advance_payroll/",
                         {"month": MONTH, "action": "discard"}, format="json")
        adv = SalaryAdvance.objects.get(employee=self.emp)
        self.assertEqual(adv.recovered, Decimal("0"))
        self.assertEqual(adv.balance, Decimal("5000.00"))

    def test_recovery_never_exceeds_net_and_queues_across_months(self):
        # An advance bigger than one month's net pay only takes what's
        # available; the rest is planned into the following month.
        self._issue(amount="40000")   # far more than ~28,000 net
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        row = res.data["rows"][0]
        self.assertEqual(row["net"], "0.00")
        self.assertEqual(row["advance_recovery"], "28000.00")
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        adv = SalaryAdvance.objects.get(employee=self.emp)
        self.assertEqual(adv.balance, Decimal("12000.00"))
        self.assertEqual(adv.status, SalaryAdvance.ACTIVE)

    def test_settles_and_stops_recovering(self):
        self._issue(amount="1000")
        self.client.force_authenticate(self.hr)
        self.client.post("/api/hr/run_payroll/", {"month": MONTH}, format="json")
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        self.client.post("/api/hr/advance_payroll/", {"month": MONTH}, format="json")
        adv = SalaryAdvance.objects.get(employee=self.emp)
        self.assertEqual(adv.status, SalaryAdvance.SETTLED)
        # Next month: nothing left to recover.
        next_month = f"{YEAR}-07"
        for d in range(1, 32):
            Attendance.objects.create(employee=self.emp, date=date(YEAR, 7, d),
                                      status=Attendance.PRESENT)
        res = self.client.get(f"/api/hr/payroll/?month={next_month}")
        self.assertEqual(res.data["rows"][0]["advance_recovery"], "0")

    def test_waive_writes_off_balance(self):
        res = self._issue(amount="5000")
        aid = res.data["id"]
        self.client.force_authenticate(self.hk)
        res = self.client.post(f"/api/hr-advances/{aid}/waive/", format="json")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.hr)
        res = self.client.post(f"/api/hr-advances/{aid}/waive/", format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "settled")
        # Waived — no longer planned into payroll.
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        self.assertEqual(res.data["rows"][0]["advance_recovery"], "0")

    def test_oldest_advance_recovered_first(self):
        self._issue(amount="3000")
        self._issue(amount="4000")
        self.client.force_authenticate(self.hr)
        res = self.client.get(f"/api/hr/payroll/?month={MONTH}")
        # 28,000 net comfortably covers both in full, oldest first.
        self.assertEqual(res.data["rows"][0]["advance_recovery"], "7000.00")
