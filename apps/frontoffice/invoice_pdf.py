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


def _logo_flowable(logo_data_url):
    """Company logo from the property's stored data URL (letterhead)."""
    import base64

    from reportlab.platypus import Image
    try:
        header, b64 = logo_data_url.split(",", 1)
        img = Image(io.BytesIO(base64.b64decode(b64)))
        # Scale to letterhead size, preserving aspect.
        scale = min(20 * mm / img.imageHeight, 46 * mm / img.imageWidth)
        img.drawWidth = img.imageWidth * scale
        img.drawHeight = img.imageHeight * scale
        img.hAlign = "LEFT"
        return img
    except Exception:
        return None


_ALIGN = {"left": 0, "center": 1, "right": 2}


def build_invoice_pdf(folio, property_name, gstin, address="", with_gst=True,
                      logo="", doc_header="", doc_footer="",
                      doc_header_align="left", doc_footer_align="center",
                      columns=()):
    """with_gst=True → GST tax invoice; False → bill of supply (no tax columns).
    logo/doc_header/doc_footer/alignment come from Settings → Letterhead.
    columns: optional extra line-item columns from Settings → Bill Template
    (⊆ "type", "gst_rate") — additive only; the statutory GST columns below
    are never hidden (BRD FR-TAX-003)."""
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

    # Letterhead: logo + name/address/GSTIN + custom header lines (Settings).
    brand_cell = [Paragraph(f"{property_name}<br/>"
                            f"<font size=8 color='#8A8478'>{address + '<br/>' if address else ''}"
                            f"{'GSTIN: ' + gstin if gstin and with_gst else ''}"
                            f"</font>", h_brand)]
    # Custom header lines get their own alignment (left/center/right).
    header_lines = [ln.strip() for ln in (doc_header or "").splitlines() if ln.strip()]
    if header_lines:
        h_extra = ParagraphStyle("hx", parent=small, alignment=_ALIGN.get(doc_header_align, 0))
        brand_cell.append(Paragraph("<br/>".join(header_lines), h_extra))
    logo_img = _logo_flowable(logo) if logo else None
    if logo_img:
        brand_cell.insert(0, logo_img)
    header = Table([[
        brand_cell,
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

    # Line items — the bill of supply carries no tax columns. "type"/"gst_rate"
    # are optional extra columns (Settings → Bill Template); the statutory
    # columns (Description/Taxable/CGST/SGST/Amount) are always present.
    show_type = "type" in columns
    show_rate = with_gst and "gst_rate" in columns
    cgst = sgst = Decimal("0")
    if with_gst:
        head = ["Description"]
        if show_type:
            head.append("Type")
        if show_rate:
            head.append("GST %")
        head += ["Taxable", "CGST", "SGST", "Amount"]
        rows = [head]
        for l in folio.lines.all():
            row = [l.description]
            if show_type:
                row.append(l.get_kind_display())
            if show_rate:
                row.append(f"{l.gst_rate}%")
            row += [_money(l.taxable), _money(l.cgst), _money(l.sgst), _money(l.total)]
            rows.append(row)
            cgst += l.cgst
            sgst += l.sgst
        # Widths in header order: Description, [Type], [GST %], Taxable, CGST, SGST, Amount.
        desc_width = 74 * mm
        if show_type:
            desc_width -= 16 * mm
        if show_rate:
            desc_width -= 16 * mm
        col_widths = [desc_width]
        if show_type:
            col_widths.append(16 * mm)
        if show_rate:
            col_widths.append(16 * mm)
        col_widths += [26 * mm, 24 * mm, 24 * mm, 30 * mm]
    else:
        head = ["Description"]
        if show_type:
            head.append("Type")
        head.append("Amount")
        rows = [head]
        for l in folio.lines.all():
            row = [l.description]
            if show_type:
                row.append(l.get_kind_display())
            row.append(_money(l.total))
            rows.append(row)
        col_widths = [98 * mm, 40 * mm, 40 * mm] if show_type else [138 * mm, 40 * mm]
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
    story += [tot, Spacer(1, 14)]
    # Custom terms & conditions / bank details footer (Settings → Letterhead).
    if doc_footer:
        f_style = ParagraphStyle("fx", parent=small, alignment=_ALIGN.get(doc_footer_align, 1))
        for ln in doc_footer.splitlines():
            if ln.strip():
                story.append(Paragraph(f"<font size=8 color='#8A8478'>{ln.strip()}</font>", f_style))
        story.append(Spacer(1, 8))
    footer = ("GST-compliant tax invoice" if with_gst
              else "bill of supply — GST not applicable")
    story += [Paragraph(f"<font size=8 color='#B6AF9F'>{property_name} · {footer} · "
                        f"computer-generated, no signature required</font>",
                        ParagraphStyle("f", parent=ss["Normal"], alignment=1))]
    doc.build(story)
    buf.seek(0)
    return buf
