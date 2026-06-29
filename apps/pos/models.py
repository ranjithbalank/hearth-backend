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

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self):
        return self.name


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


class Order(models.Model):
    DINEIN = "dinein"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"
    MODE_CHOICES = [(DINEIN, "Dine-in"), (TAKEAWAY, "Takeaway"), (DELIVERY, "Delivery")]

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

    def __str__(self):
        return f"{self.qty}× {self.menu_item.name}"

    @property
    def display_name(self):
        bits = [self.menu_item.name]
        if self.variant_id:
            bits.append(f"({self.variant.name})")
        if self.addons:
            bits.append("+ " + ", ".join(a["name"] for a in self.addons))
        return " ".join(bits)
