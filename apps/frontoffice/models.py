from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.reservations.models import Reservation
from apps.rooms.models import Room


class Folio(models.Model):
    OPEN = "open"
    SETTLED = "settled"
    STATUS_CHOICES = [(OPEN, "Open"), (SETTLED, "Settled")]

    reservation = models.OneToOneField(
        Reservation, on_delete=models.PROTECT, related_name="folio", null=True, blank=True
    )
    guest_name = models.CharField(max_length=200)
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="folios"
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN)
    routing = models.CharField(
        max_length=20, default="guest",
        help_text="guest | city_ledger — where the balance is collected",
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    invoice_no = models.CharField(max_length=30, blank=True)
    # KYC for statutory guest reporting (BRD FR-PMS-012)
    id_type = models.CharField(max_length=30, blank=True)
    id_number = models.CharField(max_length=40, blank=True)
    guest_type = models.CharField(max_length=20, blank=True)
    # For corporate (bill-to-company) guests — the company the folio bills to.
    company_name = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"Folio #{self.id} — {self.guest_name}"

    @property
    def charges_total(self):
        # Aggregate hits the DB every time, so it stays correct even when the
        # related rows were prefetched (and thus cached) before a new charge/payment.
        return self.lines.aggregate(s=Sum("total"))["s"] or Decimal("0")

    @property
    def paid_total(self):
        return self.settlements.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    @property
    def balance(self):
        return self.charges_total - self.paid_total


class FolioLine(models.Model):
    KIND_ROOM = "room"
    KIND_TAX = "tax"
    KIND_FNB = "fnb"
    KIND_INCIDENTAL = "incidental"
    KIND_CHOICES = [
        (KIND_ROOM, "Room"),
        (KIND_TAX, "Tax"),
        (KIND_FNB, "F&B"),
        (KIND_INCIDENTAL, "Incidental"),
    ]

    folio = models.ForeignKey(Folio, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_ROOM)
    description = models.CharField(max_length=200)
    source = models.CharField(max_length=40, blank=True, help_text="e.g. POS order #, night-audit")
    taxable = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.description} — {self.total}"


class Settlement(models.Model):
    TENDERS = ["Cash", "Card", "UPI", "BTC", "Other"]

    folio = models.ForeignKey(
        Folio, on_delete=models.CASCADE, related_name="settlements", null=True, blank=True
    )
    tender = models.CharField(max_length=20, default="Cash")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.tender} {self.amount}"


class NightAuditRun(models.Model):
    """End-of-day close (BRD FR-PMS-008 / NFR-014). Atomic + resumable."""

    business_date = models.DateField()
    rooms_posted = models.IntegerField(default=0)
    room_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_posted = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-business_date"]

    def __str__(self):
        return f"Night audit {self.business_date} ({'done' if self.completed else 'pending'})"
