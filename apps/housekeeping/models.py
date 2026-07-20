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


class ChecklistItem(models.Model):
    """One line of the default room-cleaning checklist (Settings > Masters).
    Snapshotted onto each HousekeepingTask at creation — editing this list
    later never rewrites history."""

    label = models.CharField(max_length=120, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label


class LinenItem(models.Model):
    """A linen/amenity item tracked per room turn (Settings > Masters). This
    is a lightweight per-task log, not wired into the inventory stock
    ledger — that's a separate, bigger cross-module integration."""

    name = models.CharField(max_length=80, unique=True)
    par_per_room = models.PositiveSmallIntegerField(default=1, help_text="standard quantity per room turn")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class HousekeepingTask(models.Model):
    """One attendant's work session on a room turn: assignment, checklist,
    timer and linen issued. Purely additive on top of the existing shared
    Dirty→Cleaning→Clean→Inspected board — a property that never assigns a
    task can keep using HousekeepingViewSet.advance exactly as before."""

    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    STATUS_CHOICES = [
        (ASSIGNED, "Assigned"),
        (IN_PROGRESS, "In Progress"),
        (DONE, "Done"),
    ]

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="housekeeping_tasks")
    assigned_to = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="housekeeping_tasks",
        help_text="Attendant this task is assigned to — must hold the Housekeeping role",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ASSIGNED)
    checklist = models.JSONField(default=list, help_text="snapshot: [{label, done}]")
    linen_issued = models.JSONField(default=dict, blank=True, help_text="{linen item name: qty}")
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.room.number} ({self.status})"
