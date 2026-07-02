"""GST tax-invoice PDF generation with ReportLab (BRD FR-TAX-003)."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.platypus.paragraph import Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

PINE = colors.HexColor("#1C6B57")
INK = colors.HexColor("#16221F")
MUTED = colors.HexColor("#8A8478")
CREAM = colors.HexColor("#F6F2EC")


def _money(v):
    return "INR " + f"{Decimal(str(v)):,.2f}"


def build_invoice_pdf(folio, property_name, gstin, address="", with_gst=True):
    """with_gst=True → GST tax invoice; False → bill of supply (no tax columns)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm, title=f"Invoice {folio.invoice_no or folio.id}")
    ss = getSampleStyleSheet()
    h_brand = ParagraphStyle("brand", parent=ss["Title"], textColor=PINE, fontSize=20, spaceAfter=0)
    h_doc = ParagraphStyle("doc", parent=ss["Normal"], alignment=2, fontSize=13, textColor=INK, leading=16)
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=9, textColor=MUTED)
    normal = ParagraphStyle("n", parent=ss["Normal"], fontSize=10.5, textColor=INK)
    story = []
    doc_title = "TAX INVOICE" if with_gst else "BILL OF SUPPLY"

    # Header
    header = Table([[
        Paragraph(f"{property_name}<br/>"
                  f"<font size=8 color='#8A8478'>{address + '<br/>' if address else ''}"
                  f"{'GSTIN: ' + gstin if gstin and with_gst else ''}</font>", h_brand),
        Paragraph(f"<b>{doc_title}</b><br/><font size=9 color='#8A8478'>No. {folio.invoice_no or '—'}<br/>"
                  f"{folio.opened_at:%d %b %Y}</font>", h_doc),
    ]], colWidths=[100 * mm, 78 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 4), HRFlowable(width="100%", thickness=2, color=PINE), Spacer(1, 8)]

    room = f" &nbsp;·&nbsp; Room {folio.room.number}" if folio.room else ""
    if folio.company_name:
        # Corporate: the company is billed; the guest is the occupant.
        story += [Paragraph(f"<b>Bill to:</b> {folio.company_name} <font size=8 color='#8A8478'>(bill-to-company)</font>", normal),
                  Paragraph(f"<font size=9 color='#8A8478'>Guest: {folio.guest_name}{room}</font>", small), Spacer(1, 8)]
    else:
        story += [Paragraph(f"<b>Bill to:</b> {folio.guest_name}{room}", normal), Spacer(1, 8)]

    # Line items — the bill of supply carries no tax columns.
    cgst = sgst = Decimal("0")
    if with_gst:
        rows = [["Description", "Taxable", "CGST", "SGST", "Amount"]]
        for l in folio.lines.all():
            rows.append([l.description, _money(l.taxable), _money(l.cgst), _money(l.sgst), _money(l.total)])
            cgst += l.cgst
            sgst += l.sgst
        col_widths = [74 * mm, 26 * mm, 24 * mm, 24 * mm, 30 * mm]
    else:
        rows = [["Description", "Amount"]]
        for l in folio.lines.all():
            rows.append([l.description, _money(l.total)])
        col_widths = [138 * mm, 40 * mm]
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CREAM),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#EDE7DC")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [tbl, Spacer(1, 10)]

    if with_gst:
        taxable = Decimal(str(folio.charges_total)) - cgst - sgst
        tot_rows = [
            ["Taxable", _money(taxable)],
            ["CGST", _money(cgst)],
            ["SGST", _money(sgst)],
            ["Total", _money(folio.charges_total)],
            ["Paid", _money(folio.paid_total)],
            ["Balance", _money(folio.balance)],
        ]
        total_idx = 3
    else:
        tot_rows = [
            ["Total", _money(folio.charges_total)],
            ["Paid", _money(folio.paid_total)],
            ["Balance", _money(folio.balance)],
        ]
        total_idx = 0
    tot = Table(tot_rows, colWidths=[40 * mm, 38 * mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LINEABOVE", (0, total_idx), (-1, total_idx), 1.4, PINE),
        ("FONTNAME", (0, total_idx), (-1, total_idx), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    footer = ("GST-compliant tax invoice" if with_gst
              else "bill of supply — GST not applicable")
    story += [tot, Spacer(1, 18),
              Paragraph(f"<font size=8 color='#B6AF9F'>{property_name} · {footer} · "
                        f"computer-generated, no signature required</font>",
                        ParagraphStyle("f", parent=ss["Normal"], alignment=1))]
    doc.build(story)
    buf.seek(0)
    return buf
