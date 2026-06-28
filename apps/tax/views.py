from collections import defaultdict
from decimal import Decimal

from django.http import HttpResponse
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import viewsets

from apps.accounts.permissions import ModuleViewSetMixin
from apps.frontoffice.models import FolioLine


class TaxViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """GST summaries and GSTR-1-style export (BRD 5.23, FR-TAX-005)."""

    module = "tax"

    def _summary_rows(self):
        rows = defaultdict(lambda: {"taxable": Decimal("0"), "cgst": Decimal("0"),
                                    "sgst": Decimal("0"), "total": Decimal("0")})
        for line in FolioLine.objects.exclude(kind=FolioLine.KIND_TAX):
            key = str(line.gst_rate)
            r = rows[key]
            r["taxable"] += line.taxable
            r["cgst"] += line.cgst
            r["sgst"] += line.sgst
            r["total"] += line.total
        return rows

    def list(self, request):
        rows = self._summary_rows()
        out = [
            {
                "rate": rate,
                "taxable": str(v["taxable"]),
                "cgst": str(v["cgst"]),
                "sgst": str(v["sgst"]),
                "tax": str(v["cgst"] + v["sgst"]),
                "total": str(v["total"]),
            }
            for rate, v in sorted(rows.items())
        ]
        return Response(out)

    @action(detail=False, methods=["get"])
    def gstr1(self, request):
        """Export the output-tax summary as CSV (GSTR-1 style)."""
        rows = self._summary_rows()
        lines = ["Rate%,Taxable,CGST,SGST,Total"]
        for rate, v in sorted(rows.items()):
            lines.append(
                f"{rate},{v['taxable']},{v['cgst']},{v['sgst']},{v['total']}"
            )
        resp = HttpResponse("\n".join(lines), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="gstr1.csv"'
        return resp
