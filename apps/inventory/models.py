from decimal import Decimal

from django.db import models


# Raw-material categories and base consumption units from the
# "Restaurant Inventory, Recipe & Consumption Management" spec (§1).
CATEGORY_SUGGESTIONS = [
    "Vegetables", "Meat / Seafood", "Dairy", "Spices", "Beverages",
    "Packaging Materials", "Cleaning Materials", "Other Consumables",
]
UNIT_CHOICES = [
    ("kg", "Kilogram (KG)"), ("g", "Gram (GM)"), ("l", "Litre (L)"),
    ("ml", "Millilitre (ML)"), ("pc", "Piece (Nos)"), ("pkt", "Packet"),
]


class Ingredient(models.Model):
    """Raw-material / ingredient master (BRD 5.17 FR-INV-001, spec §1)."""

    code = models.CharField(max_length=20, blank=True, default="",
                            help_text="raw material code, e.g. RM-0001")
    name = models.CharField(max_length=120, unique=True)
    unit = models.CharField(max_length=12, default="kg", choices=UNIT_CHOICES,
                            help_text="base consumption unit")
    category = models.CharField(max_length=80, blank=True)
    current_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    min_stock_level = models.DecimalField(max_digits=12, decimal_places=3, default=0,
                                          help_text="hard floor — running out stops prep")
    reorder_level = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                    help_text="purchase rate per base unit")
    storage_location = models.CharField(max_length=80, blank=True, default="",
                                        help_text="storage location / warehouse")
    expiry_date = models.DateField(null=True, blank=True,
                                   help_text="earliest expiry in stock, where applicable")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.code:  # auto-assign a material code on first save
            self.code = f"RM-{self.pk:04d}"
            super().save(update_fields=["code"])

    @property
    def below_par(self):
        return self.current_stock < self.reorder_level

    @property
    def below_min(self):
        return self.current_stock < self.min_stock_level


class StockMovement(models.Model):
    """Append-only stock ledger (BRD FR-INV-002, spec §4)."""

    RECEIPT = "receipt"
    TRANSFER = "transfer"
    PRODUCTION = "production"
    CONSUMPTION = "consumption"
    WASTAGE = "wastage"
    ADJUSTMENT = "adjustment"
    RETURN = "return"
    COUNT = "count"
    EXPIRY = "expiry"
    KIND_CHOICES = [
        (RECEIPT, "Purchase Receipt"),
        (TRANSFER, "Stock Transfer"),
        (PRODUCTION, "Production / Preparation"),
        (CONSUMPTION, "Recipe Consumption"),
        (WASTAGE, "Wastage"),
        (ADJUSTMENT, "Stock Adjustment"),
        (RETURN, "Stock Return"),
        (COUNT, "Physical Stock Count"),
        (EXPIRY, "Expiry / Damage Loss"),
    ]

    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=14, choices=KIND_CHOICES)
    qty = models.DecimalField(max_digits=12, decimal_places=3, help_text="signed: + in, - out")
    balance = models.DecimalField(max_digits=12, decimal_places=3, default=0,
                                  help_text="stock balance after this movement")
    reason = models.CharField(max_length=160, blank=True)
    source = models.CharField(max_length=80, blank=True,
                              help_text="reference: purchase bill / order / transfer no.")
    created_by = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.qty} {self.ingredient.name}"


def apply_movement(ingredient, kind, qty, reason="", source="", user=None):
    """Record a movement and update the running stock, snapshotting the balance."""
    qty = Decimal(str(qty))
    ingredient.current_stock = (ingredient.current_stock or Decimal("0")) + qty
    StockMovement.objects.create(
        ingredient=ingredient, kind=kind, qty=qty, reason=reason, source=source,
        balance=ingredient.current_stock,
        created_by=(getattr(user, "username", "") or "") if user else "",
    )
    ingredient.save(update_fields=["current_stock"])
    return ingredient
