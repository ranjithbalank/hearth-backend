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


def build_bill_pdf(order, property_name):
    buf = io.BytesIO()
    # A slim receipt page.
    doc = SimpleDocTemplate(buf, pagesize=(80 * mm, 200 * mm), topMargin=8 * mm,
                            bottomMargin=8 * mm, leftMargin=6 * mm, rightMargin=6 * mm,
                            title=f"Bill {order.kot_no or order.id}")
    ss = getSampleStyleSheet()
    center = ParagraphStyle("c", parent=ss["Normal"], alignment=1)
    brand = ParagraphStyle("b", parent=center, fontSize=13, textColor=PINE, spaceAfter=2)
    small = ParagraphStyle("s", parent=center, fontSize=8, textColor=MUTED)
    t = order.totals()
    where = f"Table {order.table.name}" if order.table else order.get_mode_display()
    story = [
        Paragraph(property_name, brand),
        Paragraph(where, small),
        Paragraph(f"{order.kot_no or ('#' + str(order.id))}", small),
        Spacer(1, 4), HRFlowable(width="100%", thickness=1, color=PINE), Spacer(1, 4),
    ]
    rows = [["Item", "Qty", "Amt"]]
    for l in order.lines.all():
        rows.append([l.display_name, str(l.qty), _m(l.unit_price * l.qty)])
    tbl = Table(rows, colWidths=[38 * mm, 12 * mm, 18 * mm])
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
    story.append(Paragraph(f"Thank you · {property_name}", small))
    doc.build(story)
    buf.seek(0)
    return buf
