"""Bring phone numbers already in the database to the canonical shape.

Until validate_phone landed, User.phone, Property.phone and Customer.mobile
accepted any string at all, so the same number could be stored as "9876543210",
"98765 43210" or "+91-98765-43210" and none of them matched the others. This
strips separators so stored values compare equal.

What it deliberately does NOT do is invent a country code. A bare ten-digit
number does not say which country it belongs to, and guessing turns a guest
record into an undialable one. Existing numbers keep whatever they were entered
as; new ones get a code from the picker (Property.default_country_code).

Customer.mobile is unique, so normalising can collide two rows that were only
ever distinct because of their spacing. Those are left untouched rather than
raising mid-deploy — merging two guest records is a decision for a human, and
the duplicate-acceptance sweep reports them.
"""
from django.db import migrations


def normalise(apps, schema_editor):
    from apps.accounts.validators import normalize_phone

    for label, model_name, field in (
        ("accounts", "User", "phone"),
        ("accounts", "Property", "phone"),
        ("crm", "Customer", "mobile"),
        ("hr", "Employee", "phone"),
    ):
        model = apps.get_model(label, model_name)
        unique = field == "mobile"
        for row in model.objects.exclude(**{field: ""}).iterator():
            current = getattr(row, field) or ""
            cleaned = normalize_phone(current)
            if cleaned == current:
                continue
            if unique and model.objects.filter(**{field: cleaned}).exclude(pk=row.pk).exists():
                # Two records that differed only in spacing. Leaving both as
                # they are keeps the deploy green and the collision visible.
                continue
            setattr(row, field, cleaned)
            row.save(update_fields=[field])


class Migration(migrations.Migration):
    """Reverse is a no-op: the original spacing carried no information, so there
    is nothing to restore. Rolling back leaves the tidied values in place."""

    dependencies = [
        ("accounts", "0029_property_default_country_code"),
        ("crm", "0004_seed_base_loyalty_tier"),
        ("hr", "0011_employee_country_code_employee_weekly_rate_and_more"),
    ]

    operations = [
        migrations.RunPython(normalise, migrations.RunPython.noop),
    ]
