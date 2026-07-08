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
    # Pay terms: salaried staff carry a monthly gross; casual labour
    # (kitchen helpers, cleaners) a per-day rate paid by attendance.
    MONTHLY = "monthly"
    DAILY = "daily"
    WAGE_CHOICES = [(MONTHLY, "Monthly salary"), (DAILY, "Daily wage")]
    wage_type = models.CharField(max_length=10, choices=WAGE_CHOICES, default=MONTHLY)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Salaried structure: True = the standard basic/HRA/allowances split,
    # False = the whole gross is basic (no allowance components). Note the
    # PF effect: basic is the PF base, so an all-basic structure deducts
    # more PF until the ₹1,800 cap. Ignored for daily wages (always all-basic).
    has_allowances = models.BooleanField(default=True)
    # Whether PF/ESI/PT apply — on the rolls vs casual. Daily-wage staff are
    # typically off-rolls (no deductions), but the flag is independent so a
    # registered daily-rated worker still gets their PF.
    statutory = models.BooleanField(default=True)

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


class LeaveType(models.Model):
    """Property-configurable leave category (FR-HRM leave management).

    `annual_quota` is days per calendar year; 0 means uncapped (Loss of Pay).
    `is_paid` drives the attendance mark on approval — paid leave counts as a
    payable day for payroll, unpaid marks the day absent (0 payable).
    """

    name = models.CharField(max_length=80, unique=True)
    annual_quota = models.PositiveIntegerField(default=0, help_text="Days per year; 0 = no cap")
    is_paid = models.BooleanField(default=True)
    carry_forward = models.BooleanField(
        default=False, help_text="Unused balance rolls into next year (informational)")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.annual_quota or '∞'}/yr, {'paid' if self.is_paid else 'unpaid'})"


class LeaveRequest(models.Model):
    """A leave application moving through two-level approval.

    pending → (department manager, see accounts.constants.leave_approvers_for:
    Housekeeping/Front Office → Hotel Manager, Kitchen/Bar/F&B → Restaurant
    Manager) → manager_approved → (HR final sign-off, LEAVE_FINAL_APPROVERS)
    → approved. GM/MD/Super Admin override at both levels; nobody ever decides
    their own request. Only the FINAL approval marks the covered dates in
    Attendance (paid → 'leave', unpaid → 'absent'), tagged `leave:<id>` so a
    later cancellation can revert exactly the marks it wrote.
    """

    PENDING = "pending"
    MANAGER_APPROVED = "manager_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [(PENDING, "Pending"), (MANAGER_APPROVED, "Manager approved"),
                      (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]
    # Statuses that hold (or will hold) days against the balance / calendar.
    ACTIVE_STATUSES = [PENDING, MANAGER_APPROVED, APPROVED]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.PositiveIntegerField(help_text="Calendar days covered, inclusive")
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    # Usernames, matching the rest of HR/matreq — the employee themselves for
    # self-service, or HR / the manager when filed on behalf of no-login staff.
    requested_by = models.CharField(max_length=120, blank=True)
    # Level 1: the department manager's sign-off.
    manager_decided_by = models.CharField(max_length=120, blank=True)
    manager_decided_at = models.DateTimeField(null=True, blank=True)
    # Level 2: HR's final decision (or rejection/cancellation at any point).
    decided_by = models.CharField(max_length=120, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Leave #{self.id} — {self.employee.name} {self.start_date}→{self.end_date} ({self.status})"


class SalaryAdvance(models.Model):
    """Money given ahead of wages (FR-HRM): a one-off advance or a loan.

    Advances are recovered in full from the next payroll; loans in fixed
    monthly installments (`monthly_installment`). Recovery is allocated when
    a payroll run is created (see AdvanceRecovery) but only APPLIED —
    `recovered` moving, the row settling — when that run is marked paid, so
    a discarded draft never eats into anyone's balance.
    """

    ADVANCE = "advance"
    LOAN = "loan"
    KIND_CHOICES = [(ADVANCE, "Advance"), (LOAN, "Loan")]
    ACTIVE = "active"
    SETTLED = "settled"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="advances")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=ADVANCE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # 0 for advances (recover everything next month); loans repay this much
    # per payroll month.
    monthly_installment = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    recovered = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, default=ACTIVE)
    note = models.CharField(max_length=200, blank=True)
    issued_by = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    @property
    def balance(self):
        return self.amount - self.recovered

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} — {self.employee.name} ({self.status})"


class PayrollRun(models.Model):
    """One month's payroll (FR-HRM payroll): draft → finalized → paid.

    Running it snapshots every active employee's attendance into Payslips —
    later attendance edits never change a finalized month. Draft slips can
    take a manual adjustment (bonus / recovery); finalizing locks the
    numbers, marking paid records the payout.
    """

    DRAFT = "draft"
    FINALIZED = "finalized"
    PAID = "paid"
    STATUS_CHOICES = [(DRAFT, "Draft"), (FINALIZED, "Finalized"), (PAID, "Paid")]

    month = models.CharField(max_length=7, unique=True)   # "2026-07"
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_by = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_by = models.CharField(max_length=120, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return f"Payroll {self.month} ({self.status})"


class Payslip(models.Model):
    """One employee's slip inside a run — a full snapshot (days, split,
    deductions, net) so the payslip stays exactly what was paid. See
    payroll.compute_payslip for the split/deduction rules."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="slips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    days_in_month = models.PositiveIntegerField()
    payable_days = models.DecimalField(max_digits=4, decimal_places=1)
    wage_type = models.CharField(max_length=10, default=Employee.MONTHLY)
    statutory = models.BooleanField(default=True)
    # Contracted terms at run time: the monthly gross, or the per-day rate
    # for daily-wage staff (wage_type says which).
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    basic = models.DecimalField(max_digits=12, decimal_places=2)          # earned ↓
    hra = models.DecimalField(max_digits=12, decimal_places=2)
    other_allowance = models.DecimalField(max_digits=12, decimal_places=2)
    gross_earned = models.DecimalField(max_digits=12, decimal_places=2)
    pf = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    esi = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pt = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Manual bonus (+) or recovery (−) while the run is a draft.
    adjustment = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    adjustment_note = models.CharField(max_length=200, blank=True)
    # Total advance/loan recovery taken off this slip (see AdvanceRecovery
    # for the per-advance breakdown) — already netted into `net` below.
    advance_recovery = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = [("run", "employee")]
        ordering = ["employee__department", "employee__name"]

    def __str__(self):
        return f"{self.employee.name} — {self.run.month} net {self.net}"


class AdvanceRecovery(models.Model):
    """One advance's recovery line on one payslip — planned at run-creation,
    applied (moves SalaryAdvance.recovered, may settle it) only once the run
    is marked paid. Discarding a draft run deletes these with it; nothing
    was ever applied."""

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="recoveries")
    advance = models.ForeignKey(SalaryAdvance, on_delete=models.CASCADE, related_name="recovery_lines")
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = [("payslip", "advance")]

    def __str__(self):
        return f"{self.amount} off {self.advance_id} via payslip {self.payslip_id}"
