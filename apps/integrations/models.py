import random
from datetime import timedelta

from django.db import models
from django.utils import timezone


class SentMessage(models.Model):
    """Outbound notification log (BRD 5.24 / FR-NOT-001)."""

    channel = models.CharField(max_length=12, default="sms")  # sms | whatsapp | email
    to = models.CharField(max_length=120)
    body = models.CharField(max_length=400)
    status = models.CharField(max_length=20, default="sent")
    provider_id = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.channel}->{self.to}: {self.body[:30]}"


class Otp(models.Model):
    """One-time passcode for customer/order verification (FR-NOT-002)."""

    mobile = models.CharField(max_length=20)
    code = models.CharField(max_length=6)
    expires_at = models.DateTimeField()
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, mobile, ttl_minutes=5):
        code = f"{random.randint(0, 999999):06d}"
        return cls.objects.create(
            mobile=mobile, code=code,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )

    def is_valid(self, code):
        return (not self.verified and self.code == str(code).strip()
                and timezone.now() < self.expires_at)
