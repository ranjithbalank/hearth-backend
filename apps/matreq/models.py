from django.db import models

from apps.inventory.models import Ingredient


class MaterialRequest(models.Model):
    """Departmental indent / requisition (BRD 5.7 FR-STR-002)."""

    REQUESTED = "requested"
    APPROVED = "approved"
    ISSUED = "issued"
    STATUS_CHOICES = [(REQUESTED, "Requested"), (APPROVED, "Approved"), (ISSUED, "Issued")]

    department = models.CharField(max_length=80)
    requested_by = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=REQUESTED)
    created_at = models.DateTimeField(auto_now_add=True)
    # Which branch this indent is for — set at creation from the requester's
    # own branch (same single-assignment auto-scoping as POS orders). An
    # approver/issuer only ever sees their own branch's indents unless
    # they're a universal (all-branch) role.
    location = models.ForeignKey(
        "accounts.Branch", null=True, blank=True, on_delete=models.SET_NULL, related_name="material_requests",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Indent #{self.id} — {self.department} ({self.status})"


class MaterialRequestLine(models.Model):
    request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name="indent_lines")
    qty = models.DecimalField(max_digits=12, decimal_places=3)

    def __str__(self):
        return f"{self.qty} {self.ingredient.name}"
