from django.db import migrations

# Spec §1/§6 defaults, mirroring the previous hardcoded choice lists.
UNITS = [
    ("kg", "Kilogram (KG)"), ("g", "Gram (GM)"), ("l", "Litre (L)"),
    ("ml", "Millilitre (ML)"), ("pc", "Piece (Nos)"), ("pkt", "Packet"),
]
CATEGORIES = [
    "Vegetables", "Meat / Seafood", "Dairy", "Spices", "Beverages",
    "Packaging Materials", "Cleaning Materials", "Other Consumables",
    "Prepared",  # auto-created by production batches ("Prep: <item>")
]


def seed(apps, schema_editor):
    Uom = apps.get_model("inventory", "Uom")
    IngredientCategory = apps.get_model("inventory", "IngredientCategory")
    Ingredient = apps.get_model("inventory", "Ingredient")

    for code, name in UNITS:
        Uom.objects.get_or_create(code=code, defaults={"name": name})
    for name in CATEGORIES:
        IngredientCategory.objects.get_or_create(name=name)

    # Adopt any values already in use so existing rows stay valid.
    for unit in Ingredient.objects.exclude(unit="").values_list("unit", flat=True).distinct():
        Uom.objects.get_or_create(code=unit, defaults={"name": unit})
    for cat in Ingredient.objects.exclude(category="").values_list("category", flat=True).distinct():
        IngredientCategory.objects.get_or_create(name=cat)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_ingredientcategory_uom_alter_ingredient_unit"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
