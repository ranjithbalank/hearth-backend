from django.db import models


class GstSlab(models.Model):
    """Configurable GST rate slab applied per revenue category (BRD 5.23 / GST Master)."""

    name = models.CharField(max_length=80, unique=True)
    rate = models.DecimalField(max_digits=4, decimal_places=1)
    hsn_sac = models.CharField(max_length=12, blank=True)
    applies_to = models.CharField(max_length=40, blank=True, help_text="rooms | fnb | banquet | service")

    class Meta:
        ordering = ["rate"]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"
