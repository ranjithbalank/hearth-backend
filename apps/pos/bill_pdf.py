"""POS bill/receipt PDF (BRD FR-POS-007) with ReportLab."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PINE = colors.HexColor("#1C6B57")
MUTED = colors.HexColor("#8A8478")
CREAM = colors.HexColor("#F6F2EC")


def _m(v):
    return "INR " + f"{Decimal(str(v)):,.2f}"


_ALIGN = {"left": 0, "center": 1, "right": 2}


def build_bill_pdf(order, property_name, doc_header="", doc_header_align="center",
                   doc_footer="", doc_footer_align="center", columns=()):
    """doc_header/doc_footer/alignment come from Settings → Bill Template — POS
    (its own template, separate from the guest invoice's).
    columns: optional extra line-item columns (⊆ "rate") — additive only,
    next to the existing Item/Qty/Amt columns."""
    buf = io.BytesIO()
    # A slim receipt page.
    doc = SimpleDocTemplate(buf, pagesize=(80 * mm, 200 * mm), topMargin=8 * mm,
                            bottomMargin=8 * mm, leftMargin=6 * mm, rightMargin=6 * mm,
                            title=f"Bill {order.bill_no or order.kot_no or order.id}")
    ss = getSampleStyleSheet()
    center = ParagraphStyle("c", parent=ss["Normal"], alignment=1)
    brand = ParagraphStyle("b", parent=center, fontSize=13, textColor=PINE, spaceAfter=2)
    small = ParagraphStyle("s", parent=center, fontSize=8, textColor=MUTED)
    t = order.totals()
    where = f"Table {order.table.name}" if order.table else order.get_mode_display()
    story = [
        Paragraph(property_name, brand),
        Paragraph(where, small),
        Paragraph(f"{order.bill_no or order.kot_no or ('#' + str(order.id))}", small),
    ]
    header_lines = [ln.strip() for ln in (doc_header or "").splitlines() if ln.strip()]
    if header_lines:
        h_style = ParagraphStyle("bh", parent=small, alignment=_ALIGN.get(doc_header_align, 1))
        story.append(Paragraph("<br/>".join(header_lines), h_style))
    story += [Spacer(1, 4), HRFlowable(width="100%", thickness=1, color=PINE), Spacer(1, 4)]
    show_rate = "rate" in columns
    head = ["Item", "Qty"]
    if show_rate:
        head.append("Rate")
    head.append("Amt")
    rows = [head]
    for l in order.lines.all():
        row = [l.display_name, str(l.qty)]
        if show_rate:
            row.append(_m(l.unit_price))
        row.append(_m(l.unit_price * l.qty))
        rows.append(row)
    col_widths = [30 * mm, 10 * mm, 14 * mm, 14 * mm] if show_rate else [38 * mm, 12 * mm, 18 * mm]
    tbl = Table(rows, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CREAM), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#EDE7DC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [tbl, Spacer(1, 6)]
    tot = [["Subtotal", _m(t["subtotal"])]]
    if t["discount"] > 0:
        tot.append(["Discount", "-" + _m(t["discount"])])
    tot += [["CGST", _m(t["cgst"])], ["SGST", _m(t["sgst"])], ["Total", _m(t["total"])]]
    tt = Table(tot, colWidths=[34 * mm, 34 * mm])
    tt.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEABOVE", (0, len(tot) - 1), (-1, len(tot) - 1), 1, PINE),
        ("FONTNAME", (0, len(tot) - 1), (-1, len(tot) - 1), "Helvetica-Bold"),
    ]))
    story += [tt, Spacer(1, 8)]
    # Pickup token for takeaway/delivery (token board).
    if order.token_no:
        story.append(Paragraph(f"Pickup token: <b>{order.token_no}</b>", center))
        story.append(Spacer(1, 4))
    # Feedback QR/link (guest rates via the public form).
    fb = getattr(order, "feedback", None)
    if fb:
        from django.conf import settings
        base = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173")
        story.append(Paragraph(f"Rate your experience: {base}/feedback?t={fb.token}", small))
        story.append(Spacer(1, 4))
    # POS bill footer (Settings → Bill Template — POS), e.g. FSSAI no. / thank-you line.
    if doc_footer:
        foot_style = ParagraphStyle("bf", parent=small, alignment=_ALIGN.get(doc_footer_align, 1))
        for ln in doc_footer.splitlines()[:3]:
            if ln.strip():
                story.append(Paragraph(ln.strip(), foot_style))
    story.append(Paragraph(f"Thank you · {property_name}", small))
    doc.build(story)
    buf.seek(0)
    return buf
