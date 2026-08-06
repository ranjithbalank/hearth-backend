"""Per-user read state for the alert panel.

Alerts themselves are not stored — `_build_alerts()` derives every one of them
from live state on each request, so there is nothing to mark read on the alert
itself. What IS worth storing is the fact that a given person has already
looked at a given condition, so the bell can stop shouting about it while it
remains true.
"""
import hashlib
import re

from django.db import models
from django.utils import timezone


def alert_key(alert):
    """A stable identity for a derived alert, so "I've seen this" survives the
    next poll — which rebuilds every alert dict from scratch.

    Digits are normalised out of the title before hashing. Without that,
    "11 room(s) awaiting cleaning" and "10 room(s) awaiting cleaning" are
    different keys, so cleaning a single room would resurrect an alert the
    reader had already acknowledged — the count moving is not new information,
    the condition is the same one. Per-item alerts stay distinct because their
    identity is a name, not a number ("Out of stock: Curd").
    """
    title = re.sub(r"\d+", "#", alert.get("title", ""))
    basis = f"{alert.get('module', '')}|{title}"
    return hashlib.sha1(basis.encode()).hexdigest()


class AlertSeen(models.Model):
    """One row per (user, alert) they have acknowledged by opening the panel.

    Rows are deliberately transient: `purge_stale()` drops any whose condition
    is no longer raised. That is what makes a recurring problem re-alert — if
    curd goes out of stock, is restocked, and runs out again, the second
    outage has to reach the person who fixed the first one.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="alerts_seen")
    key = models.CharField(max_length=40, db_index=True)
    seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("user", "key")]

    @classmethod
    def mark(cls, user, alerts):
        """Acknowledge everything currently on screen for this user."""
        for a in alerts:
            cls.objects.get_or_create(user=user, key=alert_key(a))

    @classmethod
    def purge_stale(cls, user, live_keys):
        """Forget acknowledgements for conditions that have since cleared."""
        cls.objects.filter(user=user).exclude(key__in=live_keys).delete()

    @classmethod
    def seen_keys(cls, user):
        return set(cls.objects.filter(user=user).values_list("key", flat=True))
