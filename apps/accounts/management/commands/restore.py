"""Restore application data from a backup fixture (BRD NFR-010).

    python manage.py restore backups/hearth-YYYYmmdd-HHMMSS.json
"""
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restore application data from a JSON backup fixture (NFR-010)."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **opts):
        path = opts["path"]
        if not Path(path).exists():
            raise CommandError(f"Backup not found: {path}")
        call_command("loaddata", path)
        self.stdout.write(self.style.SUCCESS(f"Restored from {path}"))
