# Seed the masters so day one looks like the app always did:
#  - the six classic departments (plus any names already living on employee
#    or indent rows, so nothing in history is orphaned from the dropdowns)
#  - a starter set of hotel/restaurant designations (plus any in use)
#  - the three builtin tenders with today's hardwired behavior as flags
from django.db import migrations

DEPARTMENTS = ["Kitchen", "Bar", "Housekeeping", "Banquets", "Front Office", "Maintenance"]

DESIGNATIONS = [
    "Manager", "Chef", "Cook", "Steward", "Waiter", "Bartender",
    "Housekeeping Attendant", "Receptionist", "Cashier", "Storekeeper",
    "Maintenance Technician", "Security",
]

TENDERS = [
    # name, counts_as_cash, captain_allowed
    ("Cash", True, False),
    ("UPI", False, True),
    ("Gateway", False, True),
]


def seed(apps, schema_editor):
    Department = apps.get_model("masters", "Department")
    Designation = apps.get_model("masters", "Designation")
    PaymentMethod = apps.get_model("masters", "PaymentMethod")
    Employee = apps.get_model("hr", "Employee")
    MaterialRequest = apps.get_model("matreq", "MaterialRequest")

    in_use_departments = set(Employee.objects.values_list("department", flat=True)) | set(
        MaterialRequest.objects.values_list("department", flat=True))
    for name in [*DEPARTMENTS, *sorted(in_use_departments)]:
        if name and name.strip():
            Department.objects.get_or_create(name=name.strip())

    in_use_designations = set(Employee.objects.values_list("role", flat=True))
    for name in [*DESIGNATIONS, *sorted(in_use_designations)]:
        if name and name.strip():
            Designation.objects.get_or_create(name=name.strip())

    for name, cash, captain in TENDERS:
        PaymentMethod.objects.get_or_create(
            name=name,
            defaults={"counts_as_cash": cash, "captain_allowed": captain, "builtin": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0001_initial"),
        ("hr", "0001_initial"),
        ("matreq", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
