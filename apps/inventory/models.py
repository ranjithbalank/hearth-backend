from decimal import Decimal

from django.db import models


class Ingredient(models.Model):
    """Raw-material / ingredient master (BRD 5.17 FR-INV-001)."""

    name = models.CharField(max_length=120, unique=True)
    unit = models.CharField(max_length=12, default="kg", help_text="base unit, e.g. kg, g, l, pc")
    category = models.CharField(max_length=80, blank=True)
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    @property
    def below_par(self):
        return self.current_stock < self.reorder_level


class StockMovement(models.Model):
    """Append-only stock ledger (BRD FR-INV-002)."""

    RECEIPT = "receipt"
    CONSUMPTION = "consumption"
    WASTAGE = "wastage"
    ADJUSTMENT = "adjustment"
    KIND_CHOICES = [
        (RECEIPT, "Receipt"),
        (CONSUMPTION, "Consumption"),
        (WASTAGE, "Wastage"),
        (ADJUSTMENT, "Adjustment"),
    ]

    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=14, choices=KIND_CHOICES)
    qty = models.DecimalField(max_digits=12, decimal_places=3, help_text="signed: + in, - out")
    reason = models.CharField(max_length=160, blank=True)
    source = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.qty} {self.ingredient.name}"


def apply_movement(ingredient, kind, qty, reason="", source=""):
    """Record a movement and update the running stock atomically-ish."""
    qty = Decimal(str(qty))
    StockMovement.objects.create(
        ingredient=ingredient, kind=kind, qty=qty, reason=reason, source=source
    )
    ingredient.current_stock = (ingredient.current_stock or Decimal("0")) + qty
    ingredient.save(update_fields=["current_stock"])
    return ingredient
