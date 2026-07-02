from django.db import models

from apps.crm.models import Customer
from apps.rooms.models import RatePlan, Room, RoomType


class Reservation(models.Model):
    SOURCE_DIRECT = "direct"
    SOURCE_OTA = "ota"
    SOURCE_BOOKING = "booking"
    SOURCE_WALKIN = "walkin"
    SOURCE_CHOICES = [
        (SOURCE_DIRECT, "Direct"),
        (SOURCE_OTA, "OTA"),
        (SOURCE_BOOKING, "Booking Engine"),
        (SOURCE_WALKIN, "Walk-in"),
    ]

    BOOKED = "booked"
    IN_HOUSE = "in_house"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    STATUS_CHOICES = [
        (BOOKED, "Booked"),
        (IN_HOUSE, "In House"),
        (CHECKED_OUT, "Checked Out"),
        (CANCELLED, "Cancelled"),
        (NO_SHOW, "No Show"),
    ]

    guest = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="reservations", null=True, blank=True
    )
    guest_name = models.CharField(max_length=200)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="reservations")
    rate_plan = models.ForeignKey(
        RatePlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="reservations"
    )
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="reservations"
    )
    checkin_date = models.DateField()
    checkout_date = models.DateField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_DIRECT)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=BOOKED)
    nights = models.PositiveSmallIntegerField(default=1)
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    prepaid = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)
    # Channel/OTA of origin (e.g. "Booking.com") + its external ref, for inbound
    # bookings. ota_ref makes re-delivered webhooks idempotent.
    channel_name = models.CharField(max_length=80, blank=True)
    ota_ref = models.CharField(max_length=80, blank=True, db_index=True)
    # Guest self check-in (pre-arrival web form): captured details + flag.
    # {"mobile", "email", "id_type", "id_number", "eta", "note"}
    precheckin = models.JSONField(default=dict, blank=True)
    precheckin_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["checkin_date", "guest_name"]

    def __str__(self):
        return f"{self.guest_name} · {self.room_type.code} · {self.checkin_date}"


class GroupBlock(models.Model):
    """A block/allotment that holds inventory for a group (BRD FR-PMS-009)."""

    name = models.CharField(max_length=160)
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="group_blocks")
    rooms_blocked = models.PositiveSmallIntegerField(default=1)
    rooms_picked = models.PositiveSmallIntegerField(default=0)
    checkin_date = models.DateField()
    checkout_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["checkin_date"]

    @property
    def outstanding(self):
        return max(0, self.rooms_blocked - self.rooms_picked)

    def __str__(self):
        return f"{self.name} ({self.room_type.code} ×{self.rooms_blocked})"
