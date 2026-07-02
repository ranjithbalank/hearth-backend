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
python manage.py seed_demo    # demo property, menu, users
python manage.py runserver 8010
```

Demo logins (password `hearth123`): `md`, `gm`, `frontoffice`, `cashier`, `captain`, `housekeeping`.

- SQLite by default; set `DATABASE_URL` for PostgreSQL (see `hearth/settings/`).
- Set a real `SECRET_KEY` in production (`hearth/settings/prod.py`).
- Tests: `python manage.py test apps`
