"""Front-office domain operations and cross-module seams.

These functions are the integration points referenced in the plan:
  - check_in  -> opens a folio, marks the room occupied, reservation in-house
  - post_charge -> generic folio posting (used by POS "post to room")
  - settle_folio / check_out -> multi-tender settle + room release
  - run_night_audit -> atomic, resumable day-end close
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import log_action
from apps.rooms.models import Room
from apps.tax import service as tax

from .models import Folio, FolioLine, NightAuditRun, Settlement


def _next_invoice_no():
    from apps.accounts.models import Property
    from apps.accounts.numbering import GST_MAX_DOC_NUMBER, next_document_number
    prop = Property.objects.first()
    return next_document_number(Folio, "invoice_no", prop.invoice_prefix if prop else "HRT",
                                max_total=GST_MAX_DOC_NUMBER)


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
        # The bill lives at the stay's branch (falling back to the room's).
        location_id=reservation.location_id or room.location_id,
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


def effective_billing_mode(folio):
    """The folio's GST mode: its own override, else the property's GST Master setting."""
    if folio.billing_mode:
        return folio.billing_mode
    from apps.accounts.views import get_property
    prop = get_property()
    return getattr(prop, "gst_billing_mode", "with_gst") or "with_gst"


def set_billing_mode(folio, mode, user=None):
    """Switch a folio between tax invoice and bill of supply (BRD 5.23).

    without_gst applies to ROOM (and incidental) charges only — F&B lines
    always keep GST, since restaurant food can't go on a bill of supply.
    Lines are recomputed from their taxable base; with_gst re-applies each
    line's own rate.
    """
    if mode not in ("with_gst", "without_gst"):
        raise ValueError("mode must be with_gst or without_gst")
    folio.billing_mode = mode
    folio.save(update_fields=["billing_mode"])
    for line in folio.lines.all():
        if mode == "without_gst" and line.kind != FolioLine.KIND_FNB:
            line.cgst = line.sgst = Decimal("0")
            line.total = line.taxable
        else:
            # with_gst, or an F&B line (always taxed — repairs older bills too).
            b = tax.compute(line.taxable, line.gst_rate)
            line.cgst, line.sgst, line.total = b["cgst"], b["sgst"], b["total"]
        line.save(update_fields=["cgst", "sgst", "total"])
    log_action(user, "billing_mode", entity="Folio", entity_id=folio.id,
               after={"mode": mode})
    return folio


def post_charge(folio, *, kind, description, amount, gst_rate, source="",
                inclusive=False, user=None):
    """Post a taxed charge line to a folio. Used by rooms, POS post-to-room, incidentals.

    A bill-of-supply folio (without_gst — per-folio override or the property's
    GST Master mode) posts ROOM/incidental charges with zero tax; F&B always
    carries GST. The line keeps its real GST rate so switching the bill back
    to with_gst can re-apply it.
    """
    zero_tax = (effective_billing_mode(folio) == "without_gst"
                and kind != FolioLine.KIND_FNB)
    breakdown = tax.compute(amount, Decimal("0") if zero_tax else gst_rate,
                            inclusive=inclusive)
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
    """payments: list of {tender, amount, reference?, tip?}. Multi-tender (FR-PAY-002).

    Two guards that were missing, both found by the Aug-2026 logic audit:

    * Every amount must be positive. A negative settlement is a payment that
      *increases* what is owed — nothing in the product needs one (a refund is
      its own flow, with its own audit trail), and it was a way to inflate a
      balance, or reopen a closed one, with a row that reads as a payment.
    * A folio that is already settled takes no further payment. Without this,
      hitting Settle twice on a paid bill charged the guest a second time and
      left the money sitting as a credit nobody reconciles.
    """
    # Re-read the row under a lock before deciding anything. Checking the
    # in-memory instance was correct with one request in flight and wrong with
    # two: both terminals load the folio, both see it open, both settle, and the
    # guest pays the bill twice. The lock serialises the decision on Postgres;
    # re-reading also fixes the far more common case of a stale instance, which
    # is what the check was actually failing on.
    fresh = Folio.objects.select_for_update().get(pk=folio.pk)
    if fresh.status != Folio.OPEN:
        raise ValueError("This folio is already settled — it can't take another payment.")
    parsed = []
    for p in payments:
        try:
            amount = Decimal(str(p.get("amount", 0)))
            tip = Decimal(str(p.get("tip", 0)))
        except (InvalidOperation, TypeError):
            raise ValueError("Payment amounts must be numbers.")
        if amount <= 0:
            raise ValueError("A payment must be for a positive amount.")
        if tip < 0:
            raise ValueError("A tip can't be negative.")
        parsed.append((p, amount, tip))
    for p, amount, tip in parsed:
        Settlement.objects.create(
            folio=folio,
            tender=p.get("tender", "Cash"),
            amount=amount,
            reference=p.get("reference", ""),
            tip=tip,
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
    billed, unkeyed = _nights_already_billed(folio)
    posted = Decimal("0")
    gst_rate = tax.room_rate_for(rate)
    for i in range(nights):
        source = f"stay:{folio.id}:{i + 1}"
        if source in billed:
            continue
        if unkeyed > 0:
            # A night the audit (or a pre-existing row) billed without a
            # per-night key. It still covers a night of this stay.
            unkeyed -= 1
            continue
        line = post_charge(
            folio, kind=FolioLine.KIND_ROOM,
            description=f"Room charge — night {i + 1}",
            amount=rate, gst_rate=gst_rate,
            source=source, user=user,
        )
        posted += line.total
    return posted


def _nights_already_billed(folio):
    """(per-night source keys already posted, count of room lines without one).

    Two things had to be reconciled here, and getting either wrong bills a
    guest twice for a bed they slept in once:

    * Room nights are posted by two different paths under two different source
      schemes — the night audit writes "night-audit:<date>", check-out writes
      "stay:<folio>:<n>". Only counting the keyed ones would re-bill every
      night the audit had already captured.
    * This used to be a plain count of the folio's room lines, which collapsed
      the moment a line moved: transfer one night to a companion's folio and
      the count dropped, so the night was posted again. The keyed lookup is
      therefore global — a transferred line still exists, just somewhere else.
    """
    prefix = f"stay:{folio.id}:"
    billed = set(
        FolioLine.objects.filter(source__startswith=prefix).values_list("source", flat=True)
    )
    unkeyed = (folio.lines.filter(kind=FolioLine.KIND_ROOM)
               .exclude(source__startswith=prefix).count())
    return billed, unkeyed


def pending_room_charges(folio):
    """Preview of room nights not yet posted (they post at night audit or at
    check-out) so the desk sees the real amount BEFORE collecting. Read-only —
    mirrors post_stay_room_charges without writing."""
    resv = folio.reservation
    if not resv or folio.status != Folio.OPEN:
        return []
    rate = resv.rate or (resv.room_type.base_rate if resv.room_type else Decimal("0"))
    if rate <= 0:
        return []
    nights = max(1, resv.nights or 1)
    # Exactly the rule post_stay_room_charges uses, so the preview the desk
    # quotes and the charges that actually post can never disagree.
    billed, unkeyed = _nights_already_billed(folio)
    zero_tax = effective_billing_mode(folio) == "without_gst"
    gst_rate = tax.room_rate_for(rate)
    out = []
    for i in range(nights):
        if f"stay:{folio.id}:{i + 1}" in billed:
            continue
        if unkeyed > 0:
            unkeyed -= 1
            continue
        b = tax.compute(rate, Decimal("0") if zero_tax else gst_rate)
        out.append({"description": f"Room charge — night {i + 1} (due at check-out)",
                    "total": b["total"]})
    return out


def company_account(name):
    """Get or create the corporate (bill-to-company) customer for `name`.

    Matches an existing corporate customer by name, else creates one keyed by a
    synthetic AR handle (companies often have no mobile on file).

    That handle used to be ("CO:" + name)[:20], truncated to fit the 20-char
    mobile column. Two customers whose names agreed for their first 17
    characters therefore produced the same key — "Infosys Technologies Ltd" and
    "Infosys Technologies Pvt" both became "CO:Infosys Technolog" — so
    get_or_create returned the FIRST company and the second one's folio was
    billed to it. Real money on the wrong receivable, silently. The key is now
    a digest of the full name: fixed width, no collisions between distinct
    names. (Aug-2026 logic audit.)

    Existing rows are unaffected: the name lookup below runs first, so a company
    already keyed the old way is still found by name and never re-created.
    """
    import hashlib

    from apps.crm.models import Customer
    name = (name or "").strip()
    if not name:
        return None
    # iexact, because "ACME Ltd" and "Acme Ltd" are one debtor to everyone
    # except the database.
    company = Customer.objects.filter(
        name__iexact=name, customer_type=Customer.TYPE_CORPORATE).first()
    if not company:
        digest = hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:16]
        company, _ = Customer.objects.get_or_create(
            mobile=f"CO:{digest}",
            defaults={"name": name, "customer_type": Customer.TYPE_CORPORATE,
                      "btc_enabled": True})
    return company


def _bill_to_company(folio, amount, user=None):
    """Move a checked-out folio balance to the billing company's city-ledger AR.

    The company pays later on invoice; their outstanding (receivables) grows now.
    """
    company = folio.company or company_account(folio.company_name or folio.guest_name)
    if not company:
        return None
    if folio.company_id != company.id:
        folio.company = company
        folio.save(update_fields=["company"])
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
        # Normally the room releases to Housekeeping as dirty and waits for a
        # cleaner. If the property runs no separate Housekeeping desk, that
        # cleaner never comes — the room would sit out of sellable inventory
        # forever. So release it straight to clean (sellable) instead.
        from apps.accounts.features import is_enabled
        folio.room.status = Room.VACANT_DIRTY if is_enabled("housekeeping") else Room.VACANT_CLEAN
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
