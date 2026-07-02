from django.db import models


class RoomType(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    base_rate = models.DecimalField(max_digits=10, decimal_places=2)
    max_occupancy = models.PositiveSmallIntegerField(default=2)
    gst_slab = models.DecimalField(max_digits=4, decimal_places=1, default=12)

    def __str__(self):
        return self.name


class RatePlan(models.Model):
    name = models.CharField(max_length=120)
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="rate_plans")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    inclusions = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.name} — {self.room_type.code}"


class Room(models.Model):
    # Live status values mirror the prototype's colour-coded grid.
    VACANT_CLEAN = "vacant_clean"
    VACANT_DIRTY = "vacant_dirty"
    OCCUPIED = "occupied"
    CLEANING = "cleaning"
    INSPECTED = "inspected"
    OOO = "ooo"  # out of order / out of service
    STATUS_CHOICES = [
        (VACANT_CLEAN, "Vacant / Clean"),
        (VACANT_DIRTY, "Vacant / Dirty"),
        (OCCUPIED, "Occupied"),
        (CLEANING, "Cleaning"),
        (INSPECTED, "Inspected"),
        (OOO, "Out of Order"),
    ]
    SELLABLE = {VACANT_CLEAN, INSPECTED}

    number = models.CharField(max_length=12)
    branch = models.CharField(max_length=80, default="Main")
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="rooms")
    floor = models.PositiveSmallIntegerField(default=1)
    view = models.CharField(max_length=40, blank=True)
    smoking = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=VACANT_CLEAN)
    ooo_reason = models.CharField(max_length=200, blank=True)
    # Front-desk cleaning request (FR-HSK): flags the room on the housekeeping
    # board — works for occupied rooms (make-up-room) and vacant/dirty alike.
    cleaning_requested = models.BooleanField(default=False)
    cleaning_note = models.CharField(max_length=160, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch", "number"]
        unique_together = [("branch", "number")]

    def __str__(self):
        return f"{self.branch} {self.number}"

    @property
    def is_sellable(self):
        return self.status in self.SELLABLE
