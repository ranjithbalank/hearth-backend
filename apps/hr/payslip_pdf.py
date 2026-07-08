"""Payslip PDF (FR-HRM payroll) with ReportLab — same house style as the
POS bill and folio invoice."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PINE = colors.HexColor("#1C6B57")
MUTED = colors.HexColor("#8A8478")
CREAM = colors.HexColor("#F6F2EC")
LINE = colors.HexColor("#E5E0D8")


def _m(v):
    return f"INR {Decimal(str(v)):,.2f}"


def build_payslip_pdf(slip, property_name):
    """One employee's slip for one run — earnings, deductions, net."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A5, topMargin=12 * mm, bottomMargin=12 * mm,
        leftMargin=14 * mm, rightMargin=14 * mm,
        title=f"Payslip {slip.employee.name} {slip.run.month}")
    ss = getSampleStyleSheet()
    brand = ParagraphStyle("b", parent=ss["Normal"], fontSize=14, textColor=PINE)
    h = ParagraphStyle("h", parent=ss["Normal"], fontSize=10, spaceBefore=6)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8, textColor=MUTED)

    e = slip.employee
    wage = (f"Daily wage — {_m(slip.gross_salary)}/day" if slip.wage_type == "daily"
            else f"Monthly gross — {_m(slip.gross_salary)}")
    rolls = "On rolls (PF/ESI/PT)" if slip.statutory else "Off rolls — no statutory deductions"

    meta = Table([
        ["Employee", e.name, "Month", slip.run.month],
        ["Department", f"{e.department} · {e.role}", "Days paid",
         f"{slip.payable_days} / {slip.days_in_month}"],
        ["Pay terms", wage, "Status", rolls],
    ], colWidths=[22 * mm, 48 * mm, 20 * mm, 30 * mm])
    meta.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED), ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))

    earnings = [("Basic", slip.basic)]
    if slip.hra or slip.other_allowance:
        earnings += [("House rent allowance", slip.hra), ("Other allowances", slip.other_allowance)]
    deductions = [(label, amt) for label, amt in
                  [("Provident fund (PF)", slip.pf), ("ESI", slip.esi),
                   ("Professional tax", slip.pt),
                   ("Advance / loan recovery", slip.advance_recovery)] if amt]
    rows = [["Earnings", "", "Deductions", ""]]
    for i in range(max(len(earnings), len(deductions), 1)):
        left = earnings[i] if i < len(earnings) else ("", "")
        right = deductions[i] if i < len(deductions) else ("", "")
        rows.append([left[0], _m(left[1]) if left[0] else "",
                     right[0], _m(right[1]) if right[0] else ""])
    total_ded = slip.pf + slip.esi + slip.pt + slip.advance_recovery
    rows.append(["Gross earned", _m(slip.gross_earned), "Total deductions", _m(total_ded)])
    if slip.adjustment:
        rows.append([f"Adjustment — {slip.adjustment_note or 'manual'}", _m(slip.adjustment), "", ""])
    money = Table(rows, colWidths=[38 * mm, 24 * mm, 36 * mm, 22 * mm])
    money.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), CREAM),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, LINE),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))

    net = Table([[f"NET PAY — {slip.run.month}", _m(slip.net)]], colWidths=[80 * mm, 40 * mm])
    net.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PINE),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))

    status = slip.run.status
    stamp = {"draft": "DRAFT — not finalized", "finalized": "Finalized",
             "paid": f"Paid{' by ' + slip.run.paid_by if slip.run.paid_by else ''}"}.get(status, status)
    story = [
        Paragraph(property_name, brand),
        Paragraph(f"Payslip · {slip.run.month}", h),
        Spacer(1, 4), HRFlowable(width="100%", color=LINE, thickness=0.7), Spacer(1, 6),
        meta, Spacer(1, 8), money, Spacer(1, 10), net, Spacer(1, 8),
        Paragraph(f"{stamp} · computer-generated payslip, no signature required.", small),
    ]
    doc.build(story)
    buf.seek(0)
    return buf
