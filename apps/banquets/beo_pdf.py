"""Banquet Event Order (BEO) sheet PDF (BRD FR-BQT-004) with ReportLab."""
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
CLAY = colors.HexColor("#DB7B4B")
MUTED = colors.HexColor("#8A8478")
HAIR = colors.HexColor("#EDE7DC")


def _m(v):
    return "INR " + f"{Decimal(str(v)):,.2f}"


def build_beo_pdf(event, property_name):
    ref = event.beo_no or f"BEO-{event.id}"
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=16 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm, title=ref)
    ss = getSampleStyleSheet()
    brand = ParagraphStyle("b", parent=ss["Title"], fontSize=20, textColor=PINE, spaceAfter=0)
    docr = ParagraphStyle("d", parent=ss["Normal"], alignment=2, fontSize=13)
    lbl = ParagraphStyle("l", parent=ss["Normal"], fontSize=8, textColor=MUTED)
    story = []
    header = Table([[
        Paragraph(f"{property_name}<br/><font size=8 color='#8A8478'>Banquet &amp; Events</font>", brand),
        Paragraph(f"<b>BANQUET EVENT ORDER</b><br/><font size=9 color='#8A8478'>{ref} · "
                  f"{event.status.upper()}</font>", docr),
    ]], colWidths=[100 * mm, 78 * mm])
    story += [header, Spacer(1, 4), HRFlowable(width="100%", thickness=2, color=PINE), Spacer(1, 10)]

    def card(title, pairs):
        rows = [[Paragraph(f"<font size=8 color='#8A8478'>{k.upper()}</font>", lbl),
                 Paragraph(f"<b>{v or '—'}</b>", ss["Normal"])] for k, v in pairs]
        t = Table([[Paragraph(f"<font size=8 color='#8A8478'>{title.upper()}</font>", lbl)]] +
                  [[Table(rows, colWidths=[34 * mm, 46 * mm])]], colWidths=[84 * mm])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, HAIR), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        return t

    _time = (f"{event.start_time.strftime('%H:%M')}–{event.end_time.strftime('%H:%M')}"
             if event.start_time and event.end_time
             else (event.start_time.strftime('%H:%M') if event.start_time else "—"))
    ev = card("Event", [("Title", event.title), ("Type", event.event_type),
                        ("Date", str(event.event_date)), ("Time", _time),
                        ("Space", event.space.name), ("Covers", str(event.covers))])
    cl = card("Client", [("Host", event.host), ("Contact", event.contact)])
    cat_rows = [("Food plates", str(event.food_covers) if event.food_covers else "—"),
                ("Preference", (event.food_pref or "—").upper())]
    if event.food_veg and event.veg_rate:
        cat_rows.append((f"Veg — {event.food_veg} × {_m(event.veg_rate)}", _m(event.food_veg * event.veg_rate)))
    if event.food_nonveg and event.nonveg_rate:
        cat_rows.append((f"Non-veg — {event.food_nonveg} × {_m(event.nonveg_rate)}", _m(event.food_nonveg * event.nonveg_rate)))
    cat_rows.append(("Kitchen prep", (event.beo_status or "—").upper()))
    cat = card("Catering (F&B)", cat_rows)

    subtotal = Decimal(str(event.bill_subtotal))
    gst = (subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
    total = subtotal + gst
    balance = total - Decimal(str(event.deposit))
    fin = card("Financials", [("Package", _m(event.package_amount)),
                              ("Catering", _m(event.catering_amount)),
                              ("Subtotal", _m(subtotal)), ("GST 18%", _m(gst)),
                              ("Total", _m(total)), ("Deposit", _m(event.deposit)),
                              ("Balance", _m(balance))])
    grid = Table([[ev, cl], [cat, fin]], colWidths=[88 * mm, 88 * mm])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [grid, Spacer(1, 12)]

    for section in ["Service timeline (setup · arrival · service · close)",
                    "Hall setup & layout (seating · stage · AV · décor)",
                    "Special instructions"]:
        story += [Paragraph(f"<font size=8 color='#8A8478'>{section.upper()}</font>", lbl)]
        box = Table([[""]], colWidths=[178 * mm], rowHeights=[18 * mm])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DDD5C7"))]))
        story += [box, Spacer(1, 8)]

    sign = Table([["Sales / Banquet Sales", "Banquet Manager", "Executive Chef"]], colWidths=[59 * mm] * 3)
    sign.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#C9C1B2")),
                              ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                              ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, -1), MUTED)]))
    story += [Spacer(1, 16), sign]
    doc.build(story)
    buf.seek(0)
    return buf
