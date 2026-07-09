from django.db import migrations


def backfill_floor(apps, schema_editor):
    Table = apps.get_model("pos", "Table")
    Table.objects.filter(floor="").update(floor="Ground")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0022_table_floor"),
    ]

    operations = [
        migrations.RunPython(backfill_floor, noop),
    ]
