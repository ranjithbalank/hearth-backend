"""Front-office domain operations and cross-module seams.

These functions are the integration points referenced in the plan:
  - check_in  -> opens a folio, marks the room occupied, reservation in-house
  - post_charge -> generic folio posting (used by POS "post to room")
  - settle_folio / check_out -> multi-tender settle + room release
  - run_night_audit -> atomic, resumable day-end close
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import log_action
from apps.rooms.models import Room
from apps.tax import service as tax

from .models import Folio, FolioLine, NightAuditRun, Settlement


def _next_invoice_no():
    last = Folio.objects.exclude(invoice_no="").order_by("-id").first()
    seq = 1
    if last and last.invoice_no.startswith("HRT-"):
        try:
            seq = int(last.invoice_no.split("-")[-1]) + 1
        except ValueError:
            seq = Folio.objects.exclude(invoice_no="").count() + 1
    return f"HRT-{date.today():%Y%m}-{seq:05d}"


@transaction.atomic
def check_in(reservation, room, user=None):
    """Assign a room and open a folio (BRD FR-PMS-004)."""
    if hasattr(reservation, "folio") and reservation.folio:
        return reservation.folio
    reservation.room = room
    reservation.status = reservation.IN_HOUSE
    reservation.save(update_fields=["room", "status"])

    room.status = Room.OCCUPIED
    room.save(update_fields=["status", "updated_at"])

    folio = Folio.objects.create(
        reservation=reservation,
        guest_name=reservation.guest_name,
        room=room,
    )
    # Pre-load the OTA prepayment so the guest is not charged twice (persona Anil).
    if reservation.prepaid and reservation.deposit:
        Settlement.objects.create(
            folio=folio, tender="Prepaid", amount=reservation.deposit,
            reference="OTA prepayment",
        )
    log_action(user, "check_in", entity="Reservation", entity_id=reservation.id,
               after={"room": room.number, "folio": folio.id})
    return folio


def post_charge(folio, *, kind, description, amount, gst_rate, source="",
                inclusive=False, user=None):
    """Post a taxed charge line to a folio. Used by rooms, POS post-to-room, incidentals."""
    breakdown = tax.compute(amount, gst_rate, inclusive=inclusive)
    line = FolioLine.objects.create(
        folio=folio,
        kind=kind,
        description=description,
        source=source,
        taxable=breakdown["taxable"],
        cgst=breakdown["cgst"],
        sgst=breakdown["sgst"],
        total=breakdown["total"],
        gst_rate=gst_rate,
    )
    log_action(user, "folio_charge", entity="Folio", entity_id=folio.id,
               after={"line": description, "total": str(breakdown["total"])})
    return line


@transaction.atomic
def settle_folio(folio, payments, user=None, generate_invoice=True):
    """payments: list of {tender, amount, reference?, tip?}. Multi-tender (FR-PAY-002)."""
    for p in payments:
        Settlement.objects.create(
            folio=folio,
            tender=p.get("tender", "Cash"),
            amount=Decimal(str(p.get("amount", 0))),
            reference=p.get("reference", ""),
            tip=Decimal(str(p.get("tip", 0))),
        )
    if folio.balance <= 0:
        folio.status = Folio.SETTLED
        folio.settled_at = timezone.now()
        if generate_invoice and not folio.invoice_no:
            folio.invoice_no = _next_invoice_no()
        folio.save(update_fields=["status", "settled_at", "invoice_no"])
    log_action(user, "folio_settle", entity="Folio", entity_id=folio.id,
               after={"balance": str(folio.balance), "invoice": folio.invoice_no})
    return folio


def post_stay_room_charges(folio, user=None):
    """Ensure the stay's room nights are on the folio.

    Room charges normally post at night audit; for a same-day or pre-audit
    check-out we post any nights not yet charged so revenue is captured.
    """
    resv = folio.reservation
    if not resv:
        return Decimal("0")
    rate = resv.rate or (resv.room_type.base_rate if resv.room_type else Decimal("0"))
    if rate <= 0:
        return Decimal("0")
    nights = max(1, resv.nights or 1)
    already = folio.lines.filter(kind=FolioLine.KIND_ROOM).count()
    posted = Decimal("0")
    gst_rate = tax.room_rate_for(rate)
    for i in range(already, nights):
        line = post_charge(
            folio, kind=FolioLine.KIND_ROOM,
            description=f"Room charge — night {i + 1}",
            amount=rate, gst_rate=gst_rate,
            source=f"stay:{folio.id}:{i + 1}", user=user,
        )
        posted += line.total
    return posted


def _bill_to_company(folio, amount, user=None):
    """Move a checked-out folio balance to the billing company's city-ledger AR.

    The company pays later on invoice; their outstanding (receivables) grows now.
    """
    from apps.crm.models import Customer
    name = (folio.company_name or "").strip() or folio.guest_name
    company = Customer.objects.filter(name=name, customer_type=Customer.TYPE_CORPORATE).first()
    if not company:
        key = ("CO:" + name)[:20]  # synthetic AR key when the company has no mobile on file
        company, _ = Customer.objects.get_or_create(
            mobile=key, defaults={"name": name, "customer_type": Customer.TYPE_CORPORATE,
                                  "btc_enabled": True})
    company.outstanding = (company.outstanding or Decimal("0")) + amount
    company.save(update_fields=["outstanding"])
    log_action(user, "city_ledger_post", entity="Customer", entity_id=company.id,
               after={"folio": folio.id, "amount": str(amount), "outstanding": str(company.outstanding)})
    return company


@transaction.atomic
def check_out(folio, payments=None, tender=None, user=None):
    """Settle remaining balance, release the room to housekeeping (FR-PMS-005).

    Corporate (city-ledger) folios don't collect cash at the desk — the balance
    transfers to the company's AR and the folio closes billed-to-company.
    """
    # Capture room charges for the stay if the night audit hasn't already (FR-PMS-008).
    post_stay_room_charges(folio, user=user)
    if folio.routing == "city_ledger" and folio.balance > 0:
        # Bill-to-company: move the balance to the company's receivables (no cash).
        amount = folio.balance
        settle_folio(folio, [{"tender": "BTC", "amount": str(amount)}], user=user)
        _bill_to_company(folio, amount, user=user)
    else:
        # Settle the full remaining balance (room charges may have just been added).
        if tender is None and payments:
            tender = payments[0].get("tender", "Cash")
        if folio.balance > 0 and tender:
            settle_folio(folio, [{"tender": tender, "amount": str(folio.balance)}], user=user)
    if folio.balance > 0:
        raise ValueError("Outstanding balance must be cleared before check-out")
    if folio.status != Folio.SETTLED:
        folio.status = Folio.SETTLED
        folio.settled_at = timezone.now()
        if not folio.invoice_no:
            folio.invoice_no = _next_invoice_no()
        folio.save(update_fields=["status", "settled_at", "invoice_no"])
    if folio.room:
        folio.room.status = Room.VACANT_DIRTY
        folio.room.save(update_fields=["status", "updated_at"])
    if folio.reservation:
        folio.reservation.status = folio.reservation.CHECKED_OUT
        folio.reservation.save(update_fields=["status"])
    log_action(user, "check_out", entity="Folio", entity_id=folio.id,
               after={"invoice": folio.invoice_no})
    return folio


@transaction.atomic
def run_night_audit(business_date, user=None):
    """Post one night's room + tax charges to every in-house folio, roll the date.

    Idempotent per business_date: a completed run is returned as-is (resumable, NFR-014).
    """
    run, created = NightAuditRun.objects.get_or_create(business_date=business_date)
    if run.completed:
        return run

    rooms_posted = 0
    room_revenue = Decimal("0")
    tax_posted = Decimal("0")
    open_folios = Folio.objects.filter(status=Folio.OPEN).select_related(
        "reservation", "reservation__room_type"
    )
    for folio in open_folios:
        resv = folio.reservation
        if not resv:
            continue
        rate = resv.rate or (resv.room_type.base_rate if resv.room_type else Decimal("0"))
        if rate <= 0:
            continue
        gst_rate = tax.room_rate_for(rate)
        # Guard against double-posting the same night.
        already = folio.lines.filter(
            kind=FolioLine.KIND_ROOM, source=f"night-audit:{business_date}"
        ).exists()
        if already:
            continue
        line = post_charge(
            folio, kind=FolioLine.KIND_ROOM,
            description=f"Room charge {business_date}",
            amount=rate, gst_rate=gst_rate,
            source=f"night-audit:{business_date}", user=user,
        )
        rooms_posted += 1
        room_revenue += line.taxable
        tax_posted += line.cgst + line.sgst

    run.rooms_posted = rooms_posted
    run.room_revenue = room_revenue
    run.tax_posted = tax_posted
    run.completed = True
    run.save()

    # Roll the property business date forward.
    from apps.accounts.models import Property
    prop = Property.objects.first()
    if prop:
        prop.business_date = business_date + timedelta(days=1)
        prop.save(update_fields=["business_date"])
    log_action(user, "night_audit", entity="NightAuditRun", entity_id=run.id,
               after={"rooms": rooms_posted, "revenue": str(room_revenue)})
    return run
