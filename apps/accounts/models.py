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
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    logo = models.TextField(blank=True, help_text="hotel logo as a data URL (white-label)")
    # Company letterhead blocks printed on invoices/bills. Simple markup
    # (<b>, <i>) is allowed — edited in Settings > Letterhead.
    doc_header = models.TextField(blank=True, default="",
                                  help_text="extra letterhead lines (tagline, CIN, FSSAI…)")
    doc_footer = models.TextField(blank=True, default="",
                                  help_text="terms & conditions / bank details footer")
    currency = models.CharField(max_length=8, default="INR")
    # GST Master: with_gst (tax invoice) vs without_gst (bill of supply) — BRD 5.23.
    gst_billing_mode = models.CharField(max_length=12, default="with_gst")
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
    # MFA / TOTP (BRD SR-040)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class RoleConfig(models.Model):
    """Editable per-role module allow-list (BRD FR-USR-002 / 5.10 role mapping).

    Overrides the built-in ROLE_ALLOW constant when present. Managing Director and
    General Manager are always full-access and are not stored/editable here.
    """

    role = models.CharField(max_length=40, unique=True)
    modules = models.JSONField(default=list)

    def __str__(self):
        return f"RoleConfig {self.role} ({len(self.modules)} modules)"


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

    def save(self, *args, **kwargs):
        # Append-only: once written, an audit row cannot be modified (SR-090, NFR-006).
        if self.pk is not None:
            raise PermissionError("Audit log entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")


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
