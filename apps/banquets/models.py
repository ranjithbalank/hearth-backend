from django.db import models


class FunctionSpace(models.Model):
    name = models.CharField(max_length=120, unique=True)
    capacity = models.PositiveSmallIntegerField(default=100)

    def __str__(self):
        return self.name


class Event(models.Model):
    """Banquet event / BEO (BRD 5.6)."""

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    STATUS_CHOICES = [(TENTATIVE, "Tentative"), (CONFIRMED, "Confirmed"), (COMPLETED, "Completed")]

    space = models.ForeignKey(FunctionSpace, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=160)
    host = models.CharField(max_length=160, blank=True)
    contact = models.CharField(max_length=40, blank=True)
    event_type = models.CharField(max_length=40, blank=True, help_text="Wedding | Corporate | Birthday …")
    event_date = models.DateField()
    covers = models.PositiveSmallIntegerField(default=50)
    package_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=TENTATIVE)
    billed = models.BooleanField(default=False)

    class Meta:
        ordering = ["event_date"]

    def __str__(self):
        return f"{self.title} @ {self.space.name} ({self.event_date})"
