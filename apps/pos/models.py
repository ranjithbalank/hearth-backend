from decimal import Decimal

from django.db import models

from apps.crm.models import Customer
from apps.frontoffice.models import Folio


class Table(models.Model):
    FREE = "free"
    RUNNING = "running"
    PRINTED = "printed"
    PAID = "paid"
    RESERVED = "reserved"
    STATUS_CHOICES = [
        (FREE, "Free"),
        (RUNNING, "Running"),
        (PRINTED, "Printed"),
        (PAID, "Paid"),
        (RESERVED, "Reserved"),
    ]

    name = models.CharField(max_length=20)
    section = models.CharField(max_length=40, default="Main")
    seats = models.PositiveSmallIntegerField(default=4)
    shape = models.CharField(max_length=20, default="square")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=FREE)
    qr_token = models.CharField(max_length=20, blank=True, default="", db_index=True,
                                help_text="token embedded in the table QR for guest ordering")

    class Meta:
        ordering = ["section", "name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    VEG = "veg"
    NONVEG = "nonveg"
    EGG = "egg"
    DIET_CHOICES = [(VEG, "Veg"), (NONVEG, "Non-veg"), (EGG, "Egg")]

    name = models.CharField(max_length=120)
    short_code = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=1, default=5)
    diet = models.CharField(max_length=10, choices=DIET_CHOICES, default=VEG)
    station = models.CharField(max_length=20, default="kitchen", help_text="kitchen | bar")
    available = models.BooleanField(default=True)
    # Item photo as a data URL (same pattern as the property logo) — shows on
    # POS tiles and the guest QR menu. Kept small client-side (~64kB).
    image = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self):
        return self.name


class ChannelPrice(models.Model):
    """Per-channel price override for an item (BRD FR-MNU-003).

    e.g. a dish priced higher on delivery than dine-in. Channel == order mode.
    """

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="channel_prices")
    channel = models.CharField(max_length=12, help_text="dinein | takeaway | delivery | online")
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = [("menu_item", "channel")]

    def __str__(self):
        return f"{self.menu_item.name} @ {self.channel}: {self.price}"


class Variant(models.Model):
    """A priced variant of an item, e.g. Half/Full, S/M/L (BRD FR-MNU-004)."""

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=60)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    short_code = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.menu_item.name} — {self.name}"


class AddOnGroup(models.Model):
    """A group of modifiers with selection rules (BRD FR-MNU-005)."""

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="addon_groups")
    name = models.CharField(max_length=60)
    min_select = models.PositiveSmallIntegerField(default=0)
    max_select = models.PositiveSmallIntegerField(default=1)

    @property
    def required(self):
        return self.min_select > 0

    def __str__(self):
        return f"{self.menu_item.name} · {self.name}"


class AddOn(models.Model):
    group = models.ForeignKey(AddOnGroup, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=60)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return self.name


class MenuSchedule(models.Model):
    """Time-of-day price override, e.g. happy hour (BRD FR-MNU-011)."""

    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="schedules")
    name = models.CharField(max_length=60, default="Happy hour")
    start_time = models.TimeField()
    end_time = models.TimeField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def active_now(self):
        from django.utils import timezone
        now = timezone.localtime().time()
        if self.start_time <= self.end_time:
            return self.start_time <= now <= self.end_time
        return now >= self.start_time or now <= self.end_time  # window crosses midnight

    def __str__(self):
        return f"{self.menu_item.name} {self.name} {self.start_time}-{self.end_time}"


class ComboComponent(models.Model):
    """A component of a combo/meal item (BRD FR-MNU-006).

    The combo is itself a MenuItem (with its own combo price); these rows list
    the items it bundles, so KOT prints components and stock deducts each one.
    """

    combo = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="combo_components")
    component = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="in_combos")
    qty = models.PositiveSmallIntegerField(default=1)

    def __str__(self):
        return f"{self.combo.name} ⊃ {self.qty}× {self.component.name}"


class Order(models.Model):
    DINEIN = "dinein"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"
    # Room channel: an in-house guest's order, taken against their folio from
    # the start — the bill posts to the room, never to a table.
    ROOM = "room"
    MODE_CHOICES = [(DINEIN, "Dine-in"), (TAKEAWAY, "Takeaway"),
                    (DELIVERY, "Delivery"), (ROOM, "Room")]

    OPEN = "open"
    KOT_FIRED = "kot_fired"
    BILLED = "billed"
    SETTLED = "settled"
    POSTED_TO_ROOM = "posted_to_room"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (KOT_FIRED, "KOT Fired"),
        (BILLED, "Billed"),
        (SETTLED, "Settled"),
        (POSTED_TO_ROOM, "Posted to Room"),
    ]

    mode = models.CharField(max_length=12, choices=MODE_CHOICES, default=DINEIN)
    table = models.ForeignKey(
        Table, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    covers = models.PositiveSmallIntegerField(default=1)
    captain = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=OPEN)
    folio = models.ForeignKey(
        Folio, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_orders"
    )
    kot_no = models.CharField(max_length=20, blank=True)
    # Kitchen Display status (BRD 5.13 / KDS): ""(none) | cooking | ready | served
    kitchen_status = models.CharField(max_length=12, blank=True, default="")
    # Online ordering & delivery (BRD 5.16)
    source_platform = models.CharField(max_length=20, blank=True, default="",
                                       help_text="zomato | swiggy | website | qr")
    external_ref = models.CharField(max_length=64, blank=True, default="", db_index=True)
    online_status = models.CharField(max_length=12, blank=True, default="",
                                     help_text="received | accepted | ready | dispatched")
    prepaid = models.BooleanField(default=False)
    # Virtual outlet / cloud brand sharing the kitchen (BRD FR-ONL-005)
    brand = models.CharField(max_length=60, blank=True, default="")
    # Discounts / loyalty (BRD 5.15)
    DISC_NONE = "none"
    DISC_PERCENT = "percent"
    DISC_FIXED = "fixed"
    discount_kind = models.CharField(max_length=10, default=DISC_NONE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_reason = models.CharField(max_length=160, blank=True)
    coupon = models.ForeignKey(
        "pos.Coupon", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    loyalty_redeemed = models.PositiveIntegerField(default=0, help_text="points redeemed")
    # Offline resilience (BRD FR-POS-010 / NFR-002): client-generated id for dedupe.
    client_uuid = models.CharField(max_length=64, blank=True, default="", db_index=True)
    offline_origin = models.BooleanField(default=False)
    # Pickup token for takeaway/delivery — daily sequence shown on the token board.
    token_no = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order #{self.id} ({self.mode})"

    def _subtotal(self):
        return sum((l.unit_price * l.qty for l in self.lines.all()), start=Decimal("0"))

    def discount_amount(self, subtotal=None):
        """Total pre-tax reduction from order discount + coupon + loyalty redemption."""
        sub = self._subtotal() if subtotal is None else subtotal
        disc = Decimal("0")
        if self.discount_kind == self.DISC_PERCENT:
            disc += (sub * self.discount_value / Decimal("100"))
        elif self.discount_kind == self.DISC_FIXED:
            disc += min(self.discount_value, sub)
        if self.coupon:
            disc += self.coupon.reduction(sub)
        if self.loyalty_redeemed:
            disc += Decimal(self.loyalty_redeemed)  # 1 point = ₹1
        return min(disc, sub)

    def totals(self):
        """GST computed on the discounted amount, discount apportioned across lines."""
        from apps.tax import service as tax
        from decimal import ROUND_HALF_UP
        sub = self._subtotal()
        disc = self.discount_amount(sub)
        factor = (sub - disc) / sub if sub else Decimal("0")
        taxable = cgst = sgst = total = Decimal("0")
        for line in self.lines.all():
            line_net = (line.unit_price * line.qty * factor).quantize(Decimal("0.01"), ROUND_HALF_UP)
            b = tax.compute(line_net, line.menu_item.gst_rate)
            taxable += b["taxable"]; cgst += b["cgst"]; sgst += b["sgst"]; total += b["total"]
        return {
            "subtotal": sub, "discount": disc.quantize(Decimal("0.01"), ROUND_HALF_UP),
            "taxable": taxable, "cgst": cgst, "sgst": sgst,
            "tax": cgst + sgst, "total": total,
        }


class TillSession(models.Model):
    """Cash till / shift session (competitor parity: day-end close).

    Opens with a float, tracks cash in/out, closes with a counted amount —
    expected cash is float + ins − outs + cash settlements taken during the
    session window, so the variance surfaces pilferage or mistakes.
    """

    OPEN = "open"
    CLOSED = "closed"

    opened_by = models.CharField(max_length=80, blank=True)
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=8, default=OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=80, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    denominations = models.JSONField(default=dict, blank=True,
                                     help_text='{"500": 4, "100": 10, ...} counted notes')
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Till {self.id} ({self.status})"

    def cash_in_out(self):
        from django.db.models import Sum
        ins = self.entries.filter(kind="in").aggregate(s=Sum("amount"))["s"] or Decimal("0")
        outs = self.entries.filter(kind="out").aggregate(s=Sum("amount"))["s"] or Decimal("0")
        return ins, outs

    def tender_totals(self):
        """Settlements taken during this session's window, grouped by tender."""
        from django.db.models import Count, Sum

        from apps.frontoffice.models import Settlement
        qs = Settlement.objects.filter(created_at__gte=self.opened_at)
        if self.closed_at:
            qs = qs.filter(created_at__lte=self.closed_at)
        return list(qs.values("tender").annotate(amount=Sum("amount"), count=Count("id")))


class TillEntry(models.Model):
    """Cash paid in/out of the till mid-shift (petty cash, change, bank drop)."""

    session = models.ForeignKey(TillSession, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=4, help_text="in | out")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=160)
    created_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "till entries"

    def __str__(self):
        return f"{self.kind} {self.amount} ({self.reason})"


class Kot(models.Model):
    """One fired kitchen ticket. Each fire on an order is its own round —
    round 2 must reach the kitchen as a fresh ticket with only the new items,
    never merged into (or re-showing) an already-served round (FR-POS-004)."""

    COOKING = "cooking"
    READY = "ready"
    SERVED = "served"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="kots")
    number = models.CharField(max_length=24)
    status = models.CharField(max_length=12, default=COOKING)  # cooking | ready | served
    created_at = models.DateTimeField(auto_now_add=True)
    # Kitchen performance: when the round was bumped ready / served.
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.number


class AggregatorPayout(models.Model):
    """Imported payout line from a delivery platform's settlement report.

    Reconciled against POS-recorded prepaid settlements to flag variances
    (anti-pilferage / missed-order detection).
    """

    platform = models.CharField(max_length=20)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True, default="")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        unique_together = [("platform", "date", "reference")]

    def __str__(self):
        return f"{self.platform} {self.date}: {self.amount}"


class Feedback(models.Model):
    """Guest feedback captured via the QR/link printed on the bill.

    A pending row (token only) is created when the bill closes; the guest's
    submission fills rating/NPS/comment. Public endpoint, token-credentialed.
    """

    order = models.OneToOneField(Order, on_delete=models.CASCADE,
                                 related_name="feedback", null=True, blank=True)
    token = models.CharField(max_length=40, unique=True, db_index=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1–5 stars")
    nps = models.PositiveSmallIntegerField(null=True, blank=True, help_text="0–10 recommend score")
    comment = models.CharField(max_length=400, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback {self.token[:8]} ({self.rating or '—'}★)"


class TableReservation(models.Model):
    """Restaurant table booking / walk-in waitlist (competitor parity).

    kind 'reservation' has a time; 'waitlist' is the walk-in queue. A booked
    reservation with a table holds it (Table.RESERVED) near its time.
    """

    BOOKED = "booked"
    SEATED = "seated"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"

    kind = models.CharField(max_length=12, default="reservation",
                            help_text="reservation | waitlist")
    table = models.ForeignKey(Table, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="reservations")
    name = models.CharField(max_length=80)
    mobile = models.CharField(max_length=15, blank=True)
    party_size = models.PositiveSmallIntegerField(default=2)
    reserved_for = models.DateTimeField(null=True, blank=True, help_text="null for waitlist")
    status = models.CharField(max_length=12, default=BOOKED)
    note = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reserved_for", "created_at"]

    def __str__(self):
        return f"{self.name} ({self.party_size}) {self.kind}"


class Coupon(models.Model):
    """Promo coupon (BRD FR-PRO-002)."""

    code = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=10, default="percent", help_text="percent | fixed")
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_bill = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    usage_limit = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    used_count = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

    def is_valid(self, subtotal):
        if not self.active:
            return False, "Coupon is not active"
        if subtotal < self.min_bill:
            return False, f"Minimum bill of {self.min_bill} required"
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False, "Coupon usage limit reached"
        return True, ""

    def reduction(self, subtotal):
        if self.kind == "percent":
            return (subtotal * self.value / Decimal("100"))
        return min(self.value, subtotal)


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    variant = models.ForeignKey(Variant, on_delete=models.SET_NULL, null=True, blank=True)
    addons = models.JSONField(default=list, blank=True, help_text="[{name, price}]")
    qty = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.CharField(max_length=120, blank=True)
    kot_fired = models.BooleanField(default=False)
    # Which fire round this line went out on (null until fired).
    kot = models.ForeignKey(Kot, on_delete=models.SET_NULL, null=True, blank=True,
                            related_name="lines")

    def __str__(self):
        return f"{self.qty}× {self.menu_item.name}"

    @property
    def display_name(self):
        bits = [self.menu_item.name]
        if self.variant_id:
            bits.append(f"({self.variant.name})")
        if self.addons:
            bits.append("+ " + ", ".join(a["name"] for a in self.addons))
        combo = list(self.menu_item.combo_components.all())
        if combo:
            bits.append("[" + ", ".join(f"{c.qty}×{c.component.name}" for c in combo) + "]")
        return " ".join(bits)
