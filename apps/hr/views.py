import calendar
from datetime import date
from decimal import Decimal

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import ModuleViewSetMixin, resolve_active_branch, shared_or_visible

from .models import Attendance, Employee


def _employee_dict(e):
    return {
        "id": e.id, "name": e.name, "department": e.department, "role": e.role,
        "phone": e.phone, "shifts": e.shifts, "status": e.status,
        "monthly_salary": str(e.monthly_salary),
        "branch": e.branch_id, "branch_name": e.branch.name if e.branch_id else None,
        "user": e.user_id,
    }


class HrViewSet(ModuleViewSetMixin, viewsets.ViewSet):
    module = "hr"

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
        name = (request.data.get("name") or "").strip()
        department = (request.data.get("department") or "").strip()
        role = (request.data.get("role") or "").strip()
        if not name or not department or not role:
            return Response({"detail": "name, department and role are required"}, status=400)
        branch_id = request.data.get("branch") or resolve_active_branch(request)
        e = Employee.objects.create(
            name=name, department=department, role=role,
            phone=request.data.get("phone", ""),
            monthly_salary=request.data.get("monthly_salary") or 0,
            branch_id=branch_id,
        )
        log_action(request.user, "employee_add", entity="Employee", entity_id=e.id,
                   after={"name": name, "department": department, "branch": branch_id})
        return Response(_employee_dict(e), status=201)

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

    @action(detail=False, methods=["get"])
    def payroll(self, request):
        """Monthly payroll from attendance: payable = salary × payable_days / month_days.

        present/leave = 1 day, half = 0.5, absent/unmarked = 0.
        """
        from django.utils import timezone
        month = request.query_params.get("month") or timezone.localdate().strftime("%Y-%m")
        year, mon = int(month[:4]), int(month[5:7])
        days_in_month = calendar.monthrange(year, mon)[1]
        first, last = date(year, mon, 1), date(year, mon, days_in_month)
        weights = {Attendance.PRESENT: Decimal("1"), Attendance.LEAVE: Decimal("1"),
                   Attendance.HALF: Decimal("0.5"), Attendance.ABSENT: Decimal("0")}
        rows = []
        for e in Employee.objects.filter(status="Active"):
            marks = Attendance.objects.filter(employee=e, date__range=(first, last))
            payable_days = sum(weights.get(a.status, Decimal("0")) for a in marks)
            payable = ((e.monthly_salary or Decimal("0")) * payable_days
                       / Decimal(days_in_month)).quantize(Decimal("0.01"))
            rows.append({
                "id": e.id, "name": e.name, "department": e.department, "role": e.role,
                "monthly_salary": str(e.monthly_salary),
                "days_marked": marks.count(),
                "payable_days": str(payable_days),
                "payable": str(payable),
            })
        total = sum(Decimal(r["payable"]) for r in rows)
        return Response({"month": month, "days_in_month": days_in_month,
                         "rows": rows, "total_payable": str(total)})
