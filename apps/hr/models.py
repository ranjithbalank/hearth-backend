from django.conf import settings
from django.db import models


class Employee(models.Model):
    """Staff record (BRD 5.8 FR-HRM-001).

    Deliberately separate from `User`: payroll/attendance covers everyone on
    the roster (kitchen helpers, cleaners) whether or not they ever get a
    system login. `user` links the subset who do; `branch` is this person's
    home base for attendance and payroll regardless of that.
    """

    name = models.CharField(max_length=160)
    department = models.CharField(max_length=80)
    role = models.CharField(max_length=80)
    phone = models.CharField(max_length=20, blank=True)
    branch = models.ForeignKey(
        "accounts.Branch", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="employees", help_text="Home branch for attendance/payroll/rostering",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="employee_record", help_text="Linked login, if this person has system access",
    )
    # Weekly shift pattern, one code per day (M=morning, E=evening, N=night, O=off).
    shifts = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, default="Active")
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["department", "name"]

    def __str__(self):
        return f"{self.name} — {self.role}"


class Attendance(models.Model):
    """Daily attendance mark (FR-HRM: attendance → payroll feed).

    present | half | leave | absent. Payroll counts present=1, half=0.5,
    paid leave=1, absent=0 payable days.
    """

    PRESENT = "present"
    HALF = "half"
    LEAVE = "leave"
    ABSENT = "absent"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=10, default=PRESENT)
    marked_by = models.CharField(max_length=80, blank=True)

    class Meta:
        unique_together = [("employee", "date")]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.employee.name} {self.date} {self.status}"
