"""Logic-layer audit — money written through raw ORM paths.

The non-negative rules added to the models in the Aug-2026 field sweep only
fire through a serializer. Several money-writing endpoints build kwargs and
call .objects.create() directly, which bypasses them entirely — these probe
the ones that handle real amounts.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.masters.models import Department, Designation

from .models import Event, FunctionSpace


class BanquetMoneyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.mgr = User.objects.create_user(
            username="banqaudit", password="Tk9$mZ2pQw!7", role="General Manager")
        self.client.force_authenticate(self.mgr)
        self.space = FunctionSpace.objects.create(name="Audit Hall", capacity=200)

    def _book(self, **over):
        body = {"space": self.space.id, "title": "Audit Event", "host": "Ravi Kumar",
                "event_date": str(date.today() + timedelta(days=10)),
                "covers": 100, "package_amount": "50000", "deposit": "10000"}
        body.update(over)
        return self.client.post("/api/banquets/", body, format="json")

    def test_a_banquet_package_cannot_be_negative(self):
        r = self._book(package_amount="-50000")
        self.assertEqual(r.status_code, 400,
                         "a negative contract value was booked")
        self.assertFalse(Event.objects.filter(package_amount__lt=0).exists())

    def test_a_banquet_deposit_cannot_be_negative(self):
        r = self._book(deposit="-1000")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Event.objects.filter(deposit__lt=0).exists())

    def test_a_deposit_cannot_exceed_the_package(self):
        """Taking more deposit than the event is worth leaves the property
        owing the customer money on a contract it hasn't delivered yet."""
        r = self._book(package_amount="10000", deposit="50000")
        self.assertEqual(r.status_code, 400)

    def test_a_normal_booking_still_saves(self):
        self.assertEqual(self._book().status_code, 201)


class AdvanceMoneyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.hr = User.objects.create_user(
            username="advaudit", password="Tk9$mZ2pQw!7", role="HR Manager")
        self.client.force_authenticate(self.hr)
        Department.objects.get_or_create(name="Adv Dept", defaults={"active": True})
        Designation.objects.get_or_create(name="Adv Role", defaults={"active": True})
        from apps.hr.models import Employee
        self.emp = Employee.objects.create(name="Advance Taker", department="Adv Dept",
                                           role="Adv Role", monthly_salary=Decimal("30000"))

    def test_a_negative_advance_is_refused(self):
        """A negative advance is a deduction from someone's pay dressed up as
        a loan to them."""
        from apps.hr.models import SalaryAdvance
        r = self.client.post("/api/hr-advances/", {
            "employee": self.emp.id, "kind": "advance", "amount": "-5000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(SalaryAdvance.objects.filter(amount__lt=0).exists())

    def test_a_zero_advance_is_refused(self):
        r = self.client.post("/api/hr-advances/", {
            "employee": self.emp.id, "kind": "advance", "amount": "0"}, format="json")
        self.assertEqual(r.status_code, 400)
