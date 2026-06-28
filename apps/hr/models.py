from django.db import models


class Employee(models.Model):
    """Staff record (BRD 5.8 FR-HRM-001)."""

    name = models.CharField(max_length=160)
    department = models.CharField(max_length=80)
    role = models.CharField(max_length=80)
    phone = models.CharField(max_length=20, blank=True)
    # Weekly shift pattern, one code per day (M=morning, E=evening, N=night, O=off).
    shifts = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, default="Active")

    class Meta:
        ordering = ["department", "name"]

    def __str__(self):
        return f"{self.name} — {self.role}"
