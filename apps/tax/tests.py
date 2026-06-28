from decimal import Decimal

from django.test import TestCase

from . import service as tax


class GstServiceTests(TestCase):
    def test_exclusive_fnb_5pct(self):
        b = tax.compute(1000, 5)
        self.assertEqual(b["taxable"], Decimal("1000.00"))
        self.assertEqual(b["cgst"], Decimal("25.00"))
        self.assertEqual(b["sgst"], Decimal("25.00"))
        self.assertEqual(b["total"], Decimal("1050.00"))

    def test_cgst_sgst_sum_equals_tax(self):
        b = tax.compute(333.33, 18)
        self.assertEqual(b["cgst"] + b["sgst"], b["tax"])

    def test_inclusive_backs_out_tax(self):
        b = tax.compute(1050, 5, inclusive=True)
        self.assertEqual(b["taxable"], Decimal("1000.00"))
        self.assertEqual(b["tax"], Decimal("50.00"))
        self.assertEqual(b["total"], Decimal("1050.00"))

    def test_room_slab_by_threshold(self):
        self.assertEqual(tax.room_rate_for(4500), Decimal("12"))
        self.assertEqual(tax.room_rate_for(9500), Decimal("18"))
