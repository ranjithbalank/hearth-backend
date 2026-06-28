"""Channel-manager operations and the RMS→channel push seam."""
from decimal import Decimal

from .models import Channel, ChannelPush, ChannelRate


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
