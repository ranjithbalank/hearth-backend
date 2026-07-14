import calendar
from datetime import date
from decimal import Decimal, InvalidOperation

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import log_action
from apps.accounts.permissions import AnyModuleViewSetMixin

from .models import Attendance, Employee


def _employee_dict(e):
    return {"id": e.id, "name": e.name, "department": e.department, "role": e.role,
            "phone": e.phone, "shifts": e.shifts, "status": e.status,
            "monthly_salary": str(e.monthly_salary)}


class HrViewSet(AnyModuleViewSetMixin, viewsets.ViewSet):
    # Serves two desks: the HR module proper (attendance, payroll) and the
    # Employees master screen, which Admin reaches via "employees" without
    # holding the full "hr" module.
    modules = ["hr", "employees"]

    def list(self, request):
        return Response([_employee_dict(e) for e in Employee.objects.all()])

    def create(self, request):
        """Add a staff record. Department and designation must be active rows
        in the masters (Settings > Masters) — same pattern as Ingredient.unit
        against the UoM master."""
        from apps.masters.models import Department, Designation
        name = (request.data.get("name") or "").strip()
        department = (request.data.get("department") or "").strip()
        role = (request.data.get("role") or "").strip()
        if not (name and department and role):
            return Response({"detail": "name, department and designation are required"}, status=400)
        if not Department.objects.filter(name=department, active=True).exists():
            return Response({"detail": f"'{department}' is not an active department"}, status=400)
        if not Designation.objects.filter(name=role, active=True).exists():
            return Response({"detail": f"'{role}' is not an active designation"}, status=400)
        try:
            salary = Decimal(str(request.data.get("monthly_salary") or 0))
        except InvalidOperation:
            return Response({"detail": "invalid monthly salary"}, status=400)
        e = Employee.objects.create(
            name=name, department=department, role=role,
            phone=(request.data.get("phone") or "").strip(), monthly_salary=salary)
        log_action(request.user, "employee_created", entity="Employee", entity_id=e.id,
                   after={"name": name, "department": department, "role": role})
        return Response(_employee_dict(e), status=201)

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
