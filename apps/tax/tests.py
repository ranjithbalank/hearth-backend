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


class Gstr1ExportTests(TestCase):
    """The GST summary and its GSTR-1 export are filed with the tax authority,
    so the arithmetic has to survive real folio data — not just the rounding
    cases the service-level tests cover."""

    def setUp(self):
        from rest_framework.test import APIClient

        from apps.accounts.models import User
        from apps.frontoffice.models import Folio, FolioLine
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(
            username="tax_fin", password="Tk9$mZ2pQw!7", role="Finance"))
        folio = Folio.objects.create(guest_name="GST Guest")
        # A room night at 12% and a meal at 5% — two slabs on one folio.
        FolioLine.objects.create(folio=folio, kind=FolioLine.KIND_ROOM,
                                 description="Room", taxable=Decimal("5000"),
                                 cgst=Decimal("300"), sgst=Decimal("300"),
                                 total=Decimal("5600"), gst_rate=Decimal("12"))
        FolioLine.objects.create(folio=folio, kind=FolioLine.KIND_FNB,
                                 description="Dinner", taxable=Decimal("1000"),
                                 cgst=Decimal("25"), sgst=Decimal("25"),
                                 total=Decimal("1050"), gst_rate=Decimal("5"))
        # A posted TAX line must never be counted as its own taxable supply —
        # that would tax the tax and inflate the return.
        FolioLine.objects.create(folio=folio, kind=FolioLine.KIND_TAX,
                                 description="GST", taxable=Decimal("650"),
                                 cgst=Decimal("0"), sgst=Decimal("0"),
                                 total=Decimal("650"), gst_rate=Decimal("0"))

    def _by_slab(self):
        """Keyed on the decimal value, not its string form — the field's
        precision is a schema detail these assertions should not depend on."""
        return {Decimal(r["rate"]): r for r in self.client.get("/api/tax/").data}

    def test_summary_groups_by_slab(self):
        rows = self._by_slab()
        self.assertEqual(Decimal(rows[Decimal("12")]["taxable"]), Decimal("5000"))
        self.assertEqual(Decimal(rows[Decimal("5")]["taxable"]), Decimal("1000"))

    def test_tax_lines_are_excluded_from_the_taxable_base(self):
        self.assertNotIn(Decimal("0"), self._by_slab(),
                         "a posted GST line was counted as a supply")

    def test_cgst_and_sgst_always_sum_to_the_reported_tax(self):
        for row in self.client.get("/api/tax/").data:
            self.assertEqual(
                Decimal(row["cgst"]) + Decimal(row["sgst"]), Decimal(row["tax"]), row)

    def test_gstr1_downloads_as_csv_with_a_row_per_slab(self):
        r = self.client.get("/api/tax/gstr1/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("gstr1.csv", r["Content-Disposition"])
        body = r.content.decode().splitlines()
        self.assertEqual(body[0], "Rate%,Taxable,CGST,SGST,Total")
        self.assertEqual(len(body), 3)   # header + the 5% and 12% slabs
        self.assertTrue(any(l.startswith("12.0,5000") for l in body[1:]), body)

    def test_the_export_and_the_on_screen_summary_agree(self):
        """They are two renderings of one computation; if they ever diverge,
        the figure that was checked on screen is not the one that was filed."""
        screen = self._by_slab()
        for line in self.client.get("/api/tax/gstr1/").content.decode().splitlines()[1:]:
            rate, taxable, cgst, sgst, total = line.split(",")
            row = screen[Decimal(rate)]
            self.assertEqual(Decimal(taxable), Decimal(row["taxable"]))
            self.assertEqual(Decimal(cgst), Decimal(row["cgst"]))
            self.assertEqual(Decimal(total), Decimal(row["total"]))
