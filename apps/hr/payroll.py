"""Payroll maths (FR-HRM payroll): salary split + Indian statutory deductions.

Two kinds of pay terms (Employee.wage_type):

- monthly — `monthly_salary` is the contracted GROSS, prorated by payable
  days from attendance (present/paid-leave = 1, half = 0.5, absent/unmarked
  = 0 — the muster roll's usual weights). With allowances
  (Employee.has_allowances) it's split into the standard structure (basic
  50%, HRA 20%, other allowances 30%); without, the whole gross is basic —
  which raises the PF base until the cap.
- daily — casual labour: earned = `daily_rate` × payable days, no split
  (it's all basic).

Statutory deductions apply only to staff on the rolls (Employee.statutory
— daily-wage casuals are typically outside them):

- PF  — employee share, 12% of earned basic, capped at ₹1,800/month
        (the ₹15,000 statutory wage ceiling).
- ESI — employee share, 0.75% of earned gross, while within the ₹21,000
        eligibility ceiling (contracted gross for monthly staff, earned
        gross for daily-rated).
- PT  — professional tax, flat ₹200 for months where earned gross crosses
        ₹21,000 (simplified single slab; state-specific slabs can replace it).
"""
from decimal import Decimal

TWO = Decimal("0.01")

BASIC_PCT = Decimal("0.50")
HRA_PCT = Decimal("0.20")

PF_RATE = Decimal("0.12")
PF_MONTHLY_CAP = Decimal("1800")          # 12% of the ₹15,000 ceiling
ESI_RATE = Decimal("0.0075")
ESI_GROSS_CEILING = Decimal("21000")      # eligibility on contracted gross
PT_THRESHOLD = Decimal("21000")
PT_AMOUNT = Decimal("200")


def compute_payslip(employee, payable_days, days_in_month):
    """All the money lines for one employee-month, as quantized Decimals.

    `employee` needs wage_type / monthly_salary / daily_rate / statutory —
    the Employee row itself, or a snapshot object shaped like one.
    """
    payable_days = Decimal(payable_days)
    if employee.wage_type == "daily":
        gross_earned = (Decimal(employee.daily_rate or 0) * payable_days).quantize(TWO)
        basic, hra, other = gross_earned, Decimal("0.00"), Decimal("0.00")
        esi_eligible = gross_earned <= ESI_GROSS_CEILING
    else:
        gross_salary = Decimal(employee.monthly_salary or 0)
        factor = payable_days / Decimal(days_in_month)
        gross_earned = (gross_salary * factor).quantize(TWO)
        if getattr(employee, "has_allowances", True):
            basic = (gross_salary * BASIC_PCT * factor).quantize(TWO)
            hra = (gross_salary * HRA_PCT * factor).quantize(TWO)
            other = gross_earned - basic - hra
        else:
            basic, hra, other = gross_earned, Decimal("0.00"), Decimal("0.00")
        esi_eligible = gross_salary <= ESI_GROSS_CEILING
    if employee.statutory:
        pf = min((basic * PF_RATE).quantize(TWO), PF_MONTHLY_CAP)
        esi = (gross_earned * ESI_RATE).quantize(TWO) if esi_eligible else Decimal("0")
        pt = PT_AMOUNT if gross_earned > PT_THRESHOLD else Decimal("0")
    else:
        pf = esi = pt = Decimal("0")
    return {
        "basic": basic, "hra": hra, "other_allowance": other,
        "gross_earned": gross_earned,
        "pf": pf, "esi": esi, "pt": pt,
        "net": gross_earned - pf - esi - pt,
    }
