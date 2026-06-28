from django.db import models

from apps.rooms.models import RoomType


class RateRecommendation(models.Model):
    """A dynamic-pricing suggestion awaiting approval (BRD FR-RMS-003)."""

    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    STATUS_CHOICES = [(OPEN, "Open"), (ACCEPTED, "Accepted"), (DISMISSED, "Dismissed")]

    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="rate_recs")
    current_rate = models.DecimalField(max_digits=10, decimal_places=2)
    recommended_rate = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200)
    demand_index = models.PositiveSmallIntegerField(default=50, help_text="0-100 demand signal")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.room_type.code}: {self.current_rate}→{self.recommended_rate} ({self.status})"
