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
    # Text alignment for the header/footer letterhead blocks on printed
    # documents — left / center / right (Settings > Letterhead).
    ALIGN_CHOICES = [("left", "Left"), ("center", "Center"), ("right", "Right")]
    doc_header_align = models.CharField(max_length=6, choices=ALIGN_CHOICES, default="left")
    doc_footer_align = models.CharField(max_length=6, choices=ALIGN_CHOICES, default="center")
    # Configurable prefixes for each document type's sequential number
    # (PREFIX-YYYYMM-NNNNN — see apps/accounts/numbering.py).
    invoice_prefix = models.CharField(max_length=12, default="HRT")
    bill_prefix = models.CharField(max_length=12, default="BILL")
    po_prefix = models.CharField(max_length=12, default="PO")
    grn_prefix = models.CharField(max_length=12, default="GRN")
    beo_prefix = models.CharField(max_length=12, default="BEO")
    currency = models.CharField(max_length=8, default="INR")
    # GST Master: with_gst (tax invoice) vs without_gst (bill of supply) — BRD 5.23.
    gst_billing_mode = models.CharField(max_length=12, default="with_gst")
    # Aggregator commission %, so the Zomato/Swiggy report can show net
    # realization after the platform's cut, not just gross sales.
    zomato_commission_pct = models.DecimalField(max_digits=5, decimal_places=2, default=25)
    swiggy_commission_pct = models.DecimalField(max_digits=5, decimal_places=2, default=23)
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
    # Whether the bar runs as its own separate operation (own tables, own
    # menu, Bar Captain/Bar Cashier) or combined into the one restaurant POS.
    # Changeable anytime from Settings — not locked in at initial setup.
    BAR_SEPARATE = "separate"
    BAR_COMBINED = "combined"
    BAR_MODE_CHOICES = [(BAR_SEPARATE, "Separate operation"), (BAR_COMBINED, "Combined with restaurant")]
    bar_mode = models.CharField(max_length=10, choices=BAR_MODE_CHOICES, default=BAR_SEPARATE)
    # Off by default: a ticket's items bump to ready together (course served
    # together). On lets the kitchen mark individual items ready as they
    # finish — the ticket itself auto-advances once every line on it is
    # ready (Settings > Kitchen Display).
    kds_partial_ready = models.BooleanField(default=False)

    def as_dict(self):
        return {
            "hms": self.hms,
            "restaurant": self.restaurant,
            "banquets": self.banquets,
            "rms": self.rms,
            "bar_mode": self.bar_mode,
            "kds_partial_ready": self.kds_partial_ready,
        }

    def __str__(self):
        return f"Entitlements for {self.property}"


class Branch(models.Model):
    """One physical location of the property's group (BRD 5.1 multi-branch).

    `Property` stays the group-level record (brand name, letterhead, currency);
    a `Branch` is a location under it — its own address/GSTIN (GST registration
    is state-specific in India), its own edition/entitlement flags (a branch
    can be restaurant-only while another is hotel+restaurant), and its own
    invoice numbering so two branches never collide on a series.

    Table/room names only need to be unique *within* a branch, not group-wide.
    """

    EDITION_CHOICES = Property.EDITION_CHOICES

    STATUS_ONBOARDING = "onboarding"
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_ONBOARDING, "Onboarding"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. DTN — used in invoice/table numbering")
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    edition = models.CharField(max_length=20, choices=EDITION_CHOICES, default="both")
    # Inline entitlement flags rather than a separate table — one branch, one row.
    hms = models.BooleanField(default=True)
    restaurant = models.BooleanField(default=True)
    banquets = models.BooleanField(default=True)
    rms = models.BooleanField(default=True)
    invoice_prefix = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ONBOARDING)
    logo = models.TextField(blank=True, help_text="branch logo as a data URL; falls back to the group logo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def as_entitlement_dict(self):
        return {"hms": self.hms, "restaurant": self.restaurant,
                "banquets": self.banquets, "rms": self.rms}


class UserBranchAccess(models.Model):
    """Which branch a person operates in, and in what role there (BRD 5.1).

    Additive layer, not a replacement: `User.role` still drives WHAT a role can
    do everywhere (the existing ROLE_ALLOW engine is untouched); this table
    drives WHERE — which branch's data a login actually sees. Super Admin/MD/GM
    need no rows here at all — they get every branch implicitly, same as their
    existing "*" module access.

    `end_date` supports a temporary loan (e.g. a chef covering another branch
    for a season) that reverts on its own without anyone remembering to undo it.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="branch_access")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="staff_access")
    role = models.CharField(max_length=40, choices=ROLE_CHOICES)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True, help_text="Temporary assignment — leave blank for a standing one")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["branch__name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "branch", "role"], name="unique_user_branch_role"),
        ]

    def __str__(self):
        return f"{self.user} — {self.role} @ {self.branch}"

    def is_active_on(self, day):
        if self.start_date and day < self.start_date:
            return False
        if self.end_date and day > self.end_date:
            return False
        return True


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
