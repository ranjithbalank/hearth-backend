from django.db import models


class Customer(models.Model):
    """Unified customer/guest profile, keyed by mobile (BRD 5.20 FR-CRM-001)."""

    TYPE_GUEST = "guest"
    TYPE_CORPORATE = "corporate"
    TYPE_AGENT = "agent"
    TYPE_CHOICES = [
        (TYPE_GUEST, "Guest"),
        (TYPE_CORPORATE, "Corporate"),
        (TYPE_AGENT, "Travel Agent"),
    ]

    name = models.CharField(max_length=200)
    mobile = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    locality = models.CharField(max_length=120, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    customer_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_GUEST)
    btc_enabled = models.BooleanField(default=False, help_text="Bill-to-company / credit")
    outstanding = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    loyalty_points = models.IntegerField(default=0)
    marketing_consent = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.mobile})"


class Campaign(models.Model):
    """SMS/WhatsApp marketing blast to a customer segment (FR-CRM parity).

    Only customers with marketing_consent receive it; delivery goes through
    the pluggable messaging adapter (mock locally, MSG91/Twilio in prod).
    """

    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=12, default="sms", help_text="sms | whatsapp")
    segment = models.CharField(max_length=20, default="all",
                               help_text="all | guests | corporate | loyal")
    message = models.CharField(max_length=480, help_text="{name} and {points} placeholders")
    sent_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0, help_text="no consent / no mobile")
    created_by = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.channel} → {self.segment})"
