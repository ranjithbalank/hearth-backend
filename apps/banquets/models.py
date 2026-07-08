from django.db import models


class FunctionSpace(models.Model):
    name = models.CharField(max_length=120, unique=True)
    capacity = models.PositiveSmallIntegerField(default=100)

    def __str__(self):
        return self.name


class CateringRate(models.Model):
    """Singleton: the property's standard per-plate catering prices. New banquet
    events pre-fill their veg/non-veg rates from here (overridable per event)."""
    veg_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    nonveg_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    @classmethod
    def get_solo(cls):
        return cls.objects.first() or cls.objects.create()


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
    start_time = models.TimeField(null=True, blank=True, help_text="event start time")
    end_time = models.TimeField(null=True, blank=True, help_text="event end time")
    covers = models.PositiveSmallIntegerField(default=50)
    package_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Optional catering (only when the restaurant/F&B module is enabled).
    food_covers = models.PositiveSmallIntegerField(default=0, help_text="approx. plates to cater (veg + nonveg)")
    food_pref = models.CharField(max_length=10, blank=True, help_text="veg | nonveg | both")
    # When food_pref == "both", the approximate split by preference.
    food_veg = models.PositiveSmallIntegerField(default=0, help_text="approx. veg plates")
    food_nonveg = models.PositiveSmallIntegerField(default=0, help_text="approx. non-veg plates")
    # Per-plate catering rates (₹/person) by preference.
    veg_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="₹ per veg plate")
    nonveg_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="₹ per non-veg plate")
    # BEO prep status shown on the kitchen display (FR-BQT-004): "" | pending | ready
    beo_status = models.CharField(max_length=10, blank=True, default="")
    # Sequential BEO document number, assigned once the event is first confirmed.
    beo_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=TENTATIVE)
    billed = models.BooleanField(default=False)

    class Meta:
        ordering = ["event_date"]

    def __str__(self):
        return f"{self.title} @ {self.space.name} ({self.event_date})"

    @staticmethod
    def default_rates():
        return CateringRate.get_solo()

    @property
    def catering_amount(self):
        """Catering charge = plates × per-plate rate, by preference."""
        return (self.food_veg * self.veg_rate) + (self.food_nonveg * self.nonveg_rate)

    @property
    def bill_subtotal(self):
        """Taxable base for the event: hall/package + catering."""
        return self.package_amount + self.catering_amount
