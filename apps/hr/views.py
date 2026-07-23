import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import ROLE_CHOICES
from apps.accounts.models import User, UserBranchAccess, log_action
from apps.accounts.permissions import (
    AnyModuleViewSetMixin,
    ModuleViewSetMixin,
    resolve_active_branch,
    shared_or_visible,
)
from apps.accounts.rbac import PROTECTED

from .models import (
    AdvanceRecovery,
    Attendance,
    Employee,
    Invite,
    LeaveRequest,
    LeaveType,
    PayrollRun,
    Payslip,
    SalaryAdvance,
)
from .payroll import compute_payslip


def _validate_employee_row(data):
    """Shared by HrViewSet.create (one row typed in) and import_employees
    (one row from a spreadsheet): validates a raw {name, department, role,
    phone?, wage_type?, monthly_salary?, daily_rate?, branch?} dict and
    returns (kwargs for Employee.objects.create, None) on success, or
    (None, "error message") on failure — never raises."""
    from apps.accounts.validators import validate_digits, validate_person_name
    from apps.masters.models import Department, Designation
    from rest_framework.serializers import ValidationError as DRFValidationError

    name = (data.get("name") or "").strip()
    department = (data.get("department") or "").strip()
    role = (data.get("role") or "").strip()
    if not name or not department or not role:
        return None, "name, department and role are required"
    try:
        validate_person_name(name)
        validate_digits(data.get("phone", ""), field="Phone", max_len=15)
    except DRFValidationError as e:
        return None, e.detail[0] if isinstance(e.detail, list) else str(e.detail)
    # Department and designation must be active rows in the masters
    # (Settings > Masters) — same pattern as Ingredient.unit vs UoM.
    if not Department.objects.filter(name=department, active=True).exists():
        return None, f"'{department}' is not an active department"
    if not Designation.objects.filter(name=role, active=True).exists():
        return None, f"'{role}' is not an active designation"
    wage_type = data.get("wage_type") or Employee.MONTHLY
    if wage_type not in (Employee.MONTHLY, Employee.DAILY):
        return None, "wage_type must be monthly or daily"
    try:
        monthly_salary = Decimal(str(data.get("monthly_salary") or 0))
        daily_rate = Decimal(str(data.get("daily_rate") or 0))
    except InvalidOperation:
        return None, "monthly_salary/daily_rate must be numbers"
    return {
        "name": name, "department": department, "role": role,
        "phone": data.get("phone", ""), "wage_type": wage_type,
        "monthly_salary": monthly_salary, "daily_rate": daily_rate,
        "statutory": bool(data.get("statutory", wage_type == Employee.MONTHLY)),
        "has_allowances": bool(data.get("has_allowances", True)),
    }, None


def _employee_dict(e):
    return {
        "id": e.id, "name": e.name, "department": e.department, "role": e.role,
        "phone": e.phone, "shifts": e.shifts, "status": e.status,
        "wage_type": e.wage_type, "monthly_salary": str(e.monthly_salary),
        "daily_rate": str(e.daily_rate), "statutory": e.statutory,
        "has_allowances": e.has_allowances,
        "branch": e.branch_id, "branch_name": e.branch.name if e.branch_id else None,
        "user": e.user_id,
    }


class HrViewSet(AnyModuleViewSetMixin, viewsets.ViewSet):
    # Serves two desks: the HR module proper (attendance, payroll) and the
    # Employees master screen, which Admin reaches via "employees" without
    # holding the full "hr" module.
    modules = ["hr", "employees"]

    def list(self, request):
        # Payroll/attendance covers everyone on the roster whether or not
        # they have a branch yet — unassigned rows stay visible to all,
        # same "mine + not-yet-tagged" rule used everywhere else.
        qs = shared_or_visible(Employee.objects.select_related("branch"), request, field="branch")
        return Response([_employee_dict(e) for e in qs])

    def create(self, request):
        """Add a staff record: {name, department, role, phone?, monthly_salary?, branch?}.
        `branch` defaults to the caller's own branch when they're only ever
        assigned to one — no picker needed for the common case."""
        kwargs, error = _validate_employee_row(request.data)
        if error:
            return Response({"detail": error}, status=400)
        branch_id = request.data.get("branch") or resolve_active_branch(request)
        e = Employee.objects.create(branch_id=branch_id, **kwargs)
        log_action(request.user, "employee_add", entity="Employee", entity_id=e.id,
                   after={"name": kwargs["name"], "department": kwargs["department"], "branch": branch_id})
        return Response(_employee_dict(e), status=201)

    @action(detail=False, methods=["get", "post"], url_path="import")
    def import_employees(self, request):
        """Bulk onboarding: upload a CSV/XLSX of staff — same shared plumbing
        as every other master (apps.accounts.csv_import), same skip-existing
        + per-row-error shape the Import card on the frontend expects.

        GET returns the fill-in template. POST with a `file` creates every
        valid row, reusing the exact same checks as create() via
        _validate_employee_row. Unlike ingredients, department/designation
        are never auto-created here — they drive payroll/org structure, so
        an unknown value is a row error, not something to silently invent."""
        from apps.accounts.csv_import import parse_upload, template_response
        from apps.accounts.models import Branch
        from apps.masters.models import Department, Designation

        columns = ["name", "department", "role", "phone", "wage_type",
                   "monthly_salary", "daily_rate", "branch"]
        if request.method == "GET":
            dept = Department.objects.filter(active=True).values_list("name", flat=True).first() or "Kitchen"
            desig = Designation.objects.filter(active=True).values_list("name", flat=True).first() or "Cook"
            return template_response("employees-template.csv", columns, [
                ["Anita Sharma", dept, desig, "9000000001", "monthly", "18000", "", ""],
                ["Ravi Kumar", dept, desig, "9000000002", "daily", "", "700", ""],
            ])

        try:
            rows = parse_upload(request)
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        created, skipped, errors = [], [], []
        for lineno, row in rows:
            name = row.get("name", "")
            if not name and not row.get("department") and not row.get("role"):
                continue  # blank line
            if name and Employee.objects.filter(name__iexact=name).exists():
                skipped.append(name)
                continue
            kwargs, error = _validate_employee_row(row)
            if error:
                errors.append({"row": lineno, "name": name, "reason": error})
                continue
            branch_id = None
            branch_name = row.get("branch")
            if branch_name:
                branch = Branch.objects.filter(name=branch_name).first()
                if not branch:
                    errors.append({"row": lineno, "name": kwargs["name"],
                                   "reason": f"'{branch_name}' is not a known branch"})
                    continue
                branch_id = branch.id
            else:
                branch_id = resolve_active_branch(request)
            e = Employee.objects.create(branch_id=branch_id, **kwargs)
            log_action(request.user, "employee_add", entity="Employee", entity_id=e.id,
                       after={"name": kwargs["name"], "department": kwargs["department"], "branch": branch_id})
            created.append(kwargs["name"])
        log_action(request.user, "employee_import", entity="Employee",
                   after={"created": len(created), "errors": len(errors)})
        return Response({"created": len(created), "skipped_existing": skipped, "errors": errors})

    @action(detail=True, methods=["post"])
    def set_status(self, request, pk=None):
        """Toggle Active/Inactive — inactive staff drop off attendance & payroll."""
        e = Employee.objects.filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        status_ = request.data.get("status")
        if status_ not in ("Active", "Inactive"):
            return Response({"detail": "status must be Active or Inactive"}, status=400)
        before = e.status
        e.status = status_
        e.save(update_fields=["status"])
        log_action(request.user, "employee_status", entity="Employee", entity_id=e.id,
                   before={"status": before}, after={"status": status_})
        return Response(_employee_dict(e))

    @action(detail=True, methods=["post"])
    def invite(self, request, pk=None):
        """Generate a self-onboarding link for this employee: {role}.
        Copy-only — no live email/SMS provider is wired up, so HR shares the
        link however they normally would (WhatsApp, in person, etc.)."""
        e = Employee.objects.filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        if e.user_id:
            return Response({"detail": f"{e.name} already has a login ({e.user.username})"}, status=400)
        role = (request.data.get("role") or "").strip()
        valid_roles = {r for r, _ in ROLE_CHOICES}
        if role not in valid_roles:
            return Response({"detail": "Choose a valid role"}, status=400)
        if role in PROTECTED:
            return Response(
                {"detail": "Super Admin / Managing Director / General Manager accounts "
                            "can't be self-onboarded — set these up directly in Settings."},
                status=400,
            )
        inv = Invite.issue(employee=e, role=role, created_by=request.user)
        log_action(request.user, "invite_created", entity="Employee", entity_id=e.id,
                   after={"role": role})
        return Response({"token": inv.token, "expires_at": inv.expires_at}, status=201)

    @action(detail=False, methods=["get"])
    def attendance(self, request):
        """Attendance marks for a date (default today) — the muster roll."""
        from django.utils import timezone
        day = request.query_params.get("date") or str(timezone.localdate())
        marks = {str(a.employee_id): a.status for a in Attendance.objects.filter(date=day)}
        return Response({"date": day, "marks": marks})

    @action(detail=False, methods=["post"])
    def mark_attendance(self, request):
        """Bulk mark: {date, marks: {employee_id: present|half|leave|absent}}."""
        from django.utils import timezone
        day = request.data.get("date") or str(timezone.localdate())
        marks = request.data.get("marks", {})
        valid = {Attendance.PRESENT, Attendance.HALF, Attendance.LEAVE, Attendance.ABSENT}
        saved = 0
        for emp_id, status_ in marks.items():
            if status_ not in valid:
                continue
            if not Employee.objects.filter(pk=emp_id).exists():
                continue
            Attendance.objects.update_or_create(
                employee_id=emp_id, date=day,
                defaults={"status": status_, "marked_by": request.user.username})
            saved += 1
        log_action(request.user, "attendance_mark", entity="Attendance",
                   after={"date": day, "count": saved})
        return Response({"date": day, "saved": saved})

    def update(self, request, pk=None):
        """Edit a staff record — salary revision, phone, department move,
        Active/Inactive. Only sent fields change."""
        # Same branch scoping as list() (security review 2026-07, finding B7).
        e = shared_or_visible(Employee.objects.all(), request, field="branch").filter(pk=pk).first()
        if not e:
            return Response({"detail": "not found"}, status=404)
        before = {"salary": str(e.monthly_salary), "department": e.department, "status": e.status}
        for field in ("name", "department", "role", "phone", "status"):
            if field in request.data:
                value = (request.data.get(field) or "").strip()
                if field in ("name", "department", "role") and not value:
                    return Response({"detail": f"{field} cannot be empty"}, status=400)
                setattr(e, field, value)
        for money_field in ("monthly_salary", "daily_rate"):
            if money_field in request.data:
                try:
                    amount = Decimal(str(request.data.get(money_field) or 0))
                except ArithmeticError:
                    return Response({"detail": f"{money_field} must be a number"}, status=400)
                if amount < 0:
                    return Response({"detail": f"{money_field} cannot be negative"}, status=400)
                setattr(e, money_field, amount)
        if "wage_type" in request.data:
            if request.data["wage_type"] not in (Employee.MONTHLY, Employee.DAILY):
                return Response({"detail": "wage_type must be monthly or daily"}, status=400)
            e.wage_type = request.data["wage_type"]
        if "statutory" in request.data:
            e.statutory = bool(request.data["statutory"])
        if "has_allowances" in request.data:
            e.has_allowances = bool(request.data["has_allowances"])
        if "shifts" in request.data and isinstance(request.data.get("shifts"), list):
            e.shifts = request.data["shifts"]
        if "branch" in request.data:
            branch_id = request.data.get("branch") or None
            e.branch_id = branch_id
        e.save()
        log_action(request.user, "employee_update", entity="Employee", entity_id=e.id,
                   before=before,
                   after={"salary": str(e.monthly_salary), "department": e.department,
                          "status": e.status})
        return Response(_employee_dict(e))

    # A plain ViewSet (not ModelViewSet) doesn't get partial_update for free —
    # the router sends PATCH here and 405s without it. update() already only
    # touches fields present in request.data, so it's partial by construction.
    partial_update = update

    # --- Payroll (FR-HRM): attendance-driven, snapshotted per month ---

    @staticmethod
    def _month_bounds(month):
        year, mon = int(month[:4]), int(month[5:7])
        days_in_month = calendar.monthrange(year, mon)[1]
        return date(year, mon, 1), date(year, mon, days_in_month), days_in_month

    @staticmethod
    def _payable_days(employee, first, last):
        """present/paid-leave = 1 day, half = 0.5, absent/unmarked = 0 —
        the same weights the muster roll has always used."""
        weights = {Attendance.PRESENT: Decimal("1"), Attendance.LEAVE: Decimal("1"),
                   Attendance.HALF: Decimal("0.5"), Attendance.ABSENT: Decimal("0")}
        marks = Attendance.objects.filter(employee=employee, date__range=(first, last))
        return sum(weights.get(a.status, Decimal("0")) for a in marks), marks.count()

    @staticmethod
    def _slip_dict(s, days_marked=None):
        return {
            "payslip": s.pk, "id": s.employee_id, "name": s.employee.name,
            "department": s.employee.department, "role": s.employee.role,
            "wage_type": s.wage_type, "statutory": s.statutory,
            "monthly_salary": str(s.gross_salary),
            "days_marked": days_marked, "payable_days": str(s.payable_days),
            "basic": str(s.basic), "hra": str(s.hra), "other_allowance": str(s.other_allowance),
            "gross_earned": str(s.gross_earned),
            "pf": str(s.pf), "esi": str(s.esi), "pt": str(s.pt),
            "adjustment": str(s.adjustment), "adjustment_note": s.adjustment_note,
            "advance_recovery": str(s.advance_recovery),
            "net": str(s.net), "payable": str(s.net),
        }

    @staticmethod
    def _plan_recovery(employee, available):
        """Which active advances/loans get recovered from this employee's
        pay this month, oldest first, never exceeding the net earned.
        Advances recover in full; loans recover their fixed installment
        (or whatever's left if that's less)."""
        lines = []
        remaining = available
        for adv in SalaryAdvance.objects.filter(
                employee=employee, status=SalaryAdvance.ACTIVE).order_by("created_at"):
            balance = adv.balance
            if balance <= 0 or remaining <= 0:
                continue
            wanted = balance if adv.kind == SalaryAdvance.ADVANCE else min(adv.monthly_installment or balance, balance)
            planned = min(wanted, remaining)
            if planned <= 0:
                continue
            lines.append((adv, planned))
            remaining -= planned
        return lines

    @staticmethod
    def _run_dict(run):
        return {"id": run.id, "month": run.month, "status": run.status,
                "created_by": run.created_by, "finalized_by": run.finalized_by,
                "finalized_at": run.finalized_at, "paid_by": run.paid_by, "paid_at": run.paid_at}

    @action(detail=False, methods=["get"])
    def payroll(self, request):
        """The month's payroll sheet. Once a run exists its snapshot is
        served verbatim (that IS the payroll); before that, a live preview
        from attendance: gross split into basic/HRA/allowances, prorated by
        payable days, PF/ESI/PT deducted — see payroll.compute_payslip."""
        from django.utils import timezone
        month = request.query_params.get("month") or timezone.localdate().strftime("%Y-%m")
        first, last, days_in_month = self._month_bounds(month)
        run = PayrollRun.objects.filter(month=month).first()
        if run:
            rows = [self._slip_dict(s) for s in run.slips.select_related("employee")]
        else:
            rows = []
            for e in Employee.objects.filter(status="Active"):
                payable_days, days_marked = self._payable_days(e, first, last)
                money = compute_payslip(e, payable_days, days_in_month)
                recovery = sum(p for _, p in self._plan_recovery(e, money["net"]))
                net = money["net"] - recovery
                rows.append({
                    "payslip": None, "id": e.id, "name": e.name,
                    "department": e.department, "role": e.role,
                    "wage_type": e.wage_type, "statutory": e.statutory,
                    "monthly_salary": str(e.daily_rate if e.wage_type == Employee.DAILY
                                          else e.monthly_salary),
                    "days_marked": days_marked, "payable_days": str(payable_days),
                    **{k: str(v) for k, v in money.items() if k != "net"},
                    "adjustment": "0", "adjustment_note": "",
                    "advance_recovery": str(recovery),
                    "net": str(net), "payable": str(net),
                })
        total = sum(Decimal(r["net"]) for r in rows)
        return Response({"month": month, "days_in_month": days_in_month,
                         "run": self._run_dict(run) if run else None,
                         "rows": rows, "total_payable": str(total)})

    @action(detail=False, methods=["get"])
    def payslip_pdf(self, request):
        """Printable payslip: ?payslip=<id> → application/pdf."""
        from django.http import HttpResponse

        from apps.accounts.models import Property

        from .payslip_pdf import build_payslip_pdf
        s = Payslip.objects.select_related("run", "employee").filter(
            pk=request.query_params.get("payslip")).first()
        if not s:
            return Response({"detail": "not found"}, status=404)
        prop = Property.objects.first()
        pdf = build_payslip_pdf(s, prop.name if prop else "Hearth")
        resp = HttpResponse(pdf.read(), content_type="application/pdf")
        resp["Content-Disposition"] = (
            f'inline; filename="payslip-{s.employee.name.replace(" ", "-")}-{s.run.month}.pdf"')
        return resp

    @action(detail=False, methods=["get"])
    def overview(self, request):
        """Today at a glance for the HR landing: headcount, muster summary,
        who's on approved leave, and the monthly wage bill estimate."""
        from django.utils import timezone
        today = timezone.localdate()
        active = Employee.objects.filter(status="Active")
        marks = {a.employee_id: a.status for a in Attendance.objects.filter(date=today)}
        counts = {"present": 0, "half": 0, "leave": 0, "absent": 0, "unmarked": 0}
        for e in active:
            counts[marks.get(e.id, "unmarked")] = counts.get(marks.get(e.id, "unmarked"), 0) + 1
        on_leave = LeaveRequest.objects.filter(
            status=LeaveRequest.APPROVED, start_date__lte=today, end_date__gte=today,
        ).select_related("employee", "leave_type")
        salaried = active.filter(wage_type=Employee.MONTHLY)
        casuals = active.filter(wage_type=Employee.DAILY)
        # Casual cost estimated at 26 working days a month.
        wage_bill = (sum((e.monthly_salary or Decimal("0")) for e in salaried)
                     + sum((e.daily_rate or Decimal("0")) * 26 for e in casuals))
        return Response({
            "date": str(today),
            "headcount": active.count(), "salaried": salaried.count(), "casual": casuals.count(),
            "today": counts,
            "on_leave": [{"employee": r.employee.name, "type": r.leave_type.name,
                          "until": str(r.end_date)} for r in on_leave],
            "monthly_wage_bill": str(wage_bill),
        })

    @action(detail=False, methods=["post"])
    def run_payroll(self, request):
        """Create the month's draft run: {month}. Snapshots every active
        employee's attendance into payslips — attendance edits after this
        point don't move the numbers (delete the draft and rerun instead)."""
        from apps.accounts.constants import PAYROLL_MANAGER_ROLES
        if getattr(request.user, "role", "") not in PAYROLL_MANAGER_ROLES:
            return Response({"detail": "only HR or Finance can run payroll"}, status=403)
        month = request.data.get("month") or ""
        try:
            first, last, days_in_month = self._month_bounds(month)
        except (ValueError, IndexError):
            return Response({"detail": "month must look like 2026-07"}, status=400)
        if PayrollRun.objects.filter(month=month).exists():
            return Response({"detail": f"payroll for {month} already exists"}, status=400)
        with transaction.atomic():
            run = PayrollRun.objects.create(month=month, created_by=request.user.username)
            for e in Employee.objects.filter(status="Active"):
                payable_days, _ = self._payable_days(e, first, last)
                money = compute_payslip(e, payable_days, days_in_month)
                lines = self._plan_recovery(e, money["net"])
                recovery = sum(p for _, p in lines)
                slip = Payslip.objects.create(
                    run=run, employee=e, days_in_month=days_in_month,
                    payable_days=payable_days, wage_type=e.wage_type,
                    statutory=e.statutory,
                    gross_salary=(e.daily_rate if e.wage_type == Employee.DAILY
                                  else e.monthly_salary) or 0,
                    **{**money, "net": money["net"] - recovery},
                    advance_recovery=recovery)
                for adv, amount in lines:
                    AdvanceRecovery.objects.create(payslip=slip, advance=adv, amount=amount)
        log_action(request.user, "payroll_run", entity="PayrollRun", entity_id=run.id,
                   after={"month": month, "slips": run.slips.count()})
        return Response(self._run_dict(run), status=201)

    @action(detail=False, methods=["post"])
    def adjust_payslip(self, request):
        """Manual bonus (+) or recovery (−) on one draft slip:
        {payslip, amount, note} — net is recomputed, the note explains why."""
        from apps.accounts.constants import PAYROLL_MANAGER_ROLES
        if getattr(request.user, "role", "") not in PAYROLL_MANAGER_ROLES:
            return Response({"detail": "only HR or Finance can adjust payroll"}, status=403)
        s = Payslip.objects.select_related("run", "employee").filter(
            pk=request.data.get("payslip")).first()
        if not s:
            return Response({"detail": "not found"}, status=404)
        if s.run.status != PayrollRun.DRAFT:
            return Response({"detail": f"payroll {s.run.month} is {s.run.status} — no more changes"},
                            status=400)
        try:
            amount = Decimal(str(request.data.get("amount") or 0)).quantize(Decimal("0.01"))
        except ArithmeticError:
            return Response({"detail": "amount must be a number"}, status=400)
        s.net = s.net - s.adjustment + amount
        s.adjustment = amount
        s.adjustment_note = (request.data.get("note") or "").strip()
        s.save(update_fields=["adjustment", "adjustment_note", "net"])
        log_action(request.user, "payslip_adjust", entity="Payslip", entity_id=s.id,
                   after={"employee": s.employee.name, "amount": str(amount),
                          "note": s.adjustment_note})
        return Response(self._slip_dict(s))

    @action(detail=False, methods=["post"])
    def advance_payroll(self, request):
        """Move the month forward: {month} — draft → finalized (numbers lock)
        → paid (payout recorded). A draft can instead be deleted with
        {month, action: "discard"} to rerun after attendance corrections."""
        from apps.accounts.constants import PAYROLL_MANAGER_ROLES
        if getattr(request.user, "role", "") not in PAYROLL_MANAGER_ROLES:
            return Response({"detail": "only HR or Finance can run payroll"}, status=403)
        run = PayrollRun.objects.filter(month=request.data.get("month")).first()
        if not run:
            return Response({"detail": "no payroll run for that month"}, status=404)
        if request.data.get("action") == "discard":
            if run.status != PayrollRun.DRAFT:
                return Response({"detail": f"payroll is {run.status} — it can no longer be discarded"},
                                status=400)
            log_action(request.user, "payroll_discard", entity="PayrollRun", entity_id=run.id,
                       after={"month": run.month})
            run.delete()
            return Response({"discarded": True})
        if run.status == PayrollRun.DRAFT:
            run.status = PayrollRun.FINALIZED
            run.finalized_by = request.user.username
            run.finalized_at = timezone.now()
            run.save(update_fields=["status", "finalized_by", "finalized_at"])
            log_action(request.user, "payroll_finalized", entity="PayrollRun", entity_id=run.id)
        elif run.status == PayrollRun.FINALIZED:
            with transaction.atomic():
                run.status = PayrollRun.PAID
                run.paid_by = request.user.username
                run.paid_at = timezone.now()
                run.save(update_fields=["status", "paid_by", "paid_at"])
                # Only NOW do the planned recoveries actually count against
                # the advance/loan balance — a discarded draft never did.
                for rec in AdvanceRecovery.objects.filter(payslip__run=run).select_related("advance"):
                    adv = rec.advance
                    adv.recovered += rec.amount
                    if adv.recovered >= adv.amount:
                        adv.status = SalaryAdvance.SETTLED
                    adv.save(update_fields=["recovered", "status"])
            log_action(request.user, "payroll_paid", entity="PayrollRun", entity_id=run.id,
                       after={"month": run.month})
        else:
            return Response({"detail": "this month is already paid"}, status=400)
        return Response(self._run_dict(run))


def _leave_dict(r):
    from apps.accounts.constants import LEAVE_FINAL_APPROVERS, leave_approvers_for
    # Whose desk this waits on right now — the department manager while
    # pending, HR once manager-approved. Shown on the card, same as matreq.
    if r.status == LeaveRequest.PENDING:
        waiting_on = sorted(leave_approvers_for(r.employee.department))
    elif r.status == LeaveRequest.MANAGER_APPROVED:
        waiting_on = sorted(LEAVE_FINAL_APPROVERS)
    else:
        waiting_on = []
    return {
        "id": r.id,
        "employee": r.employee_id, "employee_name": r.employee.name,
        "department": r.employee.department,
        "leave_type": r.leave_type_id, "leave_type_name": r.leave_type.name,
        "is_paid": r.leave_type.is_paid,
        "start_date": str(r.start_date), "end_date": str(r.end_date), "days": r.days,
        "reason": r.reason, "status": r.status,
        "requested_by": r.requested_by,
        "manager_decided_by": r.manager_decided_by, "manager_decided_at": r.manager_decided_at,
        "decided_by": r.decided_by, "decided_at": r.decided_at,
        "decision_note": r.decision_note,
        "created_at": r.created_at,
        "approver_roles": waiting_on,
    }


def _type_dict(t):
    return {"id": t.id, "name": t.name, "annual_quota": t.annual_quota,
            "is_paid": t.is_paid, "carry_forward": t.carry_forward, "active": t.active}


def _leave_balances(employee, year):
    """Per-type usage for a calendar year. Approved days consume the quota;
    days still in the approval pipeline (pending or manager-approved) are
    shown — and counted at request time — so an employee can't over-book
    while an application is still moving through the two levels."""
    taken = LeaveRequest.objects.filter(
        employee=employee, start_date__year=year,
        status__in=LeaveRequest.ACTIVE_STATUSES,
    )
    used, pending = {}, {}
    for r in taken:
        bucket = used if r.status == LeaveRequest.APPROVED else pending
        bucket[r.leave_type_id] = bucket.get(r.leave_type_id, 0) + r.days
    rows = []
    for t in LeaveType.objects.filter(active=True):
        u, p = used.get(t.id, 0), pending.get(t.id, 0)
        rows.append({
            **_type_dict(t),
            "used": u, "pending": p,
            # None == uncapped (Loss of Pay)
            "remaining": (t.annual_quota - u - p) if t.annual_quota else None,
        })
    return rows


def _advance_dict(a):
    return {
        "id": a.id, "employee": a.employee_id, "employee_name": a.employee.name,
        "department": a.employee.department, "kind": a.kind,
        "amount": f"{a.amount:.2f}", "monthly_installment": f"{a.monthly_installment:.2f}",
        "recovered": f"{a.recovered:.2f}", "balance": f"{a.balance:.2f}", "status": a.status,
        "note": a.note, "issued_by": a.issued_by, "created_at": a.created_at,
    }


class SalaryAdvanceViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Salary advances & loans (FR-HRM payroll): HR/Finance issue them,
    payroll recovers them automatically — see HrViewSet._plan_recovery and
    run_payroll/advance_payroll for how recovery is planned then applied."""

    module = "hr"

    def list(self, request):
        qs = SalaryAdvance.objects.select_related("employee")
        employee_id = request.query_params.get("employee")
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response([_advance_dict(a) for a in qs])

    def create(self, request):
        """Issue one: {employee, kind: advance|loan, amount,
        monthly_installment? (loans), note?}."""
        from apps.accounts.constants import PAYROLL_MANAGER_ROLES
        if getattr(request.user, "role", "") not in PAYROLL_MANAGER_ROLES:
            return Response({"detail": "only HR or Finance can issue an advance or loan"}, status=403)
        emp = Employee.objects.filter(pk=request.data.get("employee")).first()
        if not emp:
            return Response({"detail": "unknown employee"}, status=400)
        kind = request.data.get("kind")
        if kind not in (SalaryAdvance.ADVANCE, SalaryAdvance.LOAN):
            return Response({"detail": "kind must be advance or loan"}, status=400)
        try:
            amount = Decimal(str(request.data.get("amount") or 0))
        except ArithmeticError:
            return Response({"detail": "amount must be a number"}, status=400)
        if amount <= 0:
            return Response({"detail": "amount must be positive"}, status=400)
        installment = Decimal("0")
        if kind == SalaryAdvance.LOAN:
            try:
                installment = Decimal(str(request.data.get("monthly_installment") or 0))
            except ArithmeticError:
                return Response({"detail": "monthly_installment must be a number"}, status=400)
            if installment <= 0:
                return Response({"detail": "a loan needs a positive monthly installment"}, status=400)
        a = SalaryAdvance.objects.create(
            employee=emp, kind=kind, amount=amount, monthly_installment=installment,
            note=(request.data.get("note") or "").strip(), issued_by=request.user.username)
        log_action(request.user, "advance_issued", entity="SalaryAdvance", entity_id=a.id,
                   after={"employee": emp.name, "kind": kind, "amount": str(amount)})
        return Response(_advance_dict(a), status=201)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        """Write off whatever's left — an unrecoverable loan, or a manager's
        call to forgive it. Stops it being planned into future payroll."""
        from apps.accounts.constants import PAYROLL_MANAGER_ROLES
        if getattr(request.user, "role", "") not in PAYROLL_MANAGER_ROLES:
            return Response({"detail": "only HR or Finance can write off a balance"}, status=403)
        a = SalaryAdvance.objects.filter(pk=pk).first()
        if not a:
            return Response({"detail": "not found"}, status=404)
        if a.status != SalaryAdvance.ACTIVE:
            return Response({"detail": "this is already settled"}, status=400)
        a.status = SalaryAdvance.SETTLED
        a.note = (a.note + " " if a.note else "") + f"[waived by {request.user.username}]"
        a.save(update_fields=["status", "note"])
        log_action(request.user, "advance_waived", entity="SalaryAdvance", entity_id=a.id,
                   after={"employee": a.employee.name, "balance_written_off": str(a.balance)})
        return Response(_advance_dict(a))


class LeaveViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    """Leave desk (FR-HRM leave management) — shared service like matreq:
    every role reaches it for their own applications; approvals route to the
    employee's department manager (see leave_approvers_for)."""

    module = "leave"

    def _own_employee(self, request):
        return getattr(request.user, "employee_record", None)

    def list(self, request):
        """?view=mine (default) → my applications + any I filed on behalf.
        ?view=queue → requests awaiting MY sign-off at whichever level:
        pending ones for the departments I manage, manager-approved ones if
        I give the final HR sign-off (universal roles get both).
        ?view=all → full oversight (HR / CEO / Super Admin / MD / GM)."""
        from apps.accounts.constants import (
            LEAVE_FINAL_APPROVERS,
            LEAVE_OVERSIGHT_ROLES,
            leave_approvers_for,
        )
        role = getattr(request.user, "role", "")
        view = request.query_params.get("view", "mine")
        qs = LeaveRequest.objects.select_related("employee", "leave_type")
        if view == "all" and role in LEAVE_OVERSIGHT_ROLES:
            pass
        elif view == "queue":
            qs = qs.filter(status__in=[LeaveRequest.PENDING, LeaveRequest.MANAGER_APPROVED])
            qs = [r for r in qs
                  if (r.status == LeaveRequest.PENDING
                      and role in leave_approvers_for(r.employee.department))
                  or (r.status == LeaveRequest.MANAGER_APPROVED
                      and role in LEAVE_FINAL_APPROVERS)]
            return Response([_leave_dict(r) for r in qs])
        else:
            emp = self._own_employee(request)
            from django.db.models import Q
            cond = Q(requested_by=request.user.username)
            if emp:
                cond |= Q(employee=emp)
            qs = qs.filter(cond)
        return Response([_leave_dict(r) for r in qs])

    @action(detail=False, methods=["get"])
    def staff(self, request):
        """Employee picklist for on-behalf entry — scoped to the departments
        this role may file for (HR everywhere, a manager for the departments
        they approve). Most of these roles don't have the full 'hr' module,
        so this can't just proxy to /hr/ — same pattern as matreq/materials."""
        from apps.accounts.constants import can_enter_leave_on_behalf
        role = getattr(request.user, "role", "")
        rows = [e for e in Employee.objects.filter(status="Active")
                if can_enter_leave_on_behalf(role, e.department)]
        return Response([{"id": e.id, "name": e.name, "department": e.department,
                          "role": e.role, "has_login": bool(e.user_id)} for e in rows])

    @action(detail=False, methods=["get"])
    def types(self, request):
        """Active leave types for the apply form; managers get inactive too."""
        from apps.accounts.constants import LEAVE_TYPE_MANAGER_ROLES
        qs = LeaveType.objects.all()
        if getattr(request.user, "role", "") not in LEAVE_TYPE_MANAGER_ROLES:
            qs = qs.filter(active=True)
        return Response([_type_dict(t) for t in qs])

    @action(detail=False, methods=["post"])
    def save_type(self, request):
        """Create/update a leave type: {id?, name, annual_quota, is_paid,
        carry_forward, active}. HR/Admin/GM/MD/Super Admin only."""
        from apps.accounts.constants import LEAVE_TYPE_MANAGER_ROLES
        if getattr(request.user, "role", "") not in LEAVE_TYPE_MANAGER_ROLES:
            return Response({"detail": "only HR or an administrator can manage leave types"}, status=403)
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "name is required"}, status=400)
        try:
            quota = int(request.data.get("annual_quota") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "annual_quota must be a number of days"}, status=400)
        if quota < 0:
            return Response({"detail": "annual_quota cannot be negative"}, status=400)
        fields = {
            "name": name, "annual_quota": quota,
            "is_paid": bool(request.data.get("is_paid", True)),
            "carry_forward": bool(request.data.get("carry_forward", False)),
            "active": bool(request.data.get("active", True)),
        }
        type_id = request.data.get("id")
        if type_id:
            t = LeaveType.objects.filter(pk=type_id).first()
            if not t:
                return Response({"detail": "not found"}, status=404)
            if LeaveType.objects.exclude(pk=t.pk).filter(name__iexact=name).exists():
                return Response({"detail": "a leave type with that name already exists"}, status=400)
            for k, v in fields.items():
                setattr(t, k, v)
            t.save()
        else:
            if LeaveType.objects.filter(name__iexact=name).exists():
                return Response({"detail": "a leave type with that name already exists"}, status=400)
            t = LeaveType.objects.create(**fields)
        log_action(request.user, "leave_type_save", entity="LeaveType", entity_id=t.id, after=fields)
        return Response(_type_dict(t))

    @action(detail=False, methods=["get"])
    def balances(self, request):
        """Leave balances: ?employee=<id> (default: my own record), ?year=."""
        from apps.accounts.constants import LEAVE_OVERSIGHT_ROLES, leave_approvers_for
        year = int(request.query_params.get("year") or timezone.localdate().year)
        emp_id = request.query_params.get("employee")
        own = self._own_employee(request)
        if emp_id and (not own or int(emp_id) != own.id):
            emp = Employee.objects.filter(pk=emp_id).first()
            if not emp:
                return Response({"detail": "not found"}, status=404)
            role = getattr(request.user, "role", "")
            if role not in LEAVE_OVERSIGHT_ROLES and role not in leave_approvers_for(emp.department):
                return Response({"detail": "you can only view your own leave balance"}, status=403)
        else:
            emp = own
            if not emp:
                return Response({"employee": None, "year": year, "balances": [],
                                 "detail": "no staff record is linked to your login — ask HR to link one"})
        return Response({"employee": emp.id, "employee_name": emp.name, "year": year,
                         "balances": _leave_balances(emp, year)})

    def create(self, request):
        """Apply for leave: {leave_type, start_date, end_date, reason?,
        employee?}. `employee` is only for HR / the department's manager
        filing on behalf of staff without a login."""
        from apps.accounts.constants import can_enter_leave_on_behalf
        own = self._own_employee(request)
        emp_id = request.data.get("employee")
        if emp_id and (not own or int(emp_id) != own.id):
            emp = Employee.objects.filter(pk=emp_id).first()
            if not emp:
                return Response({"detail": "unknown employee"}, status=400)
            if not can_enter_leave_on_behalf(getattr(request.user, "role", ""), emp.department):
                return Response({"detail": "only HR or this department's manager can apply on behalf of staff"},
                                status=403)
        else:
            emp = own
            if not emp:
                return Response({"detail": "no staff record is linked to your login — ask HR to link one"},
                                status=400)
        try:
            lt_id = int(request.data.get("leave_type"))
        except (TypeError, ValueError):
            # A non-numeric id crashed with a 500 here (QA finding TC-095).
            return Response({"detail": "pick a leave type"}, status=400)
        lt = LeaveType.objects.filter(pk=lt_id, active=True).first()
        if not lt:
            return Response({"detail": "pick a leave type"}, status=400)
        try:
            start = date.fromisoformat(str(request.data.get("start_date")))
            end = date.fromisoformat(str(request.data.get("end_date")))
        except (TypeError, ValueError):
            return Response({"detail": "valid start and end dates are required"}, status=400)
        if end < start:
            return Response({"detail": "end date is before start date"}, status=400)
        if start.year != end.year:
            return Response({"detail": "a request cannot span calendar years — file one per year"}, status=400)
        days = (end - start).days + 1
        overlap = LeaveRequest.objects.filter(
            employee=emp, status__in=LeaveRequest.ACTIVE_STATUSES,
            start_date__lte=end, end_date__gte=start,
        ).first()
        if overlap:
            return Response({"detail": f"{emp.name} already has {overlap.leave_type.name} "
                                       f"{overlap.start_date}→{overlap.end_date} ({overlap.status}) over those dates"},
                            status=400)
        if lt.annual_quota:
            committed = sum(r.days for r in LeaveRequest.objects.filter(
                employee=emp, leave_type=lt, start_date__year=start.year,
                status__in=LeaveRequest.ACTIVE_STATUSES))
            if committed + days > lt.annual_quota:
                left = lt.annual_quota - committed
                return Response({"detail": f"not enough {lt.name} balance — {max(left, 0)} of "
                                           f"{lt.annual_quota} day(s) left for {start.year}"},
                                status=400)
        r = LeaveRequest.objects.create(
            employee=emp, leave_type=lt, start_date=start, end_date=end,
            days=days, reason=(request.data.get("reason") or "").strip(),
            requested_by=request.user.username,
        )
        log_action(request.user, "leave_requested", entity="LeaveRequest", entity_id=r.id,
                   after={"employee": emp.name, "type": lt.name, "days": days})
        return Response(_leave_dict(r), status=201)

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        """Approve or reject: {decision: approve|reject, note?}. Two levels:
        a pending request is decided by the employee's department approvers,
        a manager-approved one by HR (final). GM/MD/Super Admin can act at
        either level; never on your own request. Only the FINAL approval
        writes the attendance marks that feed payroll: paid leave counts as
        a payable day, unpaid (LOP) marks absent."""
        from apps.accounts.constants import LEAVE_FINAL_APPROVERS, leave_approvers_for
        r = LeaveRequest.objects.select_related("employee", "leave_type").filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=404)
        if r.status not in (LeaveRequest.PENDING, LeaveRequest.MANAGER_APPROVED):
            return Response({"detail": f"this request is already {r.status}"}, status=400)
        role = getattr(request.user, "role", "")
        if r.status == LeaveRequest.PENDING:
            approvers = leave_approvers_for(r.employee.department)
            if role not in approvers:
                return Response({"detail": f"a {' or '.join(sorted(approvers))} must decide "
                                           f"{r.employee.department} leave first"}, status=403)
        else:
            if role not in LEAVE_FINAL_APPROVERS:
                return Response({"detail": "the manager has approved — HR gives the final sign-off"},
                                status=403)
        own = self._own_employee(request)
        if r.requested_by == request.user.username or (own and own.id == r.employee_id):
            return Response({"detail": "this is your own request — someone else must decide it"}, status=403)
        decision = request.data.get("decision")
        if decision not in ("approve", "reject"):
            return Response({"detail": "decision must be approve or reject"}, status=400)
        note = (request.data.get("note") or "").strip()
        with transaction.atomic():
            if decision == "reject":
                r.status = LeaveRequest.REJECTED
                r.decided_by = request.user.username
                r.decided_at = timezone.now()
                r.decision_note = note
                r.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
            elif r.status == LeaveRequest.PENDING:
                # Level 1 — the department manager's sign-off; on to HR.
                r.status = LeaveRequest.MANAGER_APPROVED
                r.manager_decided_by = request.user.username
                r.manager_decided_at = timezone.now()
                if note:
                    r.decision_note = note
                r.save(update_fields=["status", "manager_decided_by", "manager_decided_at",
                                      "decision_note"])
            else:
                # Level 2 — HR's final approval puts it on the record.
                r.status = LeaveRequest.APPROVED
                r.decided_by = request.user.username
                r.decided_at = timezone.now()
                if note:
                    r.decision_note = note
                r.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
                mark = Attendance.LEAVE if r.leave_type.is_paid else Attendance.ABSENT
                for i in range(r.days):
                    Attendance.objects.update_or_create(
                        employee=r.employee, date=r.start_date + timedelta(days=i),
                        defaults={"status": mark, "marked_by": f"leave:{r.id}"})
        log_action(request.user, f"leave_{r.status}", entity="LeaveRequest", entity_id=r.id,
                   after={"employee": r.employee.name, "days": r.days})
        return Response(_leave_dict(r))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Withdraw a request. Still in the pipeline (pending or manager-
        approved): the requester/employee themselves (or HR/an approver).
        Fully approved: approvers or HR only — cancelling reverts exactly
        the attendance marks the final approval wrote. The employee can
        never cancel their own APPROVED leave; and CEO, oversight-only
        everywhere, can see requests but not cancel them either."""
        from apps.accounts.constants import LEAVE_FINAL_APPROVERS, leave_approvers_for
        r = LeaveRequest.objects.select_related("employee", "leave_type").filter(pk=pk).first()
        if not r:
            return Response({"detail": "not found"}, status=404)
        if r.status not in LeaveRequest.ACTIVE_STATUSES:
            return Response({"detail": f"this request is already {r.status}"}, status=400)
        role = getattr(request.user, "role", "")
        own = self._own_employee(request)
        # Everyone with cancel authority at either approval level — the
        # department's approvers plus HR/universal. Deliberately NOT
        # LEAVE_OVERSIGHT_ROLES: that set includes the visibility-only CEO.
        is_manager = role in LEAVE_FINAL_APPROVERS or role in leave_approvers_for(r.employee.department)
        is_mine = r.requested_by == request.user.username or (own and own.id == r.employee_id)
        if r.status != LeaveRequest.APPROVED and not (is_mine or is_manager):
            return Response({"detail": "only the requester (or a manager) can withdraw this"}, status=403)
        if r.status == LeaveRequest.APPROVED and not is_manager:
            return Response({"detail": "approved leave can only be cancelled by the approver or HR"}, status=403)
        with transaction.atomic():
            if r.status == LeaveRequest.APPROVED:
                Attendance.objects.filter(employee=r.employee, marked_by=f"leave:{r.id}").delete()
            r.status = LeaveRequest.CANCELLED
            r.decided_by = request.user.username
            r.decided_at = timezone.now()
            r.save(update_fields=["status", "decided_by", "decided_at"])
        log_action(request.user, "leave_cancelled", entity="LeaveRequest", entity_id=r.id)
        return Response(_leave_dict(r))


class InvitePublicView(APIView):
    """Self-onboarding: the new hire opens HR's link, sees who/what role
    they're being invited as, and sets their own username + password.
    Token-credentialed like Feedback/QR-order, no login, throttled."""

    permission_classes = [AllowAny]
    throttle_scope = "sensitive"

    def get(self, request):
        inv = Invite.objects.select_related("employee").filter(
            token=request.query_params.get("t", "")).first()
        if not inv or not inv.is_valid():
            return Response({"detail": "This invite link is invalid or has expired"}, status=404)
        return Response({
            "employee_name": inv.employee.name,
            "role": inv.role,
            "branch_name": inv.employee.branch.name if inv.employee.branch_id else None,
        })

    def post(self, request):
        inv = Invite.objects.select_related("employee").filter(
            token=request.data.get("t", "")).first()
        if not inv or not inv.is_valid():
            return Response({"detail": "This invite link is invalid or has expired"}, status=404)
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username:
            return Response({"detail": "Choose a username"}, status=400)
        if User.objects.filter(username=username).exists():
            return Response({"detail": "That username is already taken"}, status=400)
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(password)
        except DjangoValidationError as e:
            return Response({"detail": " ".join(e.messages)}, status=400)

        first_name, _, last_name = inv.employee.name.partition(" ")
        with transaction.atomic():
            user = User(username=username, first_name=first_name, last_name=last_name,
                        role=inv.role, is_active=True)
            user.set_password(password)
            user.save()
            if inv.employee.branch_id:
                UserBranchAccess.objects.create(
                    user=user, branch=inv.employee.branch, role=inv.role)
            inv.employee.user = user
            inv.employee.save(update_fields=["user"])
            inv.used_at = timezone.now()
            inv.save(update_fields=["used_at"])
        log_action(user, "invite_completed", entity="User", entity_id=user.id,
                   after={"role": inv.role, "employee": inv.employee_id})
        return Response({"username": username}, status=201)
