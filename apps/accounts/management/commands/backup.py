"""Database backup (BRD NFR-010): dump app data to a timestamped JSON fixture.

    python manage.py backup                 # -> backups/hearth-YYYYmmdd-HHMMSS.json
    python manage.py backup --out path.json

Restore with the companion `restore` command (or `manage.py loaddata <file>`).
For PostgreSQL in production prefer `pg_dump`; this is a portable app-level dump.
"""
from datetime import datetime
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

# Transient/framework tables we never want in a data backup.
EXCLUDE = ["contenttypes", "sessions", "admin.logentry", "auth.permission"]


class Command(BaseCommand):
    help = "Back up application data to a JSON fixture (NFR-010)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="")

    def handle(self, *args, **opts):
        out = opts["out"]
        if not out:
            Path("backups").mkdir(exist_ok=True)
            out = f"backups/hearth-{datetime.now():%Y%m%d-%H%M%S}.json"
        with open(out, "w", encoding="utf-8") as f:
            call_command("dumpdata", exclude=EXCLUDE, natural_foreign=True,
                         indent=2, stdout=f)
        size = Path(out).stat().st_size
        self.stdout.write(self.style.SUCCESS(f"Backup written: {out} ({size} bytes)"))
