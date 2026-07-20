# Seed the two stations that already exist implicitly today, using the exact
# same lowercase strings already stored on every MenuItem row so nothing
# needs to change there — plus any other station string already in use, so
# no existing menu item is orphaned from validation on day one.
from django.db import migrations


def seed(apps, schema_editor):
    KitchenStation = apps.get_model("masters", "KitchenStation")
    MenuItem = apps.get_model("pos", "MenuItem")

    KitchenStation.objects.get_or_create(
        name="kitchen", defaults={"mode": "kds", "is_bar": False})
    KitchenStation.objects.get_or_create(
        name="bar", defaults={"mode": "kds", "is_bar": True})

    in_use = set(MenuItem.objects.values_list("station", flat=True))
    for name in sorted(in_use):
        if name and name.strip():
            KitchenStation.objects.get_or_create(
                name=name.strip(), defaults={"mode": "kds", "is_bar": name.strip().lower() == "bar"})


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0003_kitchenstation"),
        ("pos", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
