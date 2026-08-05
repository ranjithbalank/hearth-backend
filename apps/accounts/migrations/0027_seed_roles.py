"""Seed the Role Master from the built-in constants.

Three things happen here, all of them lossless:

1. The seventeen built-ins become `is_system` rows carrying their ROLE_ALLOW
   mapping and ROLE_RANK seniority. Super Admin / MD / GM store "*".
2. Any RoleConfig override the property had already made is folded into the
   matching row's modules, so an edited mapping survives the switch. RoleConfig
   itself is left in place (nothing reads it any more) until the next release.
3. Role names still sitting on user accounts that aren't built-ins — a
   "Night Auditor" from before that role was folded into Front Office — are
   recorded as INACTIVE custom rows with no base and no modules. That is
   exactly the access they have today (allowed_modules_for returned [] for an
   unknown name), the difference being that the owner can now see them in Role
   Master and move those people onto a current role.
"""
from django.db import migrations

from apps.accounts.constants import ROLE_ALLOW, ROLE_CHOICES, ROLE_RANK


def seed(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    RoleConfig = apps.get_model("accounts", "RoleConfig")
    User = apps.get_model("accounts", "User")

    overrides = {c.role: c.modules for c in RoleConfig.objects.all()}
    for name, _ in ROLE_CHOICES:
        allow = ROLE_ALLOW.get(name, [])
        modules = "*" if allow == "*" else list(overrides.get(name, allow))
        Role.objects.update_or_create(
            name=name,
            defaults={"base_role": "", "rank": ROLE_RANK.get(name, 1),
                      "modules": modules, "is_system": True, "active": True},
        )

    builtin = {name for name, _ in ROLE_CHOICES}
    retired = set(User.objects.exclude(role__in=builtin)
                  .values_list("role", flat=True).distinct()) - {""}
    for name in retired:
        Role.objects.update_or_create(
            name=name,
            defaults={"base_role": "", "rank": 0, "modules": [], "is_system": False,
                      "active": False,
                      "description": "Retired role, kept so existing accounts still resolve."},
        )


def unseed(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [("accounts", "0026_role")]

    operations = [migrations.RunPython(seed, unseed)]
