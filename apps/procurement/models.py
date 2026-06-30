from decimal import Decimal

from django.db import models

from apps.inventory.models import Ingredient


class Supplier(models.Model):
    name = models.CharField(max_length=160, unique=True)
    gstin = models.CharField(max_length=20, blank=True)
    contact = models.CharField(max_length=120, blank=True)
    payment_terms = models.CharField(max_length=80, blank=True)
    lead_time_days = models.PositiveSmallIntegerField(default=2)
    rating = models.DecimalField(max_digits=2, decimal_places=1, default=4)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Vendor(models.Model):
    """Service vendor (vs goods Supplier) — BRD prototype 'Vendors' master."""

    name = models.CharField(max_length=160, unique=True)
    category = models.CharField(max_length=80, blank=True)
    contact = models.CharField(max_length=120, blank=True)
    payment_terms = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, default="Active")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    PENDING = "pending"
    APPROVED = "approved"
    RECEIVED = "received"
    STATUS_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (RECEIVED, "Received")]

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PO #{self.id} — {self.supplier.name} ({self.status})"

    @property
    def total(self):
        return sum((l.qty * l.rate for l in self.lines.all()), start=Decimal("0"))


class PurchaseOrderLine(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name="po_lines")
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    received_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.qty} {self.ingredient.name} @ {self.rate}"


class GoodsReceipt(models.Model):
    """A GRN posts received quantities into the stock ledger (BRD FR-PUR-003)."""

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="grns")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"GRN for PO #{self.purchase_order_id}"
