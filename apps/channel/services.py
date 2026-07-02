"""Channel-manager operations and the RMS→channel push seam."""
from datetime import datetime
from decimal import Decimal

from .models import Channel, ChannelPush, ChannelRate


class IngestError(Exception):
    """Raised when an inbound OTA booking payload can't be accepted."""


def ingest_booking(payload, user=None):
    """Turn an OTA (e.g. Booking.com) reservation payload into a PMS reservation.

    This is the seam a real channel-manager webhook would call. Idempotent on
    ota_ref, so re-delivered webhooks return the existing booking rather than
    duplicating it. Returns (reservation, created: bool).
    """
    from apps.crm.models import Customer
    from apps.reservations.models import Reservation
    from apps.rooms.models import RoomType

    ref = str(payload.get("reservation_id") or payload.get("ota_ref") or "").strip()
    if ref:
        existing = Reservation.objects.filter(ota_ref=ref).first()
        if existing:
            return existing, False

    rt = RoomType.objects.filter(code=(payload.get("room_type") or "").upper()).first()
    if not rt:
        raise IngestError(f"unknown room type '{payload.get('room_type')}'")

    ci = payload.get("checkin")
    co = payload.get("checkout")
    if not ci or not co:
        raise IngestError("checkin and checkout dates are required")
    try:
        nights = (datetime.strptime(co, "%Y-%m-%d").date()
                  - datetime.strptime(ci, "%Y-%m-%d").date()).days
    except ValueError:
        raise IngestError("invalid date format (expected YYYY-MM-DD)")
    if nights < 1:
        raise IngestError("checkout must be after checkin")

    rate = Decimal(str(payload.get("rate") or rt.base_rate))
    prepaid_amt = Decimal(str(payload.get("amount_prepaid") or 0))
    channel_name = payload.get("channel") or "OTA"

    guest = None
    mobile = (payload.get("mobile") or "").strip()
    if mobile:
        guest, _ = Customer.objects.get_or_create(
            mobile=mobile, defaults={"name": payload.get("guest_name", "OTA Guest")})

    resv = Reservation.objects.create(
        guest=guest, guest_name=payload.get("guest_name", "OTA Guest"),
        room_type=rt, checkin_date=ci, checkout_date=co, nights=nights,
        source=Reservation.SOURCE_OTA, status=Reservation.BOOKED,
        rate=rate, deposit=prepaid_amt, prepaid=prepaid_amt > 0,
        channel_name=channel_name, ota_ref=ref,
        notes=f"Imported from {channel_name}" + (f" · {ref}" if ref else ""),
    )
    ChannelPush.objects.create(
        kind=ChannelPush.KIND_BOOKING,
        detail=f"{channel_name}: {resv.guest_name}, {rt.code} {ci}→{co}"
               + (f" (prepaid {prepaid_amt})" if prepaid_amt else ""),
    )
    return resv, True


def push_rate(room_type, rate, *, kind=ChannelPush.KIND_RMS, detail=""):
    """Write a rate to every connected channel for a room type (pooled control point).

    This is the seam an accepted RMS recommendation calls into.
    """
    rate = Decimal(str(rate))
    updated = 0
    for ch in Channel.objects.filter(connected=True):
        cr, _ = ChannelRate.objects.get_or_create(
            channel=ch, room_type=room_type, defaults={"rate": rate, "availability": 0}
        )
        cr.rate = rate
        cr.save(update_fields=["rate", "updated_at"])
        updated += 1
    ChannelPush.objects.create(
        kind=kind,
        detail=detail or f"{room_type.code} → {rate} on {updated} channels",
    )
    return updated


def parity_breaches():
    """Return room-type codes whose rate differs across connected channels."""
    breaches = []
    from apps.rooms.models import RoomType

    for rt in RoomType.objects.all():
        rates = list(
            ChannelRate.objects.filter(room_type=rt, channel__connected=True)
            .values_list("rate", flat=True)
        )
        if len(set(rates)) > 1:
            breaches.append(rt.code)
    return breaches


def fix_parity():
    """Align each room type's channel rates to the lowest current rate."""
    from apps.rooms.models import RoomType

    fixed = []
    for rt in RoomType.objects.all():
        rows = ChannelRate.objects.filter(room_type=rt, channel__connected=True)
        rates = [r.rate for r in rows]
        if len(set(rates)) > 1:
            target = min(rates)
            rows.update(rate=target)
            fixed.append(rt.code)
    if fixed:
        ChannelPush.objects.create(
            kind=ChannelPush.KIND_PARITY,
            detail=f"Parity aligned: {', '.join(fixed)}",
        )
    return fixed
