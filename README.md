# Hearth — Backend

Django 5 + DRF backend for **Hearth**, a Hotel & Restaurant OS (PMS, POS + KOT rounds,
KDS, inventory/recipes with auto consumption, banquets, RMS, CRM, night audit).

Pairs with the [hearth-frontend](https://github.com/ranjithbalank/hearth-frontend) React app.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows (source .venv/bin/activate on unix)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo    # full showcase demo (see modes below)
python manage.py runserver 8010
```

### Seeding: demo vs boilerplate

`seed_demo` has two modes. It only ever *adds* data (get-or-create, never
deletes), so to stand up a clean instance point it at a **fresh** database.

| Command | What you get |
|---|---|
| `python manage.py seed_demo` | **Full showcase demo** — a furnished property: rooms, menu, guests, bookings, bills, night audits. Use this to demo Hearth. |
| `python manage.py seed_demo --boilerplate` | **Clean template** — the property shell, role logins and reference masters only, with no rooms, menu, guests or transactions. `setup_done=False`, so a new customer lands in the Setup wizard and builds their own. |

Demo logins (password `hearth123`): `md`, `gm`, `frontoffice`, `cashier`, `captain`, `housekeeping`, `hr`,
`hotelmanager`, `restmanager`.

- SQLite by default; set `DATABASE_URL` for PostgreSQL (see `hearth/settings/`).
- Set a real `SECRET_KEY` in production (`hearth/settings/prod.py`).
- Tests: `python manage.py test apps`
