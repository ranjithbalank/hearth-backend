from django.db import models

from apps.rooms.models import Room


class LostFoundItem(models.Model):
    """Lost-and-found log (BRD FR-HSK-007)."""

    STORED = "stored"
    CLAIMED = "claimed"
    DISPOSED = "disposed"
    STATUS_CHOICES = [(STORED, "Stored"), (CLAIMED, "Claimed"), (DISPOSED, "Disposed")]

    description = models.CharField(max_length=200)
    location = models.CharField(max_length=120, blank=True)
    handler = models.CharField(max_length=120, blank=True)
    guest_contact = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STORED)
    found_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-found_at"]

    def __str__(self):
        return f"{self.description} ({self.status})"


class WorkOrder(models.Model):
    """Engineering/maintenance work order (BRD FR-HSK-004). OOO rooms link here."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (IN_PROGRESS, "In Progress"),
        (DONE, "Done"),
    ]

    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="work_orders"
    )
    title = models.CharField(max_length=200)
    detail = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=OPEN)
    raised_by = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.status})"
