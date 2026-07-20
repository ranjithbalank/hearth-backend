from django.db import migrations


def seed_base_tier(apps, schema_editor):
    LoyaltyTier = apps.get_model("crm", "LoyaltyTier")
    LoyaltyTier.objects.get_or_create(
        name="Base", defaults={"min_lifetime_points": 0, "earn_multiplier": 1})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0003_loyaltyreward_loyaltytier_customer_anniversary_date_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_base_tier, noop),
    ]
