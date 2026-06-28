from django.contrib.auth.models import AbstractUser
from django.db import models

from .constants import ROLE_CHOICES, ROLE_FRONT_OFFICE


class Property(models.Model):
    """The hotel/restaurant property. One row in this single-property release.

    Holds the one-time edition setup and entitlement flags.
    """

    EDITION_CHOICES = [
        ("hotel", "Hotel"),
        ("restaurant", "Restaurant"),
        ("both", "Both"),
    ]

    name = models.CharField(max_length=200, default="Hearth Property")
    edition = models.CharField(max_length=20, choices=EDITION_CHOICES, blank=True)
    setup_done = models.BooleanField(default=False)
    business_date = models.DateField(null=True, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    currency = models.CharField(max_length=8, default="INR")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "properties"

    def __str__(self):
        return self.name


class Entitlement(models.Model):
    """Per-property feature flags. Drives nav and API gating (deny-by-default)."""

    property = models.OneToOneField(
        Property, on_delete=models.CASCADE, related_name="entitlement"
    )
    hms = models.BooleanField(default=True)
    restaurant = models.BooleanField(default=True)
    banquets = models.BooleanField(default=True)
    rms = models.BooleanField(default=True)

    def as_dict(self):
        return {
            "hms": self.hms,
            "restaurant": self.restaurant,
            "banquets": self.banquets,
            "rms": self.rms,
        }

    def __str__(self):
        return f"Entitlements for {self.property}"


class User(AbstractUser):
    """System operator. Adds role, POS passcode and discount-cap (BRD FR-USR)."""

    CAP_NONE = "none"
    CAP_PERCENT = "percent"
    CAP_FIXED = "fixed"
    CAP_CHOICES = [
        (CAP_NONE, "No cap"),
        (CAP_PERCENT, "Percentage cap"),
        (CAP_FIXED, "Fixed-amount cap"),
    ]

    role = models.CharField(max_length=40, choices=ROLE_CHOICES, default=ROLE_FRONT_OFFICE)
    user_code = models.CharField(max_length=20, blank=True)
    passcode = models.CharField(max_length=12, blank=True, help_text="Numeric POS quick-login")
    phone = models.CharField(max_length=20, blank=True)
    discount_cap_type = models.CharField(max_length=10, choices=CAP_CHOICES, default=CAP_NONE)
    discount_cap_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    rights = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class AuditLog(models.Model):
    """Tamper-evident trail of security-relevant actions (BRD FR-USR-007 / SR-090)."""

    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=80)
    entity = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=40, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.user} @ {self.created_at:%Y-%m-%d %H:%M}"


def log_action(user, action, entity="", entity_id="", before=None, after=None, note=""):
    """Convenience helper used across the domain apps."""
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=before,
        after=after,
        note=note,
    )
