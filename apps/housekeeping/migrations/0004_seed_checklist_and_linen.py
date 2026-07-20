from django.db import migrations

CHECKLIST_ITEMS = [
    "Strip and remake bed",
    "Clean bathroom",
    "Restock amenities",
    "Vacuum / mop floor",
    "Empty trash bins",
    "Check AC, lights and TV",
]

LINEN_ITEMS = [
    ("Bath towel", 2),
    ("Hand towel", 1),
    ("Bedsheet set", 1),
    ("Pillow cover", 2),
]


def seed(apps, schema_editor):
    ChecklistItem = apps.get_model("housekeeping", "ChecklistItem")
    LinenItem = apps.get_model("housekeeping", "LinenItem")
    for i, label in enumerate(CHECKLIST_ITEMS):
        ChecklistItem.objects.get_or_create(label=label, defaults={"sort_order": i})
    for name, par in LINEN_ITEMS:
        LinenItem.objects.get_or_create(name=name, defaults={"par_per_room": par})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("housekeeping", "0003_checklistitem_linenitem_housekeepingtask"),
    ]

    operations = [
        migrations.RunPython(seed, noop),
    ]
