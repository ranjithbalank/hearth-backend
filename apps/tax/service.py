"""Central GST service (BRD 5.23 / FR-TAX). All money in INR.

Slabs (defaults): rooms 12% or 18% (by tariff threshold), F&B 5%, banquets 18%.
GST splits into CGST + SGST (intra-state) — each half the total rate.
"""
from decimal import ROUND_HALF_UP, Decimal

Q = Decimal("0.01")

FNB_RATE = Decimal("5")
BANQUET_RATE = Decimal("18")
ROOM_RATE_LOW = Decimal("12")
ROOM_RATE_HIGH = Decimal("18")
# Room tariffs at/above this (per night, INR) attract the higher slab.
ROOM_HIGH_THRESHOLD = Decimal("7500")


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def room_rate_for(tariff) -> Decimal:
    return ROOM_RATE_HIGH if _d(tariff) >= ROOM_HIGH_THRESHOLD else ROOM_RATE_LOW


def compute(amount, rate, inclusive: bool = False) -> dict:
    """Return a GST breakdown for ``amount`` at ``rate`` percent.

    exclusive: amount is the taxable base; tax is added on top.
    inclusive: amount already contains the tax; back it out.
    """
    amount = _d(amount)
    rate = _d(rate)
    if inclusive:
        taxable = (amount * Decimal(100) / (Decimal(100) + rate)).quantize(Q, ROUND_HALF_UP)
        tax = (amount - taxable).quantize(Q, ROUND_HALF_UP)
        total = amount.quantize(Q, ROUND_HALF_UP)
    else:
        taxable = amount.quantize(Q, ROUND_HALF_UP)
        tax = (amount * rate / Decimal(100)).quantize(Q, ROUND_HALF_UP)
        total = (taxable + tax).quantize(Q, ROUND_HALF_UP)
    half = (tax / Decimal(2)).quantize(Q, ROUND_HALF_UP)
    cgst = half
    sgst = (tax - half).quantize(Q, ROUND_HALF_UP)  # keep cgst+sgst == tax exactly
    return {
        "taxable": taxable,
        "rate": rate,
        "cgst": cgst,
        "sgst": sgst,
        "tax": tax,
        "total": total,
    }
