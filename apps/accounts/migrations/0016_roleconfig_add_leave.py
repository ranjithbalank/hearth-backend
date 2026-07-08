from django.db import migrations


def add_leave_module(apps, schema_editor):
    """Saved Role Matrix rows override ROLE_ALLOW wholesale, so a property
    that customised its matrix before this release would silently lose the
    new shared 'leave' module. Append it to every stored allow-list — same
    universal-access rule as matreq/notifications."""
    RoleConfig = apps.get_model("accounts", "RoleConfig")
    for cfg in RoleConfig.objects.all():
        if isinstance(cfg.modules, list) and "leave" not in cfg.modules:
            cfg.modules.append("leave")
            cfg.save(update_fields=["modules"])


def remove_leave_module(apps, schema_editor):
    RoleConfig = apps.get_model("accounts", "RoleConfig")
    for cfg in RoleConfig.objects.all():
        if isinstance(cfg.modules, list) and "leave" in cfg.modules:
            cfg.modules.remove("leave")
            cfg.save(update_fields=["modules"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_branch_userbranchaccess"),
    ]

    operations = [
        migrations.RunPython(add_leave_module, remove_leave_module),
    ]
