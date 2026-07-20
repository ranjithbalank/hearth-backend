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
    # Cumulative points ever earned — never decremented by redemption, so a
    # guest who redeems down to zero doesn't fall out of their tier. Drives
    # tier qualification only; the spendable balance stays loyalty_points.
    lifetime_points = models.IntegerField(default=0)
    date_of_birth = models.DateField(null=True, blank=True)
    anniversary_date = models.DateField(null=True, blank=True)
    marketing_consent = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.mobile})"

    def current_tier(self):
        """The highest active tier this customer's lifetime points qualify
        for — computed on read, never stored, so re-thresholding a tier
        later doesn't require a backfill pass over every customer."""
        return (LoyaltyTier.objects.filter(active=True, min_lifetime_points__lte=self.lifetime_points)
                .order_by("-min_lifetime_points").first())


class LoyaltyTier(models.Model):
    """A loyalty tier (Silver/Gold/Platinum…) unlocked by lifetime points,
    boosting how fast a guest earns further points."""

    name = models.CharField(max_length=40, unique=True)
    min_lifetime_points = models.PositiveIntegerField(default=0)
    earn_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["min_lifetime_points"]

    def __str__(self):
        return self.name


class LoyaltyReward(models.Model):
    """A rewards-catalogue entry a guest can redeem points for at settle,
    instead of typing an arbitrary point amount (mirrors pos.Coupon's
    percent/fixed reduction shape)."""

    PERCENT = "percent"
    FIXED = "fixed"
    KIND_CHOICES = [(PERCENT, "Percent off"), (FIXED, "Fixed amount off")]

    name = models.CharField(max_length=80)
    points_cost = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=FIXED)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)
    redeemed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["points_cost"]

    def __str__(self):
        return self.name

    def reduction(self, subtotal):
        if self.kind == self.PERCENT:
            return subtotal * self.value / 100
        return min(self.value, subtotal)


class LoyaltyLedger(models.Model):
    """Append-only history of every point event — the audit trail the flat
    balance field never had (earn on settle, redeem on settle, birthday/
    anniversary bonus)."""

    EARN = "earn"
    REDEEM = "redeem"
    BONUS = "bonus"
    KIND_CHOICES = [(EARN, "Earned"), (REDEEM, "Redeemed"), (BONUS, "Bonus")]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="loyalty_ledger")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    points = models.IntegerField(help_text="positive for earn/bonus, negative for redeem")
    balance_after = models.IntegerField()
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class LoyaltyGreetingLog(models.Model):
    """Dedup guard for the birthday/anniversary command — without this a
    command re-run on the same day would double-pay the bonus."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="greeting_logs")
    occasion = models.CharField(max_length=20, help_text="birthday | anniversary")
    sent_on = models.DateField()

    class Meta:
        unique_together = ("customer", "occasion", "sent_on")


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
