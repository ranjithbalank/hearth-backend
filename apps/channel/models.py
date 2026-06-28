from django.db import models

from apps.rooms.models import RoomType


class Channel(models.Model):
    """An OTA / metasearch distribution channel (BRD 5.4)."""

    name = models.CharField(max_length=80, unique=True)
    connected = models.BooleanField(default=True)
    commission_pct = models.DecimalField(max_digits=4, decimal_places=1, default=15)

    def __str__(self):
        return self.name


class ChannelRate(models.Model):
    """Availability, Rates & Inventory (ARI) for one room type on one channel."""

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="rates")
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="channel_rates")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    availability = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("channel", "room_type")]

    def __str__(self):
        return f"{self.channel.name} · {self.room_type.code} @ {self.rate}"


class ChannelPush(models.Model):
    """Audit log of ARI pushes to channels (manual or RMS-driven)."""

    KIND_MANUAL = "manual"
    KIND_RMS = "rms"
    KIND_PARITY = "parity"
    KIND_CHOICES = [(KIND_MANUAL, "Manual"), (KIND_RMS, "RMS"), (KIND_PARITY, "Parity fix")]

    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_MANUAL)
    detail = models.CharField(max_length=200)
    status = models.CharField(max_length=20, default="synced")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind}: {self.detail}"
