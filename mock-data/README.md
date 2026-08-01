# Mock data — bulk-import samples

Fully-populated sample files for every **bulk-importable** entity in Hearth, so a
fresh install (see `seed_demo --boilerplate`) can be filled with one round of
imports, and so the Import/Export features can be tested end-to-end.

Each CSV in `imports/` matches an app Import screen exactly — same column order,
same valid values — and every column is filled.

| File | Import screen / endpoint | Rows |
|---|---|---|
| `branches.csv` | Branch Master · `POST /api/auth/branches/import/` | 3 |
| `rooms.csv` | Room Master · `POST /api/rooms/import/` | 8 |
| `tables.csv` | Table Master · `POST /api/pos/tables/import/` | 7 |
| `bar-tables.csv` | Bar Table Master · `POST /api/bar/tables/import/` | 5 |
| `menu-categories.csv` | Menu Master · `POST /api/pos/categories/import/` | 8 |
| `menu-items.csv` | Menu Master · `POST /api/pos/menu-items/import/` | 10 |
| `ingredients.csv` | Inventory · `POST /api/inventory/import/` | 8 |
| `suppliers.csv` | Suppliers · `POST /api/suppliers/import/` | 5 |
| `employees.csv` | Employees · `POST /api/hr/import/` | 5 |

Reference masters the importers rely on — kitchen stations (`kitchen`, `bar`),
departments, designations, units, GST slabs — are seeded by migrations, so they
exist on any migrated DB. Room types and menu categories are auto-created from
the import rows; departments/designations are **not** (they must already exist,
which they do).

## Use it

**In the app:** open each Import card and upload the matching CSV. Suggested
order so branch-scoped rows resolve: branches → rooms → tables → bar tables →
menu categories → menu items → ingredients → suppliers → employees.

**Regenerate the files** (edit the datasets in `generate.py` first):

```bash
backend/.venv/Scripts/python.exe backend/mock-data/generate.py
```

**Test every import through the real API** (runs against a throwaway DB; the
dev/demo DBs are never touched):

```bash
backend/.venv/Scripts/python.exe backend/mock-data/test_imports.py
# -> PASS for all 9, "ALL IMPORTS SUCCEEDED"
```
