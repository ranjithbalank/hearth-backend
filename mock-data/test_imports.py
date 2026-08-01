"""Import every mock-data/imports/*.csv through the real API and report the
result per entity. Runs against a throwaway SQLite DB (migrated from scratch,
so it has the reference masters — kitchen stations, departments, designations,
units, GST, payment methods — the importers rely on). The dev/demo DBs are
never touched.

    backend/.venv/Scripts/python.exe backend/mock-data/test_imports.py
"""
import os
import sys
import tempfile

import django

# Point at a throwaway DB *before* Django reads settings.
_TMP = tempfile.mktemp(suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.replace(os.sep, '/')}"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hearth.settings.dev")

# Make the backend importable regardless of the cwd (this file lives in
# backend/mock-data/, so the backend package root is the parent directory).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
django.setup()

from django.conf import settings as S  # noqa: E402
if "testserver" not in S.ALLOWED_HOSTS:
    S.ALLOWED_HOSTS.append("testserver")

from django.core.management import call_command  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from apps.accounts.models import User  # noqa: E402

IMPORTS = os.path.join(HERE, "imports")

# (file, endpoint) — order matters: branches first so branch-scoped rows resolve.
TARGETS = [
    ("branches.csv", "/api/auth/branches/import/"),
    ("rooms.csv", "/api/rooms/import/"),
    ("tables.csv", "/api/pos/tables/import/"),
    ("bar-tables.csv", "/api/bar/tables/import/"),
    ("menu-categories.csv", "/api/pos/categories/import/"),
    ("menu-items.csv", "/api/pos/menu-items/import/"),
    ("ingredients.csv", "/api/inventory/import/"),
    ("suppliers.csv", "/api/suppliers/import/"),
    ("employees.csv", "/api/hr/import/"),
]


def main():
    call_command("migrate", verbosity=0)
    admin = User.objects.create_superuser("root", "root@example.com", "irrelevant")
    admin.role = "Super Admin"
    admin.save(update_fields=["role"])
    client = APIClient()
    client.force_authenticate(admin)

    all_ok = True
    print(f"{'RESULT':6} {'FILE':22} {'ENDPOINT':30} STATUS")
    print("-" * 90)
    for fname, url in TARGETS:
        with open(os.path.join(IMPORTS, fname), "rb") as fh:
            resp = client.post(url, {"file": fh}, format="multipart")
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.content[:160].decode("utf-8", "replace")}
        created = data.get("created")
        errors = data.get("errors") or []
        skipped = data.get("skipped_existing") or []
        ok = resp.status_code == 200 and bool(created) and not errors
        all_ok = all_ok and ok
        detail = f"created={created} skipped={len(skipped)} errors={len(errors)}"
        if errors:
            detail += f" | first: {errors[0]}"
        if resp.status_code != 200:
            detail = f"HTTP {resp.status_code} {data.get('detail', data)}"
        print(f"{'PASS' if ok else 'FAIL':6} {fname:22} {url:30} {detail}")

    print("-" * 90)
    print("ALL IMPORTS SUCCEEDED" if all_ok else "SOME IMPORTS FAILED")
    try:
        os.remove(_TMP)
    except OSError:
        pass
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
